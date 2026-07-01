.PHONY: up up-logs down restart cold-restart docker force logs roll sync pre gog gog-all help

# Resolve the workspace root the same way the gateway does: from OPENCLAW_WORKSPACE_ROOT
# in .env (the single knob that relocates all workspaces), falling back to ~/.openclaw.
# .env is located relative to this Makefile so the targets work from any cwd.
MAKEFILE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
OPENCLAW_WORKSPACE_ROOT := $(shell sed -n 's/^OPENCLAW_WORKSPACE_ROOT=//p' $(MAKEFILE_DIR)/.env 2>/dev/null | tr -d '"' | tr -d "'" | tail -1)
ifeq ($(strip $(OPENCLAW_WORKSPACE_ROOT)),)
OPENCLAW_WORKSPACE_ROOT := $(HOME)/.openclaw
endif

help:
	@echo "up           - start the gateway (returns immediately)"
	@echo "up-logs      - start the gateway, then follow logs"
	@echo "update       - check for new OpenClaw updates"
	@echo "down         - stop the gateway"
	@echo "restart      - sync, stop, then start + follow logs"
	@echo "cold-restart - sync, stop, then start (no log follow)"
	@echo "docker       - stop host gateway, build + run the container (follows logs)"
	@echo "force        - reinstall + reload the launchd service (fixes 'service not loaded')"
	@echo "logs         - follow gateway logs"
	@echo "roll         - rotate the gateway auth token"
	@echo "sync         - sync .env keys into service-env"
	@echo "pre          - run portfolio-check pre step"
	@echo "gog          - run gog for EMAIL=<email>"
	@echo "gog-all      - run gog for GOG_EMAILS=<emails>"

up:
	@openclaw gateway start

up-logs: up
	@$(MAKE) logs

update:
	@openclaw update

down:
	@openclaw gateway stop

restart: sync down update up-logs

cold-restart: sync down update up

docker:
	@echo "stopping host gateway first (avoid double-writing ~/.openclaw)…"
	@openclaw gateway stop 2>/dev/null || true
	@$(MAKE) -f docker/docker.mk d-build
	@$(MAKE) -f docker/docker.mk d-up-logs

force:
	@openclaw gateway install --force

logs:
	@openclaw logs --local-time --follow

roll:
	@bash scripts/roll-gateway-token.sh

sync:
	@python3 scripts/sync-env.sh

pre:
	$(MAKE) -C $(OPENCLAW_WORKSPACE_ROOT)/workspace-sigma/Skills/portfolio-check pre

gog:
	$(MAKE) -C $(OPENCLAW_WORKSPACE_ROOT)/workspace gog EMAIL=$(EMAIL)

gog-all:
	$(MAKE) -C $(OPENCLAW_WORKSPACE_ROOT)/workspace gog-all GOG_EMAILS="$(GOG_EMAILS)"
