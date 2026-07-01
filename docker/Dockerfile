# syntax=docker/dockerfile:1
#
# OpenClaw gateway — containerized.
#
# One container == the whole gateway == all agents. The "agents" (main, planner,
# coder, reviewer, debug, utility, sigma, free) are config-level personas routed
# by a single `openclaw gateway` process, so they are NOT split across containers.
#
# iMessage is intentionally out of scope here (option #3): it requires native
# macOS frameworks that do not exist inside a Linux container, even on a Mac mini.
# A future host-side iMessage bridge (option #2) plugs in over the network — see
# docker/README-docker.md and the seam in docker-compose.yml.

# openclaw 2026.6.9 requires Node >=22.19.0 (npm: `engines.node`).
FROM node:22-bookworm-slim

# Pin the openclaw release. Bump this in lockstep with update-check.json.
ARG OPENCLAW_VERSION=2026.6.9

# Runtime deps:
#   - python3        -> scripts/sync-env.sh is a python3 script (regenerates openclaw.json)
#   - ca-certificates-> TLS to OpenRouter/Anthropic/Google/Discord (replaces macOS
#                       keychain CA; the host NODE_EXTRA_CA_CERTS is dropped in-container)
#   - git            -> some skills/plugins shell out to git
#   - tini           -> proper PID 1 / signal forwarding for clean shutdown
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        ca-certificates \
        git \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Install the openclaw CLI globally (provides the `openclaw` binary, bin: openclaw.mjs).
RUN npm install -g "openclaw@${OPENCLAW_VERSION}" \
    && npm cache clean --force

# CRITICAL: openclaw.json hard-codes ABSOLUTE host paths for every workspace,
# agentDir, and skill repo (e.g. /Users/albertzhu/.openclaw/workspace-sigma).
# To make those resolve unchanged inside the container, HOME — and the bind-mount
# target — must match the host path EXACTLY. So HOME=/Users/albertzhu and the host
# ~/.openclaw mounts to /Users/albertzhu/.openclaw (see docker-compose.yml).
# This keeps the local and Docker configs byte-identical: no path rewriting.
ARG HOST_HOME=/Users/albertzhu
ENV HOME=${HOST_HOME} \
    OPENCLAW_GATEWAY_PORT=18789 \
    NODE_ENV=production
RUN mkdir -p ${HOST_HOME}/.openclaw
WORKDIR ${HOST_HOME}/.openclaw

# Gateway WebSocket port.
EXPOSE 18789

COPY docker/entrypoint.sh /usr/local/bin/openclaw-entrypoint.sh
RUN chmod +x /usr/local/bin/openclaw-entrypoint.sh

# tini reaps zombies and forwards SIGTERM so `gateway run` shuts down cleanly.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/openclaw-entrypoint.sh"]
