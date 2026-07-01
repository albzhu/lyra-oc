# Docker targets for the OpenClaw gateway.
# Use directly:        make -f docker/docker.mk d-up
# Or include from the main Makefile by adding:  include docker/docker.mk
#
# These mirror the launchd-based `make up/down/logs` flow but for the container.

COMPOSE ?= docker compose
OPENCLAW_HOME ?= $(HOME)/.openclaw
export OPENCLAW_HOME

.PHONY: d-build d-up d-up-logs d-down d-restart d-logs d-shell d-health d-pull-check d-help

d-help:
	@echo "d-build      - build the gateway image"
	@echo "d-up         - start the gateway container (detached)"
	@echo "d-up-logs    - start the container, then follow logs"
	@echo "d-down       - stop and remove the container"
	@echo "d-restart    - rebuild + recreate the container"
	@echo "d-logs       - follow container logs"
	@echo "d-shell      - open a shell inside the running container"
	@echo "d-health     - run the gateway health probe inside the container"
	@echo "d-pull-check - show installed vs latest openclaw version"

d-build:
	@$(COMPOSE) build

d-up:
	@$(COMPOSE) up -d

d-up-logs: d-up d-logs

d-down:
	@$(COMPOSE) down

d-restart:
	@$(COMPOSE) up -d --build

d-logs:
	@$(COMPOSE) logs -f gateway

d-shell:
	@$(COMPOSE) exec gateway bash

d-health:
	@$(COMPOSE) exec gateway sh -c 'openclaw gateway health --url ws://127.0.0.1:18789 --token "$$GATEWAY_AUTH_TOKEN" --timeout 5000'

# Compare the pinned image version against the latest published release.
d-pull-check:
	@echo "image pin (docker-compose.yml): $$(grep -m1 OPENCLAW_VERSION docker-compose.yml | tr -dc '0-9.')"
	@echo "latest on npm:                  $$(npm view openclaw version 2>/dev/null)"
