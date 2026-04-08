#!/usr/bin/env bash
# One-time local-dev setup. Creates per-service Python venvs, installs
# dependencies, installs the dashboard node modules, and prepares a dedicated
# E2E venv with Playwright + Chromium.
#
# Idempotent: safe to re-run whenever a requirements.txt or package-lock.json
# changes. Existing venvs are reused; pip/uv will only download what is
# actually missing.

. "$(dirname "$0")/_common.sh"

log "Repo root:   $REPO_ROOT"
log "Py installer: $PY_INSTALLER"

# ── 1. Host prerequisites ────────────────────────────────────────────
PYTHON=$(pick_python) || die "Python 3.11+ not found. Install via 'brew install python@3.12' or your package manager."
log "Python: $PYTHON ($($PYTHON --version))"

command -v node >/dev/null 2>&1 || die "Node.js not found. Install via 'brew install node@22' or 'fnm install 22'."
command -v npm  >/dev/null 2>&1 || die "npm not found (should ship with Node)."
log "Node:   $(node --version)   npm: $(npm --version)"

if ! command -v psql >/dev/null 2>&1; then
  warn "psql client not found — db-init.sh will fail until you install Postgres."
  warn "  macOS:   brew install postgresql@16 && brew services start postgresql@16"
  warn "  Debian:  sudo apt install postgresql && sudo service postgresql start"
fi

# ── 2. Per-service Python venvs ──────────────────────────────────────
# We can't share a single venv because prediction/dispatcher pin numpy 1.26
# and pydantic 2.9 while scheduler/retraining want numpy 2.2 and pydantic 2.11.
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name port kind <<< "$entry"
  [ "$kind" = python ] || continue

  svc_dir="$REPO_ROOT/services/$name"
  venv_dir="$svc_dir/.venv"
  reqs="$svc_dir/requirements.txt"

  log "Setting up $name venv"
  if [ ! -d "$venv_dir" ]; then
    if [ "$PY_INSTALLER" = uv ]; then
      uv venv --python "$PYTHON" "$venv_dir" >/dev/null
    else
      "$PYTHON" -m venv "$venv_dir"
    fi
  fi

  if [ "$PY_INSTALLER" = uv ]; then
    uv pip install --python "$venv_dir/bin/python" -r "$reqs"
  else
    "$venv_dir/bin/pip" install --upgrade pip wheel >/dev/null
    "$venv_dir/bin/pip" install -r "$reqs"
  fi
  ok "$name venv ready"
done

# ── 3. Dashboard node modules ────────────────────────────────────────
# Cheap stamp-file check — re-install only when package-lock.json is newer
# than the stamp. Blunt but sufficient for local dev.
DASH_DIR="$REPO_ROOT/services/dashboard-next"
STAMP="$DASH_DIR/node_modules/.local-setup-stamp"
if [ ! -f "$STAMP" ] || [ "$DASH_DIR/package-lock.json" -nt "$STAMP" ]; then
  log "Installing dashboard node modules"
  ( cd "$DASH_DIR" && npm install --no-audit --fund=false )
  touch "$STAMP"
  ok "dashboard node_modules ready"
else
  ok "dashboard node_modules up to date"
fi

# ── 4. E2E test venv ─────────────────────────────────────────────────
# Kept outside services/ so Playwright + Chromium don't bloat any production
# image if someone later decides to bake these venvs into CI containers.
E2E_VENV="$REPO_ROOT/.local/venv-e2e"
log "Setting up E2E venv at $E2E_VENV"
if [ ! -d "$E2E_VENV" ]; then
  "$PYTHON" -m venv "$E2E_VENV"
fi
"$E2E_VENV/bin/pip" install --upgrade pip wheel >/dev/null
"$E2E_VENV/bin/pip" install pytest httpx playwright >/dev/null
ok "E2E venv ready"

PW_DIR="$REPO_ROOT/.local/playwright-browsers"
if [ ! -d "$PW_DIR" ] || [ -z "$(ls -A "$PW_DIR" 2>/dev/null)" ]; then
  log "Installing Playwright Chromium (first run can take ~1 min)"
  PLAYWRIGHT_BROWSERS_PATH="$PW_DIR" "$E2E_VENV/bin/playwright" install chromium
fi
ok "Playwright chromium ready"

ok "Setup complete."
log "Next steps:"
log "  1. Ensure Postgres is running on ${PG_HOST}:${PG_PORT}"
log "  2. make db-init     # or ./scripts/local/db-init.sh"
log "  3. make up          # or ./scripts/local/up.sh"
log "  4. make e2e         # run end-to-end tests"
