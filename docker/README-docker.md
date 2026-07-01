# OpenClaw — Docker

Containerized deployment of the OpenClaw gateway.

## Model: one container, all agents

The eight agents (`main`, `planner`, `coder`, `reviewer`, `debug`, `utility`,
`sigma`, `free`) are **not** separate services. They are config-level personas in
`agents.list[]`, routed by a single `openclaw gateway` process. They share
in-process state — agent-to-agent callbacks, the LIFO return stack in
`state/callbacks/`, one Discord connection, and shared `memory/`, `cron/`,
`tasks/`. So the unit is **one container = the whole gateway = all agents**,
mirroring how launchd runs it on the host today.

## What runs, what doesn't

| Channel / feature      | In container? | Notes |
|------------------------|---------------|-------|
| Agents + routing       | ✅            | All 8 agents, one process |
| Discord                | ✅            | Single bot connection |
| Skills / cron / memory | ✅            | Bind-mounted state |
| OpenRouter / Anthropic / Google / OpenAI | ✅ | TLS via system CA |
| **iMessage**           | ❌ (parked)   | Needs native macOS; see option #2 below |

## Prerequisites

- Docker Desktop (Mac) / OrbStack / Colima with the Docker CLI + compose v2.
- The existing `~/.openclaw` state dir on the host (this repo).
- A populated `~/.openclaw/.env` (same keys the host gateway uses):
  `CLAUDE_API_KEY`, `OPENROUTER_API_KEY`, `DISCORD_BOT_TOKEN`,
  `GATEWAY_AUTH_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY`,
  `GOOGLE_WEB_SEARCH_API_KEY`, `GOPLACES_API_KEY`.

> **Important — stop the host gateway first.** Don't run the launchd service and
> the container against the same `~/.openclaw` simultaneously; they'd both write
> the same SQLite DBs. `make down` on the host before `make -f docker/docker.mk d-up`.

## Quick start

```bash
# from ~/.openclaw
make down                              # stop the launchd gateway (avoid double-writers)
make -f docker/docker.mk d-build       # build the image
make -f docker/docker.mk d-up-logs     # start container + follow logs
```

Or with compose directly:

```bash
OPENCLAW_HOME=~/.openclaw docker compose up -d --build
docker compose logs -f gateway
```

To make the make targets first-class, add one line to the main `Makefile`:

```make
include docker/docker.mk
```

## How it works

1. **Image** (`docker/Dockerfile`) — `node:22-bookworm-slim` (openclaw needs Node
   ≥22.19), plus `python3` (for `sync-env.sh`), `ca-certificates`, `git`, `tini`.
   Installs `openclaw@2026.6.9` globally. `HOME=/Users/albertzhu` (see "Paths"
   below), so state resolves to `/Users/albertzhu/.openclaw`.
2. **State** — the host `~/.openclaw` is bind-mounted to
   `/Users/albertzhu/.openclaw`, so all agents/workspaces/memory/sessions/config
   are reused as-is and persist across restarts. Nothing stateful is baked into
   the image — the container reads/writes the **same live files** as the host.

### Paths — why the mount target matches the host

`openclaw.json` stores **absolute** paths for every workspace, `agentDir`, and
skill repo (e.g. `/Users/albertzhu/.openclaw/workspace-sigma`). For those to
resolve inside the container, `HOME` and the bind-mount target are set to the
**same** host path (`/Users/albertzhu`). This keeps the local and Docker configs
byte-identical — no path rewriting, switch back and forth freely. If your host
username differs, override the `HOST_HOME` build arg in `docker-compose.yml` and
the mount `target` to match.

> **acpx / opencode caveat:** the `acpx` plugin shells out to `~/.opencode/bin/opencode`,
> which is a separate binary not installed in the image. That one backend will be
> unavailable in-container unless you add it; the other agents are unaffected.
3. **Entrypoint** (`docker/entrypoint.sh`) — regenerates `openclaw.json` from
   `openclaw-template.json` + `.env` (same as `make sync`), drops macOS
   launchd/CA leftovers, then `exec openclaw gateway run` in the **foreground**
   (no service supervisor). `tini` handles PID 1 / signals.
4. **Networking** — host config binds `loopback`, which is unreachable from
   outside the container, so the entrypoint overrides to `--bind lan` (0.0.0.0)
   and publishes `18789:18789`. Auth stays token-mode (`GATEWAY_AUTH_TOKEN`).

### Config changes

Edit `openclaw-template.json` (never `openclaw.json` directly — it's regenerated).
The entrypoint re-runs `sync-env.sh` on every boot, so:

```bash
# edit openclaw-template.json, then:
docker compose restart gateway
```

### Updating openclaw

Bump the version in **two** places and rebuild:
`docker-compose.yml` (`OPENCLAW_VERSION`) and `docker/Dockerfile` (`ARG OPENCLAW_VERSION`).

```bash
make -f docker/docker.mk d-pull-check   # installed pin vs latest on npm
make -f docker/docker.mk d-restart      # rebuild + recreate
```

## Option #2 — host-side iMessage bridge (after the Mac mini transfer)

iMessage can't run in the container: it needs Messages.app + an Apple ID session,
AppleScript/Automation, Full Disk Access to `chat.db`, and the arm64-native `imsg`
binary. None of that crosses the Linux-VM boundary, even on a Mac mini host. So
the plan is a **small native macOS process** on the mini that owns the iMessage
channel and relays to/from the containerized gateway.

The seam is already wired:

- The container can reach the host at **`host.docker.internal`**
  (`extra_hosts: host-gateway` in `docker-compose.yml`).
- A disabled `imessage-bridge` stub (under the `imessage` compose profile) marks
  where the host-side relay config goes.

When the mini is ready:

1. Build/run the bridge **natively on macOS** (not as a compose service) — grant
   it Full Disk Access + Automation→Messages, `brew install steipete/tap/imsg`.
2. Point it at the gateway: `ws://localhost:18789` with `GATEWAY_AUTH_TOKEN`.
3. Re-enable `channels.imessage` in `openclaw-template.json` and point its
   transport at the bridge (e.g. an `OPENCLAW_IMESSAGE_BRIDGE_URL` passed into the
   gateway's `environment:` block).
4. Restart the gateway container.

Until then iMessage stays `enabled: false` and nothing is lost — it's already
parked in the config.

## Troubleshooting

- **Port already in use** — the host launchd gateway is still running. `make down`.
- **`state dir ... is missing`** — `OPENCLAW_HOME` didn't resolve; pass it
  explicitly: `OPENCLAW_HOME=$HOME/.openclaw docker compose up -d`.
- **Auth failures from clients** — confirm `GATEWAY_AUTH_TOKEN` in `.env` matches
  what clients send; the entrypoint also exports it as `OPENCLAW_GATEWAY_TOKEN`.
- **TLS errors** — the container uses system `ca-certificates`; the host's
  `NODE_EXTRA_CA_CERTS` is intentionally unset inside the container.
