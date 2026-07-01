#!/usr/bin/env bash
# OpenClaw gateway container entrypoint.
#
# Responsibilities:
#   1. Sanity-check the bind-mounted state dir + required secrets.
#   2. Regenerate openclaw.json from openclaw-template.json (+ .env), exactly like
#      `make sync` does on the host. Idempotent and safe to run every boot.
#   3. Neutralize macOS launchd/service-supervisor leftovers so the foreground
#      gateway never tries to talk to a service manager that isn't here.
#   4. exec the gateway in the FOREGROUND (`openclaw gateway run`) as PID 1's child
#      so signals/log streaming work under Docker.
set -euo pipefail

# Defaults to $HOME/.openclaw; HOME is /Users/albertzhu in the image so absolute
# workspace/agentDir paths baked into openclaw.json resolve unchanged.
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
TEMPLATE="$STATE_DIR/openclaw-template.json"
CONFIG="$STATE_DIR/openclaw.json"
SYNC_SCRIPT="$STATE_DIR/scripts/sync-env.sh"

log() { printf '[entrypoint] %s\n' "$*"; }

# --- 1. Preconditions -------------------------------------------------------
if [ ! -d "$STATE_DIR" ]; then
  log "FATAL: state dir $STATE_DIR is missing. Did you bind-mount ~/.openclaw?"
  exit 1
fi
cd "$STATE_DIR"

# openclaw reads secrets from the process environment (injected via env_file/.env).
# Mirror GATEWAY_AUTH_TOKEN onto the name the CLI's --token default looks for, so
# token auth works whether read from config or env.
if [ -n "${GATEWAY_AUTH_TOKEN:-}" ] && [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
  export OPENCLAW_GATEWAY_TOKEN="$GATEWAY_AUTH_TOKEN"
fi

# --- 2. Regenerate openclaw.json from template ------------------------------
# sync-env.sh prefers $STATE_DIR/.env. If you inject secrets purely via env_file
# (no .env file on the volume), skip regeneration and trust the committed config.
if [ -f "$SYNC_SCRIPT" ] && [ -f "$TEMPLATE" ] && [ -f "$STATE_DIR/.env" ]; then
  log "regenerating openclaw.json from template via sync-env.sh"
  python3 "$SYNC_SCRIPT" || { log "FATAL: sync-env.sh failed"; exit 1; }
elif [ -f "$CONFIG" ]; then
  log "no .env on volume; using existing openclaw.json as-is"
else
  log "FATAL: no openclaw.json and cannot generate one (need .env + template + sync-env.sh)"
  exit 1
fi

# --- 2b. Fail fast if HOME doesn't match the absolute paths in the config ----
# openclaw.json hard-codes absolute workspace/agentDir paths (e.g.
# /Users/albertzhu/.openclaw/workspace-sigma). If the container's HOME doesn't
# match the home prefix in those paths, agents would load empty/missing
# workspaces instead of failing loudly. Catch that here with a clear message
# instead of a confusing half-broken gateway.
log "checking config paths resolve under HOME=$HOME"
HOME="$HOME" CONFIG="$CONFIG" python3 - <<'PY' || exit 1
import json, os, sys

home = os.environ["HOME"].rstrip("/")
cfg_path = os.environ["CONFIG"]
try:
    cfg = json.load(open(cfg_path))
except Exception as e:
    print(f"[entrypoint] FATAL: cannot read {cfg_path}: {e}", file=sys.stderr)
    sys.exit(1)

# Collect every absolute path the gateway will try to open for agents.
paths = []
agents = cfg.get("agents", {})
d = agents.get("defaults", {})
if d.get("workspace"):
    paths.append(("defaults.workspace", d["workspace"]))
for a in agents.get("list", []):
    aid = a.get("id", "?")
    for key in ("workspace", "agentDir"):
        v = a.get(key)
        if isinstance(v, str) and v:
            paths.append((f"{aid}.{key}", v))

# A path is a mismatch if it's an absolute /Users|/home style home path whose
# home prefix (/<x>/<y>) differs from the container's HOME.
expected_prefix = home + "/"
mismatches = []
for label, p in paths:
    if not p.startswith("/"):
        continue  # relative paths resolve under cwd; not our concern
    if p.startswith(("/Users/", "/home/", "/root")) and not p.startswith(expected_prefix):
        # derive the home root referenced by the config path: first two segments
        seg = p.split("/")
        ref_home = "/".join(seg[:3]) if len(seg) >= 3 else p
        mismatches.append((label, p, ref_home))

if mismatches:
    refs = sorted({m[2] for m in mismatches})
    print("[entrypoint] FATAL: config paths don't match the container's HOME.", file=sys.stderr)
    print(f"[entrypoint]   HOME (container) = {home}", file=sys.stderr)
    print(f"[entrypoint]   config references = {', '.join(refs)}", file=sys.stderr)
    for label, p, _ in mismatches[:8]:
        print(f"[entrypoint]     - {label}: {p}", file=sys.stderr)
    extra = len(mismatches) - 8
    if extra > 0:
        print(f"[entrypoint]     ... and {extra} more", file=sys.stderr)
    print("[entrypoint] Fix: rebuild with build arg HOST_HOME set to the config's", file=sys.stderr)
    print(f"[entrypoint]      home ({refs[0]}) AND set the compose mount target to", file=sys.stderr)
    print(f"[entrypoint]      {refs[0]}/.openclaw so absolute paths resolve.", file=sys.stderr)
    sys.exit(1)

print(f"[entrypoint] OK: {len(paths)} agent paths resolve under {home}")
PY

# --- 3. Drop macOS service-supervisor leftovers -----------------------------
# These files are written by the host launchd integration. In foreground mode the
# gateway ignores them, but removing the restart handoff avoids confusing probes.
rm -f "$STATE_DIR/gateway-supervisor-restart-handoff.json" 2>/dev/null || true
# Unset host-injected macOS CA hints; the container uses the system ca-certificates.
unset NODE_EXTRA_CA_CERTS NODE_USE_SYSTEM_CA 2>/dev/null || true

# --- 4. Run the gateway in the foreground -----------------------------------
# --bind: host config is `loopback`, which is unreachable from outside the
#   container. Override to make the published port usable. Default to "lan"
#   (0.0.0.0); override with OPENCLAW_BIND=loopback for host-network deployments.
# --force: reclaim the port if a stale listener is somehow present.
BIND_MODE="${OPENCLAW_BIND:-lan}"
PORT="${OPENCLAW_GATEWAY_PORT:-18789}"

log "starting gateway (foreground) bind=$BIND_MODE port=$PORT version=$(openclaw --version 2>/dev/null || echo '?')"
exec openclaw gateway run \
  --bind "$BIND_MODE" \
  --port "$PORT" \
  --force \
  "$@"
