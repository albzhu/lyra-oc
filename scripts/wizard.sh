#!/usr/bin/env bash
# Interactive setup wizard. Prompts for the minimum required info and writes .env.
# Skips any prompt whose value is already set in .env.
set -euo pipefail

OPENCLAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$OPENCLAW_DIR/.env"

# ── helpers ──────────────────────────────────────────────────────────────────

green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

env_get() {
    grep -E "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'" | tail -1
}

env_set() {
    local key="$1" val="$2"
    if grep -qE "^#?${key}=" "$ENV_FILE" 2>/dev/null; then
        # Replace existing (commented or not)
        sed -i '' "s|^#\?${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

# ── init .env from example if missing ────────────────────────────────────────

if [ ! -f "$ENV_FILE" ]; then
    cp "$OPENCLAW_DIR/env.example" "$ENV_FILE"
fi

# ── banner ────────────────────────────────────────────────────────────────────

echo ""
bold "══════════════════════════════════════"
bold "  Welcome to LYRA-OC Setup!"
bold "  Your personal AI agent network."
bold "══════════════════════════════════════"
echo ""
echo "This wizard sets up your environment in about 3 minutes."
echo "You can press Enter to skip optional steps."
echo ""

# ── Step 1: Name ──────────────────────────────────────────────────────────────

USER_MD="$OPENCLAW_DIR/workspace/USER.md"
existing_name=""
if [ -f "$USER_MD" ]; then
    existing_name=$(grep -m1 "Name:" "$USER_MD" 2>/dev/null | sed 's/.*Name:[[:space:]]*//' | tr -d '*' || true)
fi

if [ -z "$existing_name" ]; then
    printf "Your name (so LYRA knows who she's talking to): "
    read -r user_name
    if [ -n "$user_name" ]; then
        mkdir -p "$(dirname "$USER_MD")"
        cat > "$USER_MD" <<EOF
# USER.md — About You

- **Name:** $user_name
- **Setup date:** $(date +%Y-%m-%d)

LYRA and the other agents use this file to personalize how they work with you.
Feel free to add your preferences, interests, or anything you'd like them to know.
EOF
        green "  Saved your name."
    fi
else
    green "  Name already set: $existing_name"
fi

echo ""

# ── Step 2: OpenRouter API key ────────────────────────────────────────────────

existing_key=$(env_get "OPENROUTER_API_KEY")
if [ -n "$existing_key" ] && [ "$existing_key" != "YOUR_OPENROUTER_KEY_HERE" ]; then
    green "  OpenRouter key already set."
else
    echo "OpenRouter routes your agents to any AI model."
    yellow "  Get a free key at: https://openrouter.ai/keys"
    printf "Paste your OpenRouter API key: "
    read -r or_key
    if [ -n "$or_key" ]; then
        env_set "OPENROUTER_API_KEY" "$or_key"
        green "  OpenRouter key saved."
    else
        yellow "  Skipped. You'll need to add OPENROUTER_API_KEY to .env before agents can respond."
    fi
fi

echo ""

# ── Step 3: Primary AI provider ───────────────────────────────────────────────

echo "Which AI model family should your agents use primarily?"
echo "  1) Google Gemini   — fast and cost-effective"
echo "  2) Anthropic Claude — excellent at complex reasoning"
echo "  3) OpenAI GPT      — versatile and widely tested"
printf "Choice (1-3, or Enter to keep current): "
read -r choice

preset=""
provider_name=""
provider_key_name=""
provider_key_url=""

case "$choice" in
    1)
        preset="gemini"
        provider_name="Gemini"
        provider_key_name="GEMINI_API_KEY"
        provider_key_url="https://aistudio.google.com/apikey"
        ;;
    2)
        preset="claude"
        provider_name="Claude"
        provider_key_name="CLAUDE_API_KEY"
        provider_key_url="https://console.anthropic.com/"
        ;;
    3)
        preset="openai"
        provider_name="GPT"
        provider_key_name="OPENAI_API_KEY"
        provider_key_url="https://platform.openai.com/api-keys"
        ;;
    *)
        yellow "  Keeping existing model config."
        ;;
esac

if [ -n "$preset" ]; then
    python3 "$OPENCLAW_DIR/scripts/set-model-preset.py" "$preset"
    green "  Model preset applied: $provider_name"
    echo ""

    # ── Step 4: Optional direct provider key ──────────────────────────────────
    existing_provider_key=$(env_get "$provider_key_name")
    if [ -z "$existing_provider_key" ]; then
        echo "$provider_name API key improves reliability (optional — OpenRouter works without it)."
        yellow "  Get one at: $provider_key_url"
        printf "Paste your $provider_name key (or Enter to skip): "
        read -r pkey
        if [ -n "$pkey" ]; then
            env_set "$provider_key_name" "$pkey"
            green "  $provider_name key saved."
        else
            yellow "  Skipped. Agents will use $provider_name via OpenRouter."
        fi
    else
        green "  $provider_name key already set."
    fi
fi

echo ""

# ── Generate GATEWAY_AUTH_TOKEN if missing ────────────────────────────────────

existing_token=$(env_get "GATEWAY_AUTH_TOKEN")
if [ -z "$existing_token" ]; then
    token=$(openssl rand -hex 32)
    env_set "GATEWAY_AUTH_TOKEN" "$token"
    green "  Gateway auth token generated."
fi

echo ""
green "Setup complete. Running bootstrap..."
echo ""
