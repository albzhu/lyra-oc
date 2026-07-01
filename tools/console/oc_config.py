"""
oc_config.py — shared, UI-agnostic core for OpenClaw config editing.

Pure logic only: no dialogs, no HTTP. Raises exceptions on error so any UI
(the osascript tool, the web console) can present them however it likes.

Editing target is ALWAYS openclaw-template.json — `make sync`/`cold-restart`
regenerates openclaw.json from it, so direct edits to openclaw.json are
transient. Writes use stage -> validate -> backup -> atomic replace.

Agents are addressed by a string key:
  - "__defaults__"  -> agents.defaults
  - "<id>"          -> the matching agents.list[] entry
"""

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

OPENCLAW_DIR = Path(os.path.expanduser("~/.openclaw"))
TEMPLATE = OPENCLAW_DIR / "openclaw-template.json"
STAGED = OPENCLAW_DIR / "openclaw-template_staged.json"
BACKUP_DIR = OPENCLAW_DIR / "openclaw-backups"

# Observability / reporting paths
ENV_FILE = OPENCLAW_DIR / ".env"
LOG_DIR = OPENCLAW_DIR / "logs"
OBS_DIR = OPENCLAW_DIR / "workspace" / "observability" / "daily"
TOKEN_TRACKER = OPENCLAW_DIR / "workspace" / "scripts" / "token_tracker.py"

DEFAULTS_KEY = "__defaults__"
ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# --------------------------------------------------------------------------- #
# jq discovery
# --------------------------------------------------------------------------- #
def find_jq():
    for c in (shutil.which("jq"), "/opt/homebrew/bin/jq",
              "/usr/local/bin/jq", "/usr/bin/jq"):
        if c and Path(c).exists():
            return c
    return None


JQ = find_jq()


# --------------------------------------------------------------------------- #
# Load / save
# --------------------------------------------------------------------------- #
def load_config():
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE}")
    return json.loads(TEMPLATE.read_text())  # raises ValueError on bad JSON


def save_config(data):
    """Stage -> validate -> backup -> atomic replace. Returns backup path or None."""
    new_text = json.dumps(data, indent=2) + "\n"
    STAGED.write_text(new_text)
    json.loads(STAGED.read_text())  # prove it parses before committing
    backup = None
    if TEMPLATE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        backup = BACKUP_DIR / f"openclaw-template.json.uiedit.{ts}"
        shutil.copy2(TEMPLATE, backup)
    os.replace(STAGED, TEMPLATE)
    return backup


def cold_restart():
    """Run `make cold-restart` (sync down up, no log-follow). Returns CompletedProcess."""
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    return subprocess.run(
        ["make", "cold-restart"], cwd=str(OPENCLAW_DIR),
        env=env, capture_output=True, text=True,
    )


# --------------------------------------------------------------------------- #
# jq-based listing (in-memory data piped over stdin)
# --------------------------------------------------------------------------- #
def jq_get(data, filt):
    if not JQ:
        raise RuntimeError("jq not found (brew install jq)")
    proc = subprocess.run([JQ, "-r", filt], input=json.dumps(data),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def _base(aid):
    if aid == DEFAULTS_KEY:
        return ".agents.defaults"
    return f'(.agents.list[] | select(.id=="{aid}"))'


def get_primary(data, aid):
    return jq_get(data, f"{_base(aid)}.model.primary // empty").strip()


def get_fallbacks(data, aid):
    out = jq_get(data, f"{_base(aid)}.model.fallbacks[]? // empty")
    return [l for l in out.splitlines() if l.strip()]


def alias_map(data, aid):
    out = jq_get(
        data,
        f'{_base(aid)}.models // {{}} | to_entries[] | "\\(.key)\\t\\(.value.alias // "")"',
    )
    m = {}
    for line in out.splitlines():
        if "\t" in line:
            k, a = line.split("\t", 1)
            m[k] = a
    return m


def candidate_models(data, aid):
    """Union of this agent's declared models + every model referenced anywhere."""
    seen = set(alias_map(data, aid).keys())
    everywhere = jq_get(
        data,
        '[.. | objects | (.model? // empty) | (.primary?, .fallbacks?)] '
        '| flatten | map(select(. != null)) | unique[]',
    )
    for line in everywhere.splitlines():
        if line.strip():
            seen.add(line.strip())
    return sorted(seen)


# --------------------------------------------------------------------------- #
# In-memory node access + mutation
# --------------------------------------------------------------------------- #
def agent_node(data, aid):
    if aid == DEFAULTS_KEY:
        return data["agents"]["defaults"]
    for e in data["agents"]["list"]:
        if e.get("id") == aid:
            return e
    return None


def agent_keys(data):
    """Ordered list of addressable agents: defaults first, then each list id."""
    keys = [DEFAULTS_KEY]
    keys += [e["id"] for e in data.get("agents", {}).get("list", []) if e.get("id")]
    return keys


def set_primary(data, aid, model_id):
    agent_node(data, aid).setdefault("model", {})["primary"] = model_id


def set_fallbacks(data, aid, fallbacks):
    agent_node(data, aid).setdefault("model", {})["fallbacks"] = list(fallbacks)


def set_alias(data, aid, model_id, alias):
    """Set models[model_id].alias. Empty alias is a no-op (won't clobber)."""
    if not alias:
        return
    agent_node(data, aid).setdefault("models", {}).setdefault(model_id, {})["alias"] = alias


def has_alias(data, aid, model_id):
    return bool(alias_map(data, aid).get(model_id))


# --------------------------------------------------------------------------- #
# Bindings
# --------------------------------------------------------------------------- #
def bindings_for(data, aid):
    return [b for b in data.get("bindings", []) if b.get("agentId") == aid]


# --------------------------------------------------------------------------- #
# Agent CRUD (pure; raise ValueError on bad input)
# --------------------------------------------------------------------------- #
def validate_new_id(data, new_id):
    if not new_id or not ID_RE.match(new_id):
        raise ValueError("Invalid id. Use letters, numbers, dot, dash, underscore.")
    if new_id in {a.get("id") for a in data["agents"]["list"]}:
        raise ValueError(f"An agent with id '{new_id}' already exists.")


def create_agent(data, new_id, name=None, workspace=None, scaffold=True):
    """Append a new minimal agent. Returns (entry, scaffold_result|None)."""
    validate_new_id(data, new_id)
    name = name or new_id
    workspace = workspace or str(OPENCLAW_DIR / f"workspace-{new_id}")
    default_primary = data["agents"]["defaults"].get("model", {}).get("primary", "")
    entry = {
        "id": new_id,
        "model": {"primary": default_primary, "fallbacks": []},
        "models": {},
        "name": name,
        "workspace": workspace,
    }
    data["agents"]["list"].append(entry)
    result = scaffold_workspace(workspace, new_id, name) if scaffold else None
    return entry, result


def remove_agent(data, aid):
    """Remove agent + its bindings. Returns list of removed bindings."""
    affected = bindings_for(data, aid)
    data["agents"]["list"] = [a for a in data["agents"]["list"] if a.get("id") != aid]
    if affected:
        data["bindings"] = [b for b in data.get("bindings", []) if b.get("agentId") != aid]
    return affected


def rename_agent(data, aid, new_id):
    """Rename id; repoint bindings + agentDir. Returns count of repointed bindings."""
    validate_new_id(data, new_id)
    entry = agent_node(data, aid)
    if entry is None:
        raise ValueError(f"No agent '{aid}'.")
    entry["id"] = new_id
    if entry.get("agentDir") and f"/agents/{aid}/" in entry["agentDir"]:
        entry["agentDir"] = entry["agentDir"].replace(f"/agents/{aid}/", f"/agents/{new_id}/")
    n = 0
    for b in data.get("bindings", []):
        if b.get("agentId") == aid:
            b["agentId"] = new_id
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Workspace scaffolding (starter persona files)
# --------------------------------------------------------------------------- #
PERSONA_TEMPLATES = {
    "IDENTITY.md": """# IDENTITY.md — Who Am I?

- **Name:** «NAME»
- **Agent id:** «ID»
- **Creature:** _(describe this agent's archetype)_
- **Vibe:** _(tone and personality in one line)_
- **Emoji:**

---

_Write a short paragraph on what «NAME» is for and how it fits alongside the
other agents in this gateway._
""",
    "SOUL.md": """# SOUL.md — «NAME»

_You are «NAME». Describe your purpose and the principles you operate by._

## Core Truths

- **Be genuinely helpful** — skip filler, get to the point.
- **Be resourceful before asking** — try first, ask when truly blocked.
- **Be cost-conscious** — without compromising safety or quality.
- **STOP & notify immediately if any credentials, API keys, or tokens are
  discovered.** Safety overrides completion.
""",
    "AGENTS.md": """# AGENTS.md — «NAME»

_Define this agent's job: what it does, what it must not do, and how it reports
results._

## Rules
- Be specific and evidence-based.
- State assumptions; flag uncertainty ("likely", "verify").
- End with a clear, one-line outcome.

## 🔁 Callback Protocol — Don't Block, Yield
When you delegate work you'll need to act on, don't sit and wait — package a
callback, dispatch, and end your turn. Full spec: `Skills/miab-broker/CALLBACKS.md` (or `~/.openclaw/workspace/Skills/miab-broker/CALLBACKS.md`).

CLI: `python3 Skills/miab-broker/scripts/bin/claw-callback.py <cmd>`
""",
    "TOOLS.md": """- Skills default dir is ~/.openclaw/workspace/skills. Investigate any skill
  listed outside it.
- This is the **«ID»** agent workspace. Operate on the target repo/files passed
  to you, not on personal files here.
""",
    "USER.md": """# USER.md — About Your Human

_Learn about the person you're helping. Update this as you go._

- **Name:**
- **What to call them:**
- **Pronouns:** _(optional)_
- **Timezone:**
- **Notes:**

## Context

_(What do they care about? What are they working on? Build this over time.)_
""",
    "HEARTBEAT.md": """<!-- Heartbeat template; comments-only content prevents scheduled heartbeat API calls. -->

# Keep this file empty (or with only comments) to skip heartbeat API calls.
# Add tasks below when you want the agent to check something periodically.
""",
    ".gitignore": "*.DS_Store\n.env\n",
}


def scaffold_workspace(ws_path, agent_id, name):
    """Create workspace dir + write any missing persona files. Returns (created, skipped)."""
    ws = Path(ws_path).expanduser()
    ws.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for fname, template in PERSONA_TEMPLATES.items():
        dest = ws / fname
        if dest.exists():
            skipped.append(fname)
            continue
        dest.write_text(template.replace("«NAME»", name).replace("«ID»", agent_id))
        created.append(fname)
    return created, skipped


# --------------------------------------------------------------------------- #
# Observability — token reports
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(r"token_report_(\d{4}-\d{2}-\d{2})\.md$")


def token_report_dates():
    """Available daily token-report dates, newest first."""
    if not OBS_DIR.exists():
        return []
    dates = []
    for p in OBS_DIR.glob("token_report_*.md"):
        m = _DATE_RE.search(p.name)
        if m:
            dates.append(m.group(1))
    return sorted(dates, reverse=True)


def read_token_report(date):
    p = OBS_DIR / f"token_report_{date}.md"
    return p.read_text() if p.exists() else None


def _num(s):
    return int(s.replace(",", "")) if s else 0


def parse_token_report(text):
    """Parse a token_report markdown into structured data (tolerant of gaps)."""
    out = {"summary": {}, "models": [], "agents": []}
    if not text:
        return out
    g = out["summary"]
    m = re.search(r"Total Completed Turns:\*\*\s*([\d,]+)", text)
    if m: g["turns"] = _num(m.group(1))
    m = re.search(r"Total Est\. Cost:\*\*\s*\$([\d.]+)", text)
    if m: g["cost"] = float(m.group(1))
    m = re.search(r"Total Token Consumption:\*\*\s*([\d,]+)", text)
    if m: g["total"] = _num(m.group(1))
    for key, label in (("input", "Input"), ("output", "Output"),
                       ("cache", "Cache Read"), ("reasoning", "Reasoning")):
        m = re.search(rf"{label}:\s*([\d,]+)", text)
        if m: g[key] = _num(m.group(1))
    # Model breakdown: "- **<id>**: 7 turns | 667,004 tokens | $2.0578"
    for m in re.finditer(r"-\s*\*\*(.+?)\*\*:\s*([\d,]+)\s*turns?\s*\|\s*([\d,]+)\s*tokens?\s*\|\s*\$([\d.]+)", text):
        out["models"].append({"id": m.group(1), "turns": _num(m.group(2)),
                              "tokens": _num(m.group(3)), "cost": float(m.group(4))})
    # Agent breakdown blocks: a bold NAME line followed by Turns/Total Tokens + Cost
    for m in re.finditer(
        r"\*\*([A-Z0-9_-]+)\*\*\s*\n-\s*Turns:\s*([\d,]+)\s*\|\s*Total Tokens:\s*([\d,]+).*?\n(?:.*\n)?-\s*Cost:\s*\$([\d.]+)",
        text):
        out["agents"].append({"name": m.group(1), "turns": _num(m.group(2)),
                              "tokens": _num(m.group(3)), "cost": float(m.group(4))})
    return out


def regenerate_token_report(date=None):
    """Run token_tracker.py for the date and write workspace/observability/daily/.
    Returns (date, path, CompletedProcess). Raises if the tracker is missing."""
    if not TOKEN_TRACKER.exists():
        raise FileNotFoundError(f"token_tracker.py not found at {TOKEN_TRACKER}")
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    proc = subprocess.run(
        ["python3", str(TOKEN_TRACKER), "--date", date],
        capture_output=True, text=True,
    )
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    path = OBS_DIR / f"token_report_{date}.md"
    if proc.returncode == 0 and proc.stdout.strip():
        path.write_text(proc.stdout)
    return date, path, proc


# --------------------------------------------------------------------------- #
# Observability — log scan (errors + restarts)
# --------------------------------------------------------------------------- #
_ERR_RE = re.compile(
    r"failed|error|invalid|ETIMEDOUT|api key not valid|rate.?limit|quota|\b429\b|\b5[0-9][0-9]\b",
    re.I)
_IGNORE_RE = re.compile(r"liveness warning|disabled by default|plugin disabled", re.I)


def _tail_lines(path, max_bytes=200_000):
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        return f.read().decode("utf-8", "replace").splitlines()


def scan_logs(max_errors=12, max_restarts=6):
    errors = []
    for fname in ("gateway.err.log", "gateway.log"):
        for line in _tail_lines(LOG_DIR / fname):
            if _ERR_RE.search(line) and not _IGNORE_RE.search(line):
                errors.append(line.strip()[:300])
    errors = errors[-max_errors:]
    restarts = []
    for line in _tail_lines(LOG_DIR / "gateway-restart.log"):
        if "restart done" in line or "restart attempt" in line:
            restarts.append(line.strip())
    restarts = restarts[-max_restarts:]
    return {"errors": errors, "restarts": restarts}


# --------------------------------------------------------------------------- #
# Observability — live API health probes (independent of the cron health-check,
# so it never disturbs that job's offset-tracked log-scan state)
# --------------------------------------------------------------------------- #
def _read_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                v = v.strip().strip('"').strip("'")
                env[k.strip()] = v
    return env


def _http_code(url, headers=None, method="GET", timeout=10):
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
           "--max-time", str(timeout), "-X", method]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        return p.stdout.strip() or "000"
    except Exception:
        return "000"


def health_probes():
    """Live reachability/auth checks. Returns [{name, ok, code, hint}]. Secrets
    are used in headers only; never returned or logged."""
    e = _read_env()
    checks = [
        ("OpenRouter", "https://openrouter.ai/api/v1/key",
         {"Authorization": f"Bearer {e.get('OPENROUTER_API_KEY','')}"}, "Check OPENROUTER_API_KEY"),
        ("Gemini API", f"https://generativelanguage.googleapis.com/v1beta/models?key={e.get('GEMINI_API_KEY','')}",
         {}, "Rotate GEMINI_API_KEY then `make sync`"),
        ("Google Web Search", f"https://generativelanguage.googleapis.com/v1beta/models?key={e.get('GOOGLE_WEB_SEARCH_API_KEY','')}",
         {}, "Rotate GOOGLE_WEB_SEARCH_API_KEY then `make sync`"),
        ("OpenAI", "https://api.openai.com/v1/models",
         {"Authorization": f"Bearer {e.get('OPENAI_API_KEY','')}"}, "Check OPENAI_API_KEY"),
        ("Discord Bot", "https://discord.com/api/v10/users/@me",
         {"Authorization": f"Bot {e.get('DISCORD_BOT_TOKEN','')}"}, "Check DISCORD_BOT_TOKEN"),
    ]
    results = []
    for name, url, headers, hint in checks:
        code = _http_code(url, headers)
        ok = code == "200"
        results.append({"name": name, "ok": ok, "code": code, "hint": "" if ok else hint})
    return results


# --------------------------------------------------------------------------- #
# Observability — combined report + scheduling
# --------------------------------------------------------------------------- #
def generate_observability(date=None, run_probes=True):
    """Write a combined observability_<date>.md (token summary + health + logs)."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    tok = parse_token_report(read_token_report(date) or "")
    logs = scan_logs()
    probes = health_probes() if run_probes else []
    g = tok["summary"]
    lines = [f"# OpenClaw Observability — {date}", ""]
    lines.append("## Token usage")
    if g:
        lines.append(f"- Turns: {g.get('turns','?')} | Est. cost: ${g.get('cost','?')} "
                     f"| Total tokens: {g.get('total','?'):,}" if isinstance(g.get('total'), int)
                     else f"- Turns: {g.get('turns','?')} | Est. cost: ${g.get('cost','?')}")
        for mdl in tok["models"][:8]:
            lines.append(f"  - {mdl['id']}: {mdl['turns']} turns | {mdl['tokens']:,} tokens | ${mdl['cost']:.4f}")
    else:
        lines.append("- No token report for this date.")
    lines += ["", "## Health probes"]
    if probes:
        for p in probes:
            lines.append(f"- {'PASS' if p['ok'] else 'FAIL'} {p['name']} ({p['code']})"
                         + (f" — {p['hint']}" if p['hint'] else ""))
    else:
        lines.append("- (skipped)")
    lines += ["", "## Recent restarts"]
    lines += [f"- {r}" for r in logs["restarts"]] or ["- none"]
    lines += ["", "## Recent errors"]
    lines += [f"- {x}" for x in logs["errors"]] or ["- none"]
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    path = OBS_DIR / f"observability_{date}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def schedule_command(hhmm="07:30"):
    """Build the native `openclaw cron add` command for a daily observability run."""
    hh, mm = hhmm.split(":")
    cron = f"{int(mm)} {int(hh)} * * *"
    event = ("Run the daily observability report: execute "
             "`python3 ~/.openclaw/tools/console/observability_report.py` "
             "and post a one-line summary if any health probe failed.")
    return [
        "openclaw", "cron", "add",
        "--name", "Daily Observability Report",
        "--description", "Combined token usage + health + log scan",
        "--cron", cron,
        "--system-event", event,
    ]


def run_schedule(hhmm="07:30"):
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    return subprocess.run(schedule_command(hhmm), cwd=str(OPENCLAW_DIR),
                          env=env, capture_output=True, text=True)


def reports_overview(date=None):
    """Token summary for a date (latest if None) + dates list + log scan. No probes."""
    dates = token_report_dates()
    if not date:
        date = dates[0] if dates else None
    tok = parse_token_report(read_token_report(date) or "") if date else {"summary": {}, "models": [], "agents": []}
    return {"date": date, "dates": dates, "token": tok, "logs": scan_logs()}


# --------------------------------------------------------------------------- #
# Terminal — direct LLM "gateway configuration assistant" (via OpenRouter)
# --------------------------------------------------------------------------- #
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CHAT_SYSTEM = """You are the OpenClaw Gateway Configuration Assistant.
You help the user understand and configure OpenClaw gateways: providers, models
(primary/fallbacks/aliases), agents, channel bindings, plugins, and the
openclaw-template.json schema. Be concise and technical.

Key facts to honor:
- Config edits go in openclaw-template.json, never openclaw.json (which is
  regenerated from the template by `make sync` / `make cold-restart`).
- Secrets are referenced as "env:VAR_NAME" and live in ~/.openclaw/.env.
- When proposing changes, show minimal JSON snippets for openclaw-template.json.

Keep answers short — this is a low-volume helper, not a long conversation.

Current configuration summary:
{summary}
"""


def _or_model(model_id):
    """Map a config-style model id to an OpenRouter model id."""
    return model_id[len("openrouter/"):] if model_id.startswith("openrouter/") else model_id


def chat_default_model(data):
    prim = data["agents"]["defaults"].get("model", {}).get("primary", "")
    if prim.startswith("openrouter/") or "/" in prim:
        return prim
    return "openrouter/google/gemini-3.5-flash"


def config_summary(data):
    """Compact, secret-free summary of the live config for chat context."""
    agents = []
    for aid in agent_keys(data):
        node = agent_node(data, aid)
        agents.append({
            "id": "defaults" if aid == DEFAULTS_KEY else aid,
            "primary": get_primary(data, aid),
            "fallbacks": get_fallbacks(data, aid),
            "aliases": list(alias_map(data, aid).keys()),
        })
    providers = list(data.get("models", {}).get("providers", {}).keys())
    plugins = data.get("plugins", {}).get("allow", [])
    channels = list(data.get("channels", {}).keys())
    return json.dumps({"agents": agents, "providers": providers,
                       "plugins": plugins, "channels": channels}, indent=1)


def chat_stream(model, messages, data, timeout=90):
    """Yield assistant text deltas from OpenRouter. Raises RuntimeError on setup
    failure (missing key, bad status) before any text is yielded."""
    import urllib.request
    import urllib.error

    key = _read_env().get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not found in ~/.openclaw/.env")

    system = CHAT_SYSTEM.format(summary=config_summary(data))
    full = [{"role": "system", "content": system}] + messages
    body = json.dumps({"model": _or_model(model), "messages": full,
                       "stream": True, "max_tokens": 1200}).encode()
    req = urllib.request.Request(
        OPENROUTER_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Title": "OpenClaw Console"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {detail}")
    except Exception as e:
        raise RuntimeError(f"OpenRouter request failed: {e}")

    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            delta = json.loads(payload)["choices"][0]["delta"].get("content")
        except (ValueError, KeyError, IndexError):
            continue
        if delta:
            yield delta


# --------------------------------------------------------------------------- #
# Snapshot for a UI
# --------------------------------------------------------------------------- #
def snapshot(data):
    """Everything a UI needs to render the Models + Agents views."""
    out = {"agents": [], "candidates": candidate_models(data, DEFAULTS_KEY)}
    for aid in agent_keys(data):
        node = agent_node(data, aid)
        out["agents"].append({
            "key": aid,
            "id": aid if aid != DEFAULTS_KEY else "defaults",
            "is_defaults": aid == DEFAULTS_KEY,
            "name": node.get("name", "") if aid != DEFAULTS_KEY else "(global defaults)",
            "workspace": node.get("workspace", ""),
            "primary": get_primary(data, aid),
            "fallbacks": get_fallbacks(data, aid),
            "aliases": alias_map(data, aid),
            "bindings": len(bindings_for(data, aid)),
        })
    return out
