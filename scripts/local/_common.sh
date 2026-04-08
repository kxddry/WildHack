#!/usr/bin/env bash
# Shared helpers for the local-dev / E2E orchestration scripts.
#
# Source from any sibling script:
#     . "$(dirname "$0")/_common.sh"
#
# Conventions
# -----------
#   REPO_ROOT   — resolved from this file's location (not the caller's CWD)
#   RUN_DIR     — .local/run, holds pidfiles and per-service logs
#   SERVICES    — ordered registry consumed by up.sh and status.sh
#
# Each Python service has its own venv at services/<name>/.venv/, created by
# setup.sh. Venvs are kept separate because pydantic 2.9 vs 2.11 and
# numpy 1.26 vs 2.2 across services cannot share a single environment.

set -eu -o pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
RUN_DIR="$REPO_ROOT/.local/run"
mkdir -p "$RUN_DIR"

# ANSI colors — suppressed when NO_COLOR is set or stderr is not a tty.
if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_BLUE=$'\033[34m'
else
  C_RESET=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BLUE=''
fi

log()  { printf "%s[local]%s %s\n" "$C_BLUE" "$C_RESET" "$*" >&2; }
ok()   { printf "%s[ ok ]%s %s\n" "$C_GREEN" "$C_RESET" "$*" >&2; }
warn() { printf "%s[warn]%s %s\n" "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf "%s[fail]%s %s\n" "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# Service registry. Format: name|port|kind (python|node)
# Boot order matters — each depends on prior entries being healthy.
SERVICES=(
  "prediction-service|8000|python"
  "dispatcher-service|8001|python"
  "scheduler-service|8002|python"
  "retraining-service|8003|python"
  "dashboard-next|4000|node"
)

# Database settings — align with .env.example and docker-compose defaults so
# existing E2E tests continue to work against the same DSNs.
PG_USER="${POSTGRES_USER:-wildhack}"
PG_PASSWORD="${POSTGRES_PASSWORD:-wildhack_dev}"
PG_DB="${POSTGRES_DB:-wildhack}"
PG_HOST="${POSTGRES_HOST:-127.0.0.1}"
PG_PORT="${POSTGRES_PORT:-5432}"

DATABASE_URL_ASYNC="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}"
DATABASE_URL_SYNC="postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}"

# Detect installer. uv is ~10× faster than pip for the first cold install and
# produces identical results, so use it when available.
if command -v uv >/dev/null 2>&1; then
  PY_INSTALLER=uv
else
  PY_INSTALLER=pip
fi

# Pick the system python — prefer 3.12, then 3.11, then plain python3.
# All service requirements.txt files are compatible with py3.11+.
pick_python() {
  for cand in python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}

# Poll an HTTP endpoint until it returns a 2xx response or the timeout hits.
wait_http() {
  local url="$1"
  local label="$2"
  local timeout="${3:-60}"
  local elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    if curl -fs --max-time 2 "$url" >/dev/null 2>&1; then
      ok "$label healthy ($url)"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  die "$label did not become healthy within ${timeout}s ($url)"
}

# True if a TCP port is currently bound on 127.0.0.1.
port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null && { exec 3<&-; return 0; }
    return 1
  fi
}
