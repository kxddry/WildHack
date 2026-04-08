#!/usr/bin/env bash
# End-to-end runner for the local (non-docker) stack.
#
# Default flow:
#   1. Apply database schema (idempotent)
#   2. Start the full stack (no-op if already running)
#   3. Run pytest tests/e2e/
#   4. Tear the stack down on exit
#
# Flags:
#   --keep       Leave the stack running after tests (good for iterating)
#   --no-up      Skip up.sh — assume the stack is already running
#   --no-db      Skip db-init.sh — assume schema already applied
#   --smoke      Run only tests/e2e/test_smoke.py (no Playwright required)
#   --dashboard  Run only tests/e2e/test_dashboard_e2e.py
#   any other arg is forwarded verbatim to pytest
#
# Exit code: pytest's exit code (0 == all green).

. "$(dirname "$0")/_common.sh"

KEEP=0
NO_UP=0
NO_DB=0
PYTEST_TARGET="$REPO_ROOT/tests/e2e"
EXTRA_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --keep)      KEEP=1; shift;;
    --no-up)     NO_UP=1; shift;;
    --no-db)     NO_DB=1; shift;;
    --smoke)     PYTEST_TARGET="$REPO_ROOT/tests/e2e/test_smoke.py"; shift;;
    --dashboard) PYTEST_TARGET="$REPO_ROOT/tests/e2e/test_dashboard_e2e.py"; shift;;
    *)           EXTRA_ARGS+=("$1"); shift;;
  esac
done

E2E_VENV="$REPO_ROOT/.local/venv-e2e"
[ -d "$E2E_VENV" ] || die "E2E venv missing — run './scripts/local/setup.sh' first."

if [ "$NO_DB" -eq 0 ]; then
  log "Ensuring database schema is in place"
  "$REPO_ROOT/scripts/local/db-init.sh"
fi

if [ "$NO_UP" -eq 0 ]; then
  log "Bringing the stack up"
  "$REPO_ROOT/scripts/local/up.sh"
fi

cleanup() {
  local ec=$?
  if [ "$KEEP" -eq 0 ] && [ "$NO_UP" -eq 0 ]; then
    log "Tearing the stack down (use --keep to leave it running)"
    "$REPO_ROOT/scripts/local/down.sh" || true
  fi
  exit "$ec"
}
trap cleanup EXIT

log "Running pytest: $PYTEST_TARGET ${EXTRA_ARGS[*]:-}"
PLAYWRIGHT_BROWSERS_PATH="$REPO_ROOT/.local/playwright-browsers" \
  "$E2E_VENV/bin/pytest" -v "$PYTEST_TARGET" "${EXTRA_ARGS[@]}"
