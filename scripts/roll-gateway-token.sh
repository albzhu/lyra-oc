#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$OPENCLAW_DIR/.env"
CONFIG_FILE="$OPENCLAW_DIR/openclaw.json"

# Read GATEWAY_AUTH_TOKEN from .env
token=$(grep -E '^GATEWAY_AUTH_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'" | head -1)

# Read DISCORD_BOT_TOKEN from .env (optional — synced into openclaw.json if present)
discord_token=$(grep -E '^DISCORD_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'" | head -1)

if [[ -z "$token" ]]; then
  echo "GATEWAY_AUTH_TOKEN not set in $ENV_FILE — generating a new one..."
  token=$(openssl rand -hex 24)
  # Append to .env if key is missing entirely, otherwise replace the blank line
  if grep -qE '^GATEWAY_AUTH_TOKEN=' "$ENV_FILE"; then
    sed -i '' "s|^GATEWAY_AUTH_TOKEN=.*|GATEWAY_AUTH_TOKEN=$token|" "$ENV_FILE"
  else
    echo "GATEWAY_AUTH_TOKEN=$token" >> "$ENV_FILE"
  fi
  echo "Generated and saved new token to $ENV_FILE"
fi

echo "Rolling gateway auth token..."

# Update openclaw.json in-place
python3 - "$CONFIG_FILE" "$token" "$discord_token" <<'EOF'
import sys, json
config_path, new_token = sys.argv[1], sys.argv[2]
discord_token = sys.argv[3] if len(sys.argv) > 3 else ""
with open(config_path) as f:
    config = json.load(f)
config.setdefault('gateway', {}).setdefault('auth', {})['token'] = new_token
updated = ["gateway.auth.token"]
# Sync Discord token from .env so a rotated bot token actually reaches the runtime.
# Only overwrite when present in .env and when the channel is already configured.
if discord_token:
    discord = config.get('channels', {}).get('discord')
    if isinstance(discord, dict):
        if discord.get('token') != discord_token:
            discord['token'] = discord_token
            updated.append("channels.discord.token")
    else:
        print("WARNING: DISCORD_BOT_TOKEN set in .env but channels.discord missing in openclaw.json — skipped", file=sys.stderr)
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
print("Updated openclaw.json: " + ", ".join(updated))
EOF

# Restart gateway so service-env/*.env regenerates
echo "Restarting gateway..."
openclaw gateway restart

echo "Done. service-env/ai.openclaw.gateway.env will reflect the new token after restart."
