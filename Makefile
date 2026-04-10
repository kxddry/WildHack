# WildHack — local-dev + judge/demo convenience targets.
#
# Postgres + db-migrate run inside docker-compose (the only pieces that need
# containers). Every application service (prediction, dispatcher, scheduler,
# retraining, dashboard) runs on the host via scripts/local/*.sh — which
# means fast reloads, native debugging, and no image rebuilds.
#
# Separate judge/demo targets use Docker for the whole stack and auto-seed the
# bundled Team Track snapshot so the dashboard is non-empty after one command:
#   make judge-up
#   make judge-status
#   make judge-down
#
# Typical first-time workflow:
#   make setup     # install venvs + node modules + Playwright
#   make db-init   # docker compose up postgres + apply migrations
#   make e2e       # full end-to-end: up → pytest → down
#
# Iterate without restarting every time:
#   make up && make e2e-keep    # leaves host stack running for re-runs
#   make status                 # check what's alive
#   make logs                   # tail all service logs
#   make down                   # stop host services (postgres stays up)
#   make db-down                # stop postgres container too
#   make db-reset                # drop postgres volume and re-apply schema

SHELL := /usr/bin/env bash
SCRIPTS := scripts/local
COMPOSE := docker compose -f infrastructure/docker-compose.yml

.PHONY: help setup db-init db-down db-reset up down status logs e2e e2e-keep e2e-smoke e2e-dashboard judge-up judge-fresh judge-status judge-down clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## One-time: create per-service venvs, install deps, install Playwright
	@$(SCRIPTS)/setup.sh

db-init: ## Start postgres container + apply migrations (one-shot db-migrate)
	@$(SCRIPTS)/db-init.sh

db-down: ## Stop the postgres container (data volume is preserved)
	@$(COMPOSE) stop postgres

db-reset: ## Drop postgres volume + re-apply schema (DESTROYS data)
	@$(COMPOSE) down -v postgres
	@$(SCRIPTS)/db-init.sh

up: ## Start the 4 FastAPI services + Next.js dashboard on the host
	@$(SCRIPTS)/up.sh

fill: ## Seed data + run pipeline locally (like judge-up but without Docker)
	@$(SCRIPTS)/fill.sh

down: ## Stop everything started by `make up`
	@$(SCRIPTS)/down.sh

status: ## Show pid + port status for every service
	@$(SCRIPTS)/status.sh

logs: ## Tail every service log
	@tail -f .local/run/*.log

e2e: ## Full E2E: db-init → up → pytest → down
	@$(SCRIPTS)/e2e.sh

e2e-keep: ## Like e2e but leaves the stack running afterwards
	@$(SCRIPTS)/e2e.sh --keep

e2e-smoke: ## Only run tests/e2e/test_smoke.py (no Playwright)
	@$(SCRIPTS)/e2e.sh --smoke

e2e-dashboard: ## Only run the Playwright dashboard tests
	@$(SCRIPTS)/e2e.sh --dashboard

judge-up: ## Docker-only judge/demo startup with auto-bootstrap of demo data
	@./scripts/judge/up.sh

judge-fresh: ## Same as judge-up, but resets Postgres volume first
	@./scripts/judge/up.sh --fresh

judge-status: ## Show docker service state + seeded table counts
	@./scripts/judge/status.sh

judge-down: ## Stop the dockerized judge/demo stack
	@./scripts/judge/down.sh

clean: ## Remove host venvs + run state. Stops postgres. Preserves DB volume.
	@$(SCRIPTS)/down.sh || true
	@$(COMPOSE) stop postgres 2>/dev/null || true
	@rm -rf .local services/prediction-service/.venv services/dispatcher-service/.venv \
	        services/scheduler-service/.venv services/retraining-service/.venv \
	        services/dashboard-next/node_modules/.local-setup-stamp
	@echo "Cleaned host venvs, run state, and Playwright browsers."
	@echo "Postgres data volume is preserved — use 'make db-reset' to wipe it."
