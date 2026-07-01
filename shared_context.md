# Shared Context — LYRA-OC portability pass

**Audience:** LYRA (and the agent fleet). This file records what was changed in the
repo to make a clean checkout portable to a new machine (the Mac Mini migration),
why, and what still has to be done by hand on the target. Read this before acting
on the repo so you don't redo work or assume the old hardcoded paths.

**Date:** 2026-06-27
**Scope:** config/blueprint fixes only. The OpenClaw runtime is still installed
out-of-band (see "Remaining manual steps").

---

## What changed in this pass

### 1. `env.example` — added the 4 missing keys
Reconciled with `openclaw-template.json`. Now includes `OPENCLAW_WORKSPACE_ROOT`
(the single knob that relocates all workspaces), `DISCORD_BOT_TOKEN` (discord
channel is enabled → required), and the optional `GOOGLE_WEB_SEARCH_API_KEY` /
`GOPLACES_API_KEY`. The earlier rename fixes (`GATEWAY_AUTH_TOKEN`,
`CLAUDE_API_KEY`) were already applied.

### 2. Scripts + Docker — reconciled against the live `~/.openclaw` originals
My first-pass versions of these were guesses and were WRONG; they were pointed out to me to fix. The repo now carries the canonical versions
verbatim (they hold logic only, no secrets):
- `scripts/sync-env.sh` — **a Python script** (despite the `.sh` name), so the
  Makefile's `python3 scripts/sync-env.sh` was correct all along; I reverted my
  bogus "fix". It regenerates `openclaw.json` from `openclaw-template.json` by
  injecting `env:VAR` / `${VAR}` from `.env`, runs a drift guard (aborts if
  `openclaw.json` has keys the template lacks), backs up to `openclaw-backups/`,
  syncs `service-env/ai.openclaw.gateway.env`, and writes `~/.gemini/settings.json`.
- `scripts/roll-gateway-token.sh` — reads the existing `GATEWAY_AUTH_TOKEN` from
  `.env` (generates one only if missing), writes it (and `DISCORD_BOT_TOKEN`)
  literally into `openclaw.json`, then `openclaw gateway restart`. NOT a
  rotate-to-random; it materializes the .env token into config.
- `docker/docker.mk` — canonical target set (d-build/up/up-logs/down/restart/
  logs/shell/health/pull-check/help), service name `gateway`.
- **Docker stack ported** from canonical: `docker/Dockerfile`,
  `docker/entrypoint.sh`, `docker/README-docker.md`. Key design: HOME and the
  bind-mount target both = the host path (e.g., `/Users/username/.openclaw`) because
  `openclaw.json` hardcodes absolute workspace/agentDir paths — so the container
  reuses the live state byte-identically. Entrypoint regenerates config via
  sync-env, then `openclaw gateway run --bind lan` (loopback isn't reachable
  through a published port). Pinned `openclaw@2026.6.9`, Node 22-slim, tini PID 1.
- `docker-compose.yml` — now the **canonical `~/.openclaw` file verbatim**
  (Albert provided it): service `gateway`, `container_name: openclaw-gateway`,
  build `docker/Dockerfile` with `OPENCLAW_VERSION: 2026.6.9`, long-form bind
  mount `${OPENCLAW_HOME:-~/.openclaw}` → `/Users/username/.openclaw`, published
  `18789:18789`, a `gateway health` healthcheck, and the commented
  `imessage-bridge` seam under the `imessage` profile.
- Root `Dockerfile` — superseded by `docker/Dockerfile`; converted to a
  non-buildable pointer stub (couldn't delete it from the sandbox).
- `Makefile` — reverted `sync` to `python3 scripts/sync-env.sh`; `roll` stays
  `bash scripts/roll-gateway-token.sh` (both relative, as originally shipped).

### 3. `acpx/*.mjs` — de-hardcoded the ACP binary path
Both `codex-acp-wrapper.mjs` and `claude-agent-acp-wrapper.mjs` had a literal
`/Users/albertzhu/.openclaw/npm/projects/openclaw-acpx-052d680d6d/.../bin.js`
assigned unconditionally, which made the `npmCliPath`/`npx` fallbacks dead code
and would point at a nonexistent path on any other machine. Replaced with
`resolveInstalledBinPath()`: env override (`OPENCLAW_CODEX_ACP_BIN` /
`OPENCLAW_CLAUDE_AGENT_ACP_BIN`) → `createRequire` module resolution → scan of
`~/.openclaw/npm/projects/*/node_modules/@openclaw/acpx/...` → `undefined` so the
existing npx fallback engages. Both files pass `node --check`.

### 4. `Dockerfile` — install OpenClaw from npm (then superseded)
The old root Dockerfile copied a `package.json`/`pnpm-lock.yaml` and ran
`/app/dist/index.js` — none of which exist (LYRA-OC is config, not the runtime).
I first rewrote it to `npm install -g openclaw` on `node:24-bookworm`, but the
reconciliation in §2 replaced it with the canonical `docker/Dockerfile`
(`node:22-slim`, pinned `openclaw@2026.6.9`, host-path-identical design). The
root `Dockerfile` is now a non-buildable pointer stub → see `docker/Dockerfile`.

### 5. `README.md` — reconciled to the new layout
Updated so the docs match the files:
- Architecture tree: dropped root `Dockerfile`; added `scripts/` (sync-env,
  roll-gateway-token) and `docker/` (Dockerfile, entrypoint.sh, docker.mk,
  README-docker.md) subtrees; named both `acpx/` wrappers.
- Config step: replaced `cp openclaw-template.json openclaw.json` with `make sync`
  and noted `openclaw.json` is generated + gitignored (edit the template).
- Env-var names fixed to match `env.example` (`GATEWAY_AUTH_TOKEN`,
  `CLAUDE_API_KEY`, `DISCORD_BOT_TOKEN`).
- Docker section: "stop host gateway first" warning, `make -f docker/docker.mk`
  flow, `lyra-gateway` → `openclaw-gateway` with bind-mount note.
- Service-lifecycle list corrected to the real Makefile targets (added `sync`/
  `up-logs`/`docker`; `roll` described as syncing tokens into `openclaw.json`,
  not random rotation).
- Added an **Optional Integrations** subsection: how to clone the `agent-skills`
  repo into `${OPENCLAW_WORKSPACE_ROOT}/workspace/Repos/agent-skills` (referenced
  by `plugins.load.paths`, gitignored so not carried by a checkout), and how to
  install `opencode` for the acpx ACP backend (binary at `~/.opencode/bin/opencode`,
  config at `~/.openclaw/opencode/opencode.json`). Both flagged optional/host-only.
- Added **OpenClaw runtime install** as walkthrough step 1 (installer script or
  npm + `openclaw onboard --install-daemon`; Node 24 / 22.19+ prereq), renumbered
  the rest (2–6), and corrected the old "make up installs deps" step to "Start the
  Gateway" (`make up` just runs `openclaw gateway start`).

---

## Still TRUE blockers (not fixed here — need the target machine)

- **`openclaw` binary**: install on the Mini —
  `curl -fsSL https://openclaw.ai/install.sh | bash` or `npm i -g openclaw@latest`,
  then `openclaw onboard --install-daemon`.
- **Container loopback gotcha** — now handled: the ported `docker/entrypoint.sh`
  overrides to `--bind lan` (token auth satisfies the guardrail). Host (launchd)
  is still the primary path; Docker is the alt.
- **Docker on a different username** — the bind-mount design assumes the config's
  hardcoded home is matched. On a different username,
  set `HOST_HOME` (compose build arg) and re-sync so `openclaw.json` paths match,
  or the entrypoint's path-check fails fast with instructions.

---

## #6 — What to seed into `~/.openclaw` on the new machine

The repo deploys *into* `~/.openclaw`. These items live there but are gitignored
or machine-specific, so a fresh `git checkout` does NOT carry them:

**Runtime & service**
- OpenClaw CLI install + `openclaw gateway install` (registers the LaunchAgent).
- macOS permissions for iMessage when un-parking: `brew install steipete/tap/imsg`,
  Full Disk Access + Automation→Messages for the gateway (already noted in the
  template's iMessage binding comment).

**Config & secrets (gitignored)**
- `openclaw.json` ← `cp openclaw-template.json openclaw.json` (verify paths/models).
- `.env` ← `cp env.example .env`, fill `GATEWAY_AUTH_TOKEN`, `DISCORD_BOT_TOKEN`,
  model keys. Then `make sync`.
- Model/provider auth: `openclaw secrets configure` (OpenRouter API key, etc.) —
  populates per-agent `auth-profiles.json`.

**Per-agent state & workspaces**
- Agent dirs referenced by `agentDir`: `~/.openclaw/agents/main/agent`,
  `~/.openclaw/agents/sigma/agent` (created by onboarding / agent init).
- The 8 workspaces: `workspace`, `workspace-spectre`, `-cinder`, `-echo`,
  `-zero`, `-swift`, `-sigma`, `-void`. Repo ships only the `workspace/` template
  (IDENTITY/SOUL/USER/AGENTS/TOOLS/HEARTBEAT) — scaffold the rest and author each
  persona's IDENTITY/SOUL/USER.
- Seed Skills into each: the README loop
  `cp -R Skills/ ~/.openclaw/workspace-<agent>/Skills/`.

**External skill repos (gitignored `Repos/`)**
- `plugins.load.paths` points at
  `${OPENCLAW_WORKSPACE_ROOT}/workspace/Repos/agent-skills` — clone that repo.
- Enabled skills not in this package: `portfolio-check`, `scenic-video-pipeline`
  (their data dirs are gitignored; clone/install the skills themselves).

**MIAB runtime files the console expects**
- No root-level files required! With the June 27 portability updates, `claw-callback.py` resolves paths relative to `~/.openclaw` natively, and the console matches this configuration path cleanly inside `oc_config.py`. All callback mechanisms live completely within `Skills/miab-broker/`.

**ACP / opencode**
- The acpx config references `${HOME}/.openclaw/opencode/opencode.json` and
  `~/.opencode/bin/opencode` — install opencode and seed its config if the
  opencode ACP agent is used.

**Regenerated locally (do NOT copy — privacy/churn)**
- `state/sessions/`, `state/daily_wake_up_data.json`, `observability/` reports,
  `*.sqlite*`, logs. These rebuild on first run.
