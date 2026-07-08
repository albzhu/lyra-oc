#!/usr/bin/env bash
# Bootstrap LYRA-OC on a fresh machine or re-run safely on an existing install.
# Called by: make bootstrap  (and by install.command after the wizard)
set -euo pipefail

OPENCLAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$OPENCLAW_DIR"

green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
step()   { printf '\033[1m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# ── 1. Homebrew ───────────────────────────────────────────────────────────────

step "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    yellow "  Homebrew not found. Installing (this may ask for your password)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for the rest of this script
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
    green "  Homebrew installed."
else
    green "  Homebrew OK."
fi

# ── 2. Node.js ────────────────────────────────────────────────────────────────

step "Checking Node.js..."
node_ok=false
if command -v node &>/dev/null; then
    node_ver=$(node --version | sed 's/v//' | cut -d. -f1)
    if [ "$node_ver" -ge 22 ]; then
        node_ok=true
    fi
fi

if ! $node_ok; then
    yellow "  Node 22+ not found. Installing via Homebrew..."
    brew install node@22
    brew link --overwrite node@22
    green "  Node $(node --version) installed."
else
    green "  Node $(node --version) OK."
fi

# ── 3. Python 3 ───────────────────────────────────────────────────────────────

step "Checking Python 3..."
if ! command -v python3 &>/dev/null; then
    yellow "  Python 3 not found. Installing via Homebrew..."
    brew install python3
    green "  Python $(python3 --version) installed."
else
    green "  Python $(python3 --version) OK."
fi

# ── 4. OpenClaw CLI ───────────────────────────────────────────────────────────

step "Checking OpenClaw CLI..."
if ! command -v openclaw &>/dev/null; then
    yellow "  OpenClaw not found. Installing..."
    npm install -g openclaw@latest
    green "  OpenClaw $(openclaw --version 2>/dev/null || echo 'installed') OK."
else
    green "  OpenClaw $(openclaw --version 2>/dev/null || echo 'found') OK."
fi

# ── 5. Workspace directories ──────────────────────────────────────────────────

step "Seeding agent workspaces..."

AGENTS=("spectre" "cinder" "echo" "zero" "swift" "sigma" "void")
for agent in "${AGENTS[@]}"; do
    dir="$OPENCLAW_DIR/workspace-${agent}"
    mkdir -p "$dir"
    # Seed IDENTITY.md and SOUL.md only if they don't already exist
    for doc in IDENTITY.md SOUL.md; do
        src="$OPENCLAW_DIR/workspace-${agent}/${doc}"
        if [ ! -f "$src" ]; then
            # The files are committed in the repo; this handles the case where
            # the workspace dir was created but files are missing.
            yellow "  workspace-${agent}/${doc} missing — skipping (check repo)"
        fi
    done
done
green "  Workspace dirs ready."

# ── 6. service-env directory ──────────────────────────────────────────────────

step "Checking service-env..."
mkdir -p "$OPENCLAW_DIR/service-env"
SVC_ENV="$OPENCLAW_DIR/service-env/ai.openclaw.gateway.env"
if [ ! -f "$SVC_ENV" ]; then
    # Create a minimal stub; make sync will populate it
    printf '# OpenClaw gateway service environment\n# Auto-populated by: make sync\n' > "$SVC_ENV"
fi
green "  service-env ready."

# ── 7. Handle pre-existing openclaw.json (the drift fix) ─────────────────────

step "Checking for existing openclaw.json..."
if [ -f "$OPENCLAW_DIR/openclaw.json" ]; then
    yellow "  Found existing openclaw.json (from a prior install or onboard)."
    yellow "  Backing it up and regenerating from template..."
    mv "$OPENCLAW_DIR/openclaw.json" "$OPENCLAW_DIR/openclaw.json.pre-bootstrap.bak"
    green "  Backed up to openclaw.json.pre-bootstrap.bak"
fi

# ── 8. Generate openclaw.json from template ───────────────────────────────────

step "Generating openclaw.json from template..."
python3 "$OPENCLAW_DIR/scripts/sync-env.sh"
green "  openclaw.json generated."

# ── 9. Install gateway daemon ─────────────────────────────────────────────────

step "Installing gateway daemon..."
openclaw gateway install --force
green "  Gateway daemon installed."

# ── 10. Start the gateway ─────────────────────────────────────────────────────

step "Starting gateway..."
openclaw gateway start
sleep 3

if openclaw status &>/dev/null; then
    green "  Gateway running."
else
    yellow "  Gateway may still be starting. Check with: make logs"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
printf '\033[0;32m'
echo "══════════════════════════════════════════"
echo "  LYRA-OC is running!"
echo ""
echo "  Open the OpenClaw web UI to get started."
echo "  Say hello to LYRA — she'll guide you"
echo "  through connecting Discord, Telegram,"
echo "  or any other channel you want."
echo "══════════════════════════════════════════"
printf '\033[0m\n'

# Open the web UI if on macOS
if command -v open &>/dev/null; then
    open "http://localhost:18789" 2>/dev/null || true
fi
