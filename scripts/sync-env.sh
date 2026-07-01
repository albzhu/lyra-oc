#!/usr/bin/env python3
"""Generates ~/.openclaw/openclaw.json from ~/.openclaw/openclaw-template.json by injecting secrets from ~/.openclaw/.env.
Also syncs key env pairs into service-env/ai.openclaw.gateway.env.
"""
import sys, json, re
from datetime import datetime
from pathlib import Path

openclaw_dir = Path(__file__).parent.parent
env_file = openclaw_dir / ".env"
service_env = openclaw_dir / "service-env" / "ai.openclaw.gateway.env"
template_file = openclaw_dir / "openclaw-template.json"
config_file = openclaw_dir / "openclaw.json"
backup_dir = openclaw_dir / "openclaw-backups"

if not env_file.exists():
    print(f"error: {env_file} not found", file=sys.stderr)
    sys.exit(1)

if not template_file.exists():
    print(f"error: {template_file} not found", file=sys.stderr)
    sys.exit(1)

# Parse .env into a dict
env_vars = {}
for line in env_file.read_text().splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if "=" in stripped:
        key, _, value = stripped.partition("=")
        # Remove quotes if present around values
        val = value.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        env_vars[key.strip()] = val

# --- Generate openclaw.json from template ---
try:
    template_content = template_file.read_text()
except Exception as e:
    print(f"error reading template: {e}", file=sys.stderr)
    sys.exit(1)

def inject_env_values(data, variables):
    """Recursively walks JSON structure replacing "env:VAR_NAME" strings with values from .env.
    """
    if isinstance(data, dict):
        return {k: inject_env_values(v, variables) for k, v in data.items()}
    elif isinstance(data, list):
        return [inject_env_values(item, variables) for item in data]
    elif isinstance(data, str):
        # Whole-string secret stub: "env:VAR" -> the secret value.
        if data.startswith("env:"):
            key = data[4:]
            if key in variables:
                return variables[key]
            print(f"warning: Env variable '{key}' referenced in template but not found in .env. Keeping placeholder.")
            return data
        # Embedded interpolation: "${VAR}/sub/path" -> value + "/sub/path".
        # Used for things like ${OPENCLAW_WORKSPACE_ROOT}. Unknown ${VAR} are
        # left intact so unrelated literals are never mangled.
        def _interp(match):
            name = match.group(1)
            if name in variables:
                return variables[name]
            print(f"warning: Env variable '{name}' referenced as ${{{name}}} in template but not found in .env. Keeping placeholder.")
            return match.group(0)
        return re.sub(r"\$\{(\w+)\}", _interp, data)
    return data


def _is_stub(v):
    """A template leaf that resolves to a secret at sync time (e.g. "env:OPENAI_API_KEY")."""
    return isinstance(v, str) and v.startswith("env:")


def diff_config(template, current, path=""):
    """Directionally compare the template (source of truth, may hold env: stubs)
    against the current openclaw.json. Returns {extra, missing, changed} lists of
    (path, template_value, current_value):

      extra   - path present in openclaw.json but NOT in the template. This is the
                drift `openclaw update` (or a manual edit) introduces, and sync would
                silently ERASE it. BLOCKING.
      missing - path present in the template but not yet in openclaw.json. Normal
                template-edit push; sync will add it. Informational.
      changed - same path, differing non-stub value. Template is authoritative and
                will overwrite; could be a pending edit or update-driven. Informational.

    Paths whose template value is an env: stub are ignored entirely.
    """
    extra, missing, changed = [], [], []

    def _merge(sub):
        extra.extend(sub["extra"]); missing.extend(sub["missing"]); changed.extend(sub["changed"])

    if isinstance(template, dict) and isinstance(current, dict):
        for k in current:
            p = f"{path}.{k}" if path else k
            if k not in template:
                extra.append((p, None, current[k]))
            else:
                _merge(diff_config(template[k], current[k], p))
        for k in template:
            if k not in current and not _is_stub(template[k]):
                p = f"{path}.{k}" if path else k
                missing.append((p, template[k], None))
        return {"extra": extra, "missing": missing, "changed": changed}

    if isinstance(template, list) and isinstance(current, list):
        for i in range(max(len(template), len(current))):
            p = f"{path}[{i}]"
            if i >= len(template):
                extra.append((p, None, current[i]))
            elif i >= len(current):
                if not _is_stub(template[i]):
                    missing.append((p, template[i], None))
            else:
                _merge(diff_config(template[i], current[i], p))
        return {"extra": extra, "missing": missing, "changed": changed}

    # scalar leaf or a dict/list-vs-scalar type mismatch
    if _is_stub(template):
        return {"extra": extra, "missing": missing, "changed": changed}
    if template != current:
        changed.append((path, template, current))
    return {"extra": extra, "missing": missing, "changed": changed}


def _fmt(v):
    s = v if isinstance(v, str) else json.dumps(v)
    return s if len(s) <= 120 else s[:117] + "..."


try:
    config_json = json.loads(template_content)

    # --- Drift guard ---------------------------------------------------------
    # Before overwriting openclaw.json, make sure it hasn't gained configuration
    # the template doesn't know about (e.g. `openclaw update` mutating it in place).
    # Such drift would be erased by the regeneration below, so abort and report it.
    if config_file.exists():
        try:
            current_json = json.loads(config_file.read_text())
        except Exception as e:
            print(f"error: could not parse existing {config_file.name} for drift check: {e}", file=sys.stderr)
            sys.exit(1)
        d = diff_config(config_json, current_json)
        if d["extra"]:
            print("error: drift detected — openclaw.json contains configuration not present in", file=sys.stderr)
            print("       openclaw-template.json. This usually means `openclaw update` (or a manual", file=sys.stderr)
            print("       edit) changed openclaw.json directly. Port these into the template, then", file=sys.stderr)
            print("       re-run `make sync`. Sync aborted — nothing was written.", file=sys.stderr)
            print("", file=sys.stderr)
            print("  Only in openclaw.json (would be ERASED by sync):", file=sys.stderr)
            for p, _, cur in d["extra"]:
                print(f"    + {p} = {_fmt(cur)}", file=sys.stderr)
            if d["changed"]:
                print("", file=sys.stderr)
                print("  Value differences (template would overwrite — review if any are update-driven):", file=sys.stderr)
                for p, tv, cv in d["changed"]:
                    print(f"    ~ {p}: template={_fmt(tv)} | openclaw.json={_fmt(cv)}", file=sys.stderr)
            sys.exit(1)
        if d["changed"] or d["missing"]:
            n = len(d["changed"]) + len(d["missing"])
            print(f"  drift check passed (applying {n} template-side change(s): "
                  f"{len(d['changed'])} updated, {len(d['missing'])} added).")
        else:
            print("  drift check passed (openclaw.json matches template).")

    resolved_config = inject_env_values(config_json, env_vars)
    new_text = json.dumps(resolved_config, indent=2) + "\n"
    # Guard: back up the existing openclaw.json into openclaw-backups/ before
    # overwriting it, but only when the regenerated content actually differs.
    if config_file.exists():
        old_text = config_file.read_text()
        if old_text != new_text:
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            backup_path = backup_dir / f"openclaw.json.synced.{ts}"
            backup_path.write_text(old_text)
            print(f"  backed up previous openclaw.json -> {backup_path.relative_to(openclaw_dir)}")
    config_file.write_text(new_text)
    print("  openclaw.json generated successfully from template.")
except Exception as e:
    print(f"error parsing or generating openclaw.json: {e}", file=sys.stderr)
    sys.exit(1)

# --- Sync service-env ---
if service_env.exists():
    lines = service_env.read_text().splitlines()
    svc_updated = 0
    new_lines = []

    for line in lines:
        replaced = False
        for key, value in env_vars.items():
            if line.startswith(f"export {key}="):
                new_lines.append(f"export {key}='{value}'")
                print(f"  service-env updated: {key}")
                svc_updated += 1
                replaced = True
                break
        if not replaced:
            new_lines.append(line)

    service_env.write_text("\n".join(new_lines) + "\n")
    print(f"sync complete: service-env ({svc_updated} variables synced)")
else:
    print(f"warning: service-env file {service_env} not found. Skipping service-env sync.")

# --- Sync GEMINI_API_KEY into ~/.gemini/settings.json ---
gemini_api_key = env_vars.get("GEMINI_API_KEY")
if gemini_api_key:
    gemini_settings_file = Path.home() / ".gemini" / "settings.json"
    gemini_settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings = {}
    if gemini_settings_file.exists():
        try:
            settings = json.loads(gemini_settings_file.read_text())
        except Exception:
            print(f"warning: could not parse {gemini_settings_file}, overwriting.")
    settings["geminiApiKey"] = gemini_api_key
    gemini_settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  ~/.gemini/settings.json updated with GEMINI_API_KEY.")
else:
    print("warning: GEMINI_API_KEY not found in .env. Skipping ~/.gemini/settings.json sync.")
