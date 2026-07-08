#!/usr/bin/env bash
# LYRA-OC Installer
# Double-click this file on macOS to set up your AI agent network.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/albzhu/lyra-oc.git"
OPENCLAW_DIR="$HOME/.openclaw"

red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

# Keep Terminal window open if launched by double-click
trap 'echo ""; yellow "Press Enter to close this window."; read -r' EXIT

echo ""
bold "LYRA-OC Installer"
echo "─────────────────────────────────────────"
echo ""

# ── Ensure git is available ───────────────────────────────────────────────────

if ! command -v git &>/dev/null; then
    yellow "git not found. Triggering macOS developer tools install..."
    xcode-select --install 2>/dev/null || true
    echo ""
    yellow "After the developer tools finish installing, double-click install.command again."
    exit 1
fi

# ── Clone or update the repo ──────────────────────────────────────────────────

if [ -d "$OPENCLAW_DIR/.git" ]; then
    remote=$(git -C "$OPENCLAW_DIR" remote get-url origin 2>/dev/null || echo "")
    if echo "$remote" | grep -q "lyra-oc"; then
        green "Existing LYRA-OC install found at $OPENCLAW_DIR."
        yellow "Re-running setup (your .env and customizations are preserved)."
    else
        yellow "~/.openclaw exists but is not a LYRA-OC repo."
        yellow "If you want a fresh install, back up and remove ~/.openclaw, then re-run."
        exit 1
    fi
elif [ -d "$OPENCLAW_DIR" ] && [ "$(ls -A "$OPENCLAW_DIR" 2>/dev/null)" ]; then
    yellow "~/.openclaw already exists and is not empty."
    yellow "If you want a fresh LYRA-OC install, back up and remove ~/.openclaw, then re-run."
    exit 1
else
    echo "Downloading LYRA-OC to ~/.openclaw ..."
    git clone "$REPO_URL" "$OPENCLAW_DIR"
    green "Downloaded."
fi

echo ""

# ── Run the wizard then bootstrap ────────────────────────────────────────────

cd "$OPENCLAW_DIR"
bash scripts/wizard.sh
make bootstrap
