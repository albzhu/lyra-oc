# LYRA-OC

**LYRA-OC** is a ready-to-run multi-agent AI system built on [OpenClaw](https://openclaw.ai). Eight specialized agents — each with a distinct role and personality — run locally on your machine, communicate with each other asynchronously, and are accessible through any channel you choose (web UI, Discord, Telegram, iMessage, and more).

Named after the constellation *Lyra* and its brightest star, Vega — the luminous center everything else orients around.

> This project is actively evolving. Expect rough edges and feel free to make it your own.

---

## Quick Start

**On macOS:** download [`install.command`](./install.command), double-click it, and follow the prompts. That's it.

The installer handles everything — Homebrew, Node, Python, the OpenClaw runtime, agent configuration, and startup. When it finishes, your browser opens to the OpenClaw web UI and LYRA is ready to talk.

**You'll need:**
- A Mac running macOS 12 or later
- An [OpenRouter](https://openrouter.ai/keys) API key (free to sign up)
- About 3–5 minutes

**After setup**, say hello to LYRA in the web UI. She'll walk you through connecting any additional channels (Discord, Telegram, iMessage) and explain what each agent does.

---

## The Agent Fleet

Eight agents run in parallel, each with a specialized role:

| Agent | Name | Role |
|-------|------|------|
| `main` | **LYRA** | Orchestrator — your primary interface, routes work to specialists |
| `planner` | **SPECTRE** | Architect — maps strategy and builds plans before any code is touched |
| `coder` | **Cinder** | Implementer — turns plans into clean, working diffs |
| `reviewer` | **ECHO** | Auditor — finds bugs, security gaps, and logic flaws |
| `debug` | **Zero** | Debugger — reproduces, isolates, and fixes issues methodically |
| `utility` | **Swift** | Utility worker — handles fast, mechanical tasks cheaply |
| `sigma` | **SIGMA** | Analyst — financial and data analysis with a quant lens |
| `free` | **VOID** | Scout — low-cost background checks and liveness monitoring |

Each agent has its own workspace (`workspace-{name}/`) with identity and personality docs that you can read and customize.

---

## How Agents Talk to Each Other

LYRA-OC uses the **MIAB (Message in a Bottle) Protocol** for async agent coordination. Instead of blocking a session while waiting for another agent to finish, the originating agent packages a compact resume context, fires the delegation, and ends its turn. When the work is done, the finishing agent wakes the next one in the chain.

```
LYRA receives a complex task
  │  creates callback, delegates to SPECTRE, ends turn
  ▼
SPECTRE architects a plan
  │  forwards to Cinder with the blueprint, ends turn
  ▼
Cinder implements the plan
  │  returns result, wakes SPECTRE
  ▼
SPECTRE reviews output, wakes LYRA
  ▼
LYRA delivers the final result to you
```

No agent idles waiting. No tokens wasted on polling. The full protocol is documented in [`Skills/miab-broker/SKILL.md`](./Skills/miab-broker/SKILL.md).

---

## File Structure

```
lyra-oc/
├── install.command               Double-clickable macOS installer
├── openclaw-template.json        Master config — edit this, never openclaw.json
├── Makefile                      Lifecycle commands (see below)
├── env.example                   Environment variable template
├── scripts/
│   ├── wizard.sh                 Interactive setup prompts
│   ├── bootstrap.sh              Prereq install + workspace seeding + gateway start
│   ├── set-model-preset.py       Patches model primary/fallbacks by provider choice
│   ├── sync-env.sh               Regenerates openclaw.json from template + .env
│   └── roll-gateway-token.sh     Rotates gateway auth token
├── workspace/                    LYRA's workspace (your primary agent)
├── workspace-spectre/            SPECTRE's workspace
├── workspace-cinder/             Cinder's workspace
├── workspace-echo/               ECHO's workspace
├── workspace-zero/               Zero's workspace
├── workspace-swift/              Swift's workspace
├── workspace-sigma/              SIGMA's workspace
├── workspace-void/               VOID's workspace
├── Skills/
│   ├── miab-broker/              Async agent callback protocol + CLI
│   └── token-observability/      Daily token cost auditing
├── tools/console/                Local web config UI for model/agent settings
├── acpx/                         ACP wrappers for Claude Code and Codex
└── docker/                       Containerized deployment (advanced)
```

**Key rule:** always edit `openclaw-template.json`, never `openclaw.json`. The live config is regenerated from the template every time you run `make sync`.

---

## Makefile Reference

```bash
make bootstrap    # First-time setup: prereqs, workspaces, gateway
make doctor       # Health check — shows what's working and what's not

make up           # Start the gateway
make up-logs      # Start the gateway and follow logs
make down         # Stop the gateway
make restart      # sync → stop → update runtime → start + follow logs
make sync         # Regenerate openclaw.json from template + .env
make update       # Update the OpenClaw runtime
make roll         # Rotate the gateway auth token
make logs         # Follow live gateway logs
make force        # Reinstall the launchd daemon (fixes "service not loaded")
make doctor       # Check gateway, env, workspaces, and config
```

---

## Configuration

### Adding or changing API keys

Edit `.env`, then run `make sync` to apply:

```bash
OPENROUTER_API_KEY=sk-or-...     # Required — all agents route through OpenRouter
CLAUDE_API_KEY=sk-ant-...        # Optional — improves Claude reliability
GEMINI_API_KEY=AIza...           # Optional — improves Gemini reliability
OPENAI_API_KEY=sk-...            # Optional — improves GPT reliability
GATEWAY_AUTH_TOKEN=...           # Auto-generated on first run
```

### Changing a model

Edit `openclaw-template.json` → find the agent under `agents.list` → update `model.primary` or `model.fallbacks`. Then:

```bash
make sync && make restart
```

Or run the preset script to reconfigure the default model family for all agents:

```bash
python3 scripts/set-model-preset.py claude   # or: gemini, openai
make sync && make restart
```

### Customizing an agent

Each workspace has two files you can freely edit:

- `IDENTITY.md` — name, vibe, emoji, avatar
- `SOUL.md` — personality, operating principles, boundaries

Changes take effect on the next conversation with that agent — no restart needed.

---

## Optional Integrations

These are not required to run. LYRA can walk you through any of them after your system is up.

### Channels

By default LYRA is accessible through the OpenClaw web UI. To connect additional channels, ask LYRA: *"How do I connect Discord?"* (or Telegram, iMessage, etc.) — she'll guide you step by step.

For Discord specifically, the template is pre-configured; you just need to supply `DISCORD_BOT_TOKEN` in `.env`.

### Agent Skills plugin (`agent-skills`)

The template can load extra plugin skills from an external repo:

```bash
mkdir -p ~/.openclaw/workspace/Repos
git clone <your-agent-skills-remote> ~/.openclaw/workspace/Repos/agent-skills
make restart
```

If you're not using it, remove the entry from `plugins.load.paths` in `openclaw-template.json` to suppress the missing-path warning.

### OpenCode ACP backend

The `acpx` plugin can delegate coding work to [opencode](https://opencode.ai):

```bash
curl -fsSL https://opencode.ai/install | bash
mkdir -p ~/.openclaw/opencode
# configure ~/.openclaw/opencode/opencode.json with your model settings
```

### Docker

If you prefer a containerized setup:

```bash
make down                              # stop the host gateway first
make -f docker/docker.mk d-build
make -f docker/docker.mk d-up-logs
```

The container bind-mounts `~/.openclaw`, so all agents, workspaces, and sessions are shared with the host. See [`docker/README-docker.md`](./docker/README-docker.md) for details.

---

## Updating

To update the OpenClaw runtime:

```bash
make update
```

This updates the `openclaw` binary only. Your config, workspaces, and `.env` are untouched.

To pull the latest LYRA-OC templates and scripts (your `.env` and any local customizations are preserved — they're gitignored):

```bash
git -C ~/.openclaw pull origin main
make restart
```

---

## Git Hygiene

The `.gitignore` is configured to keep secrets and high-churn state local:

- `.env` and `openclaw.json` — never committed
- `*.sqlite*` — agent state databases
- `state/sessions/` — chat history and skill prompt cache
- `openclaw-backups/` — config snapshots
- `logs/` — gateway logs

What IS committed: `openclaw-template.json`, workspace identity docs, skills, and scripts. Everything needed to reproduce the setup on a new machine.

---

## Troubleshooting

**`make doctor` shows failures:**
Run the suggested fix command shown next to each failure. Most issues resolve with `make sync` followed by `make restart`.

**Gateway won't start ("service not loaded"):**
```bash
make force
```

**`make sync` fails with drift error:**
`openclaw.json` has keys not in the template — usually from `openclaw update` adding new fields. Port the listed keys into `openclaw-template.json`, then re-run `make sync`.

**Starting fresh on an existing machine:**
The installer detects an existing `~/.openclaw` and re-runs setup without touching your `.env` or customizations.

---

## License

MIT — build on it, customize it, make it yours.
