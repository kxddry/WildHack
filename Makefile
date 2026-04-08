# WildHack — local-dev convenience targets (no Docker required).
#
# Typical first-time workflow:
#   make setup     # install venvs + node modules + Playwright Chromium
#   make db-init   # create wildhack role + DB + schema (needs local Postgres)
#   make e2e       # full end-to-end: up → pytest → down
#
# Iterate without restarting every time:
#   make up && make e2e-keep    # leaves stack running for re-runs
#   make status                 # check what's alive
#   make logs                   # tail all service logs
#   make down                   # stop everything

SHELL := /usr/bin/env bash
SCRIPTS := scripts/local

.PHONY: help setup db-init up down status logs e2e e2e-keep e2e-smoke e2e-dashboard clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## One-time: create per-service venvs, install deps, install Playwright
	@$(SCRIPTS)/setup.sh

db-init: ## Create wildhack role/DB, apply init.sql + migrations
	@$(SCRIPTS)/db-init.sh

up: ## Start the full stack (4 FastAPI services + Next.js dashboard)
	@$(SCRIPTS)/up.sh

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

clean: ## Remove venvs and run-state (does NOT drop the database)
	@$(SCRIPTS)/down.sh || true
	@rm -rf .local services/prediction-service/.venv services/dispatcher-service/.venv \
	        services/scheduler-service/.venv services/retraining-service/.venv \
	        services/dashboard-next/node_modules/.local-setup-stamp
	@echo "Cleaned local venvs, run state, and Playwright browsers."
