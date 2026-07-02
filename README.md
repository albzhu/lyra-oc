# LYRA-OC

**LYRA-OC** is a fully customized, professional reference architecture and boilerplate for managing advanced **Multi-Agent Systems** under digital sovereignty. Built as a secure, local, and document-driven engine, it enables developers and power-users to deploy, coordinate, and orchestrate automated agents within highly disciplined local workspaces.

Named after the constellation *Lyra*—and its central guiding star Vega—this repository establishes a luminous, yield-driven ecosystem for parallelized workflows and asynchronous multi-agent coordination.

Note: this project has been vibe-coded, and still in testing...

---

## 🌌 Key Highlights

* **Multi-Agent Coordination Blueprint:** Define and maintain multiple specialist personas (LYRA the router, SPECTRE the architect, Cinder the coder, ECHO the reviewer, and auxiliary support/debug twins) inside distinct persistent boundaries.
* **MIAB (Message in a Bottle) Protocol:** A robust, file-based asynchronous callback transport. Prevents agent idling and token waste by pushing "resume contexts" onto an active return-stack lifecycle, freeing execution turns while delegating deep reasoning processes down the chain.
* **Claude Code / ACP Native Support:** Seamless integration wrappers mapping terminal environments to Claude's Agentic Control Protocol (ACP) for high-context parallel debugging and automated refactoring inside individual workspaces.
* **Zero-Leak Git Hygiene:** Pre-configured constraints and `.gitignore` protocols defensively structured to ensure local secrets, databases, high-churn session trajectories, and personal memories never escape to remote source controls.
* **Automated Operational Workflows:** Standard life-cycle orchestrations bundled via a master `Makefile` for reliable service actions, token rolls, runtime syncing, and pipeline routines.

---

## 📁 System Architecture

```
LYRA-OC/
├── openclaw-template.json        # Unified master blueprint (Models, Roles, Plugins & Channels)
├── Makefile                      # Master service daemon orchestration & lifecycle scripts
├── docker-compose.yml            # Container orchestration (service: gateway → one process, all agents)
├── env.example                   # Secured container environment variables template
├── .gitignore                    # Gold-standard directory defense template
├── scripts/                      # Lifecycle helpers invoked by the Makefile
│   ├── sync-env.sh               # (python3) Regenerates openclaw.json from template + .env, syncs service-env
│   └── roll-gateway-token.sh     # Materializes GATEWAY/DISCORD tokens into openclaw.json, then restarts
├── docker/                       # Containerized gateway (host-path-identical bind mount)
│   ├── Dockerfile                # node:22-slim + openclaw CLI; HOME = host path so absolute config paths resolve
│   ├── entrypoint.sh             # Regenerates config (sync-env), then `openclaw gateway run --bind lan` (foreground)
│   ├── docker.mk                 # `make -f docker/docker.mk d-*` build/up/logs/health targets
│   └── README-docker.md          # Container deployment guide + parked iMessage-bridge seam
├── acpx/                         # ACP wrappers for Terminal LLMs (codex + claude-agent)
│   ├── codex-acp-wrapper.mjs     # Resolves the Codex ACP binary dynamically (env → require → project scan)
│   └── claude-agent-acp-wrapper.mjs  # Same resolver for the Claude Agent ACP binary
├── Skills/                       # Pre-configured sovereign local skills templates
│   ├── miab-broker/              # MIAB LIFO callback stack: protocol docs + ledger observer + reaper
│   │   ├── SKILL.md              # Lifecycle (register/create/forward/return/resolve) & state files
│   │   └── scripts/
│   │       ├── interagent_queue.py   # Ledger observer → rich logs → Discord (#scheduling) pipe
│   │       ├── reap-callbacks.sh     # Stale/orphan bottle reaper + dangling-handle cleanup
│   │       └── bin/
│   │           ├── claw-callback.py       # Core LIFO stack engine
│   │           └── claw-callback-reap.sh  # Low-level daemon reaper script
│   └── token-observability/     # Daily token/cost auditing & multi-agent resource monitoring
│       ├── SKILL.md              # Mission, trajectory interpretation & resource-leak checklist
│       └── scripts/
│           ├── token_tracker.py                   # Daily compiler: costs, per-skill avgs, leak audit
│           └── generate_observability_manifest.py # Weekly rollup → observability/weekly/
├── tools/                        # Host-side operator tooling
│   └── console/                  # Config UIs over openclaw-template.json (web app + console)
│       ├── OpenClaw-Console.command  # Local web app (Models / Agents / Reports / Terminal tabs)
│       ├── oc_web.py             # Web server entrypoint
│       ├── oc_config.py          # Shared config core
│       ├── model_config.py       # Model definitions
│       └── observability_report.py   # Reads observability/daily token reports
└── workspace/                    # Centralized assistant workspace template
    ├── IDENTITY.md               # Persona identifier, designate, and description
    ├── SOUL.md                   # Agent guidelines, red-lines, and vibe check
    ├── USER.md                   # Human identity file mapping local preferences
    ├── AGENTS.md                 # Agent routing patterns and delegation directions
    ├── TOOLS.md                  # Active tool lists, platform mappings, and notes
    └── HEARTBEAT.md              # Periodic background audit loop rules
```

---

## 🚦 Getting Started

### 1. Prerequisites
* **macOS / Linux Environment** (Metal acceleration recommended for local executions via Ollama)
* **Node 24** (recommended) or **Node 22.19+** — required by the OpenClaw runtime
* **Python 3.10+** (Required for callback & tool scripts)
* **OpenClaw** installed on the host (step 1 of the walkthrough below installs it)

### 2. Manual Local Installation Walkthrough (Guidance)
Running the platform directly on your local system allows the framework to make native terminal completions, manipulate the filesystem directly, and execute fast scripting procedures. Follow these steps to configure your environment:

1. **Install the OpenClaw Runtime:**
   LYRA-OC is config + skills, not the runtime itself, so the `openclaw` binary is
   installed separately. Use the official installer (detects OS, installs Node if
   needed, runs onboarding), or install via a package manager you manage yourself:
   ```bash
   # Recommended: official installer
   curl -fsSL https://openclaw.ai/install.sh | bash

   # Or via npm/pnpm/bun (if you manage Node yourself), then register the daemon:
   npm install -g openclaw@latest
   openclaw onboard --install-daemon   # macOS LaunchAgent / Linux systemd user service

   # Verify:
   openclaw --version
   openclaw doctor
   ```
   > LYRA-OC's files are expected to live in `~/.openclaw` (the `Makefile`/console
   > run from there). Place this repo's contents there, or point
   > `OPENCLAW_WORKSPACE_ROOT` at its location.

2. **Configure Environment Variables:**
   Create your local `.env` file to hold access keys and custom configurations:
   ```bash
   cp env.example .env
   # Open .env and populate GATEWAY_AUTH_TOKEN, DISCORD_BOT_TOKEN, API keys (e.g., CLAUDE_API_KEY, GEMINI_API_KEY), and other settings.
   ```

3. **Generate Your Configuration Manifest (`openclaw.json`):**
   `openclaw.json` is *generated* from the template with secrets injected from `.env` — don't edit it by hand (it's regenerated and gitignored):
   ```bash
   make sync
   # Regenerates openclaw.json from openclaw-template.json (+ .env) and syncs service-env.
   # Always edit openclaw-template.json instead, then re-run `make sync` to apply.
   ```

4. **Start the Gateway:**
   With the runtime installed and config generated, start the gateway service:
   ```bash
   make up        # starts the gateway daemon (returns immediately)
   make up-logs   # or: start, then follow logs
   ```

5. **Verify Gateway Connectivity:**
   Confirm the OpenClaw Gateway is online and executing correctly:
   ```bash
   # Status of your host service daemon
   openclaw status
   # Check logs and confirm target port (default: 18789) is bound
   tail -n 100 ~/.openclaw/logs/gateway.log
   ```

6. **Deploy Sovereign Local Skills:**
   Ensure each of your specialized agent workspaces contains the necessary target capability libraries:
   ```bash
   # Initialize and seed individual agent workspaces (SPECTRE, Cinder, etc.) with pre-configured templates:
   for agent in spectre cinder echo zero void; do
     mkdir -p ~/.openclaw/workspace-$agent/Skills
     cp -R Skills/ ~/.openclaw/workspace-$agent/Skills/
   done
   ```

### Optional Integrations
These are **not required** for the gateway to boot. The config references each by
path, so install them only if you want the corresponding capability. Without them
the rest of the fleet runs unaffected — OpenClaw skips the missing path/backend.

#### Optional: `agent-skills` repo (external plugin skills)
`openclaw-template.json` loads extra plugin skills from
`plugins.load.paths → ${OPENCLAW_WORKSPACE_ROOT}/workspace/Repos/agent-skills`.
The whole `Repos/` tree is gitignored, so a clean checkout does **not** carry it —
clone it into that exact location:

```bash
# Defaults to ~/.openclaw when OPENCLAW_WORKSPACE_ROOT is unset.
mkdir -p "${OPENCLAW_WORKSPACE_ROOT:-$HOME/.openclaw}/workspace/Repos"
git clone <your-agent-skills-remote> \
  "${OPENCLAW_WORKSPACE_ROOT:-$HOME/.openclaw}/workspace/Repos/agent-skills"
make restart   # reload so the new plugin path is picked up
```

If you don't use it, drop the entry from `plugins.load.paths` in
`openclaw-template.json` and `make sync` to avoid a missing-path warning.

#### Optional: `opencode` (ACP coding backend)
The `acpx` plugin can route to [opencode](https://opencode.ai) as an ACP agent.
Its configured command is:

```
env OPENCODE_CONFIG=${HOME}/.openclaw/opencode/opencode.json ~/.opencode/bin/opencode acp
```

So two things must exist for that backend to work:

```bash
# 1. The opencode binary at ~/.opencode/bin/opencode (per opencode's installer):
curl -fsSL https://opencode.ai/install | bash

# 2. Its config where the acpx command expects it:
mkdir -p ~/.openclaw/opencode
$EDITOR ~/.openclaw/opencode/opencode.json   # model/provider config for opencode
```

> **Note:** opencode is a separate binary not installed in the Docker image, so
> this backend is host-only. The other ACP backends (Codex, Claude Agent) and all
> agents are unaffected if opencode is absent.

### Alternatively: Run with Docker Compose (Containerized Version)
If you prefer a sandboxed, containerized environment that keeps your host machine completely untouched, you can use the included Docker deployment:

> **Stop the host gateway first** (`make down`) — the container bind-mounts the same `~/.openclaw`, and two gateways writing the same SQLite DBs will corrupt state.

1. Copy the example environment template and configure your access tokens:
   ```bash
   cp env.example .env
   # Open `.env` and fill in your GATEWAY_AUTH_TOKEN, DISCORD_BOT_TOKEN, API keys, and timezone.
   ```
2. Build and stand up the OpenClaw service inside a background container:
   ```bash
   make -f docker/docker.mk d-build      # build the image
   make -f docker/docker.mk d-up-logs    # start container + follow logs
   ```
   This spins up the `openclaw-gateway` container, binds port `18789`, and bind-mounts your live `~/.openclaw` so all agents/workspaces/memory/sessions are reused as-is. See `docker/README-docker.md` for details. (Plain `docker compose up -d --build` works too.)

### 3. Service Lifecycle
The master `Makefile` exposes reliable commands for controlling your local orchestrations safely:
```bash
make sync       # Regenerate openclaw.json from template + .env, sync service-env
make up         # Start the gateway (returns immediately)
make up-logs    # Start the gateway, then follow logs
make down       # Stop the gateway
make restart    # sync → stop → update → start + follow logs
make roll       # Sync GATEWAY_AUTH_TOKEN / DISCORD_BOT_TOKEN from .env into openclaw.json, then restart
make docker     # Stop host gateway, then build + run the container (follows logs)
```

---

## 🔄 MIAB Protocol (Message in a Bottle)

The asynchronous callback mechanism has been elevated to a formal local task capability: the **`miab-broker`** skill. 

This protocol allows an active caller to spawn a delegated agent on a heavy/deep background task and yield its runtime session immediately—preventing CPU-blocking and token-wasting loop-polls. Controls cleanly unwind back up the LIFO (Last-In-First-Out) state stack whenever woken.

For full execution specifications, CLI command options, and ledger observer triggers, inspect **`Skills/miab-broker/SKILL.md`**.

```
       [Caller: MAIN/ORCHESTRATOR]
             │   Creates CB ID, pushes resume frame,
             │   hands off task & ends turn.
             ▼
      [Holder: PLANNER]
             │   Runs complex planning; forwards
             │   to coder if subtasks arise.
             ▼
       [Holder: CODER]
             │   Completes work; calls returnValue.
             ▼
[Target resurfaced & woken via cron(action=wake)]
```

### Callback Operations
All callback actions are tracked directly via the registry CLI under the control of the `miab-broker` skill:
```bash
# Register an agent path to enable the wake path
python3 Skills/miab-broker/scripts/bin/claw-callback.py register --agent main --agent-id agent:main

# Initiate an asynchronous handoff
python3 Skills/miab-broker/scripts/bin/claw-callback.py create --task "Analyze files" --from main --to planner \
  --summary "Awaiting architecture file output" \
  --step "Examine generated architecture maps" \
  --expects "Clean JSON spec mapping targets"

# Complete the work and surface the target returning holder session
python3 Skills/miab-broker/scripts/bin/claw-callback.py return --id cb-XXXX --from planner --result "Analysis finished"
```

---

## 🛡️ Git Hygiene Policy

This blueprint comes equipped with a strict local `.gitignore` optimized for LLM workspaces. It guarantees that the following elements stay strictly confidential and local to your system:
* Active session logs, prompts, and raw chat trajectories (under `state/sessions/`)
* Local configurations (`openclaw.json` and `.env` files)
* SQLite database file states, journals, and WAL temporary registers (`*.sqlite*`)
* Backup, roll transcripts, and temporary debugging paths

---

## 📜 License

This boilerplate configuration and blueprint package are distributed under the **MIT License**. Build, modify, and customize to mold your digital sovereignty in absolute privacy.
