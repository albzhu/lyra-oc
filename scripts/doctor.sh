#!/usr/bin/env bash
# Health check for LYRA-OC. Run: make doctor
set -uo pipefail

OPENCLAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$OPENCLAW_DIR/.env"

pass=0; fail=0

ok()   { printf '  \033[0;32m✓\033[0m %s\n' "$*"; ((pass++)); }
fail() { printf '  \033[0;31m✗\033[0m %s\n' "$*"; ((fail++)); }
warn() { printf '  \033[0;33m~\033[0m %s\n' "$*"; }

env_get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'" | tail -1; }

echo ""
echo "LYRA-OC Health Check"
echo "────────────────────"

# .env exists
if [ -f "$ENV_FILE" ]; then
    ok ".env found"
else
    fail ".env missing — run: cp env.example .env  (or run install.command)"
fi

# Required keys
or_key=$(env_get "OPENROUTER_API_KEY")
if [ -n "$or_key" ]; then
    ok "OPENROUTER_API_KEY set"
else
    fail "OPENROUTER_API_KEY not set in .env — agents cannot respond without it"
fi

gw_token=$(env_get "GATEWAY_AUTH_TOKEN")
if [ -n "$gw_token" ]; then
    ok "GATEWAY_AUTH_TOKEN set"
else
    fail "GATEWAY_AUTH_TOKEN not set — run: make roll"
fi

# openclaw.json
if [ -f "$OPENCLAW_DIR/openclaw.json" ]; then
    if python3 -c "import json,sys; json.load(open('$OPENCLAW_DIR/openclaw.json'))" 2>/dev/null; then
        ok "openclaw.json valid"
    else
        fail "openclaw.json exists but is invalid JSON — run: make sync"
    fi
else
    fail "openclaw.json missing — run: make sync"
fi

# Gateway running
if openclaw status &>/dev/null 2>&1; then
    ok "OpenClaw gateway running"
else
    fail "Gateway not running — run: make up"
fi

# Workspace dirs
AGENTS=("main" "spectre" "cinder" "echo" "zero" "swift" "sigma" "void")
missing_ws=()
for agent in "${AGENTS[@]}"; do
    dir="$OPENCLAW_DIR/workspace"
    [ "$agent" != "main" ] && dir="${dir}-${agent}"
    [ -d "$dir" ] || missing_ws+=("$agent")
done

if [ ${#missing_ws[@]} -eq 0 ]; then
    ok "All 8 workspace dirs present"
else
    fail "Missing workspace dirs: ${missing_ws[*]} — run: make bootstrap"
fi

# service-env
if [ -f "$OPENCLAW_DIR/service-env/ai.openclaw.gateway.env" ]; then
    ok "service-env configured"
else
    warn "service-env/ai.openclaw.gateway.env not found — run: make sync"
fi

echo ""
echo "────────────────────"
printf "  %s passed  %s failed\n" "$pass" "$fail"
echo ""

[ "$fail" -eq 0 ]
