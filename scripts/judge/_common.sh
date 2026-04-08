#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
COMPOSE_FILE="$REPO_ROOT/infrastructure/docker-compose.yml"
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/.env.example"

if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_BLUE=$'\033[34m'
else
  C_RESET=''
  C_GREEN=''
  C_YELLOW=''
  C_RED=''
  C_BLUE=''
fi

log()  { printf "%s[judge]%s %s\n" "$C_BLUE" "$C_RESET" "$*" >&2; }
ok()   { printf "%s[ ok ]%s %s\n" "$C_GREEN" "$C_RESET" "$*" >&2; }
warn() { printf "%s[warn]%s %s\n" "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf "%s[fail]%s %s\n" "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

compose() {
  (
    cd "$REPO_ROOT"
    docker compose -f "$COMPOSE_FILE" "$@"
  )
}

ensure_prereqs() {
  command -v docker >/dev/null 2>&1 \
    || die "docker not found in PATH. Install Docker Desktop, Colima, or OrbStack."
  docker compose version >/dev/null 2>&1 \
    || die "'docker compose' subcommand not available. Upgrade Docker to Compose v2."
  command -v curl >/dev/null 2>&1 \
    || die "curl not found in PATH."
}

ensure_env_file() {
  if [ -f "$ENV_FILE" ]; then
    return 0
  fi
  [ -f "$ENV_EXAMPLE" ] || die ".env.example not found at $ENV_EXAMPLE"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  ok "Created .env from .env.example"
}

load_env() {
  ensure_env_file
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a

  POSTGRES_DB="${POSTGRES_DB:-wildhack}"
  POSTGRES_USER="${POSTGRES_USER:-wildhack}"
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-wildhack_dev}"
  POSTGRES_PORT="${POSTGRES_PORT:-5432}"

  PREDICTION_PORT="${PREDICTION_PORT:-8000}"
  DISPATCHER_PORT="${DISPATCHER_PORT:-8001}"
  SCHEDULER_PORT="${SCHEDULER_PORT:-8002}"
  RETRAINING_PORT="${RETRAINING_PORT:-8003}"
  DASHBOARD_PORT="${DASHBOARD_PORT:-4000}"
  PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
  GRAFANA_PORT="${GRAFANA_PORT:-3001}"
  STEP_INTERVAL_MINUTES="${STEP_INTERVAL_MINUTES:-30}"
  DEMO_REPLAY_ANCHOR_COUNT="${DEMO_REPLAY_ANCHOR_COUNT:-5}"
  DEMO_REPLAY_SPACING_HOURS="${DEMO_REPLAY_SPACING_HOURS:-5}"
  DEMO_REPLAY_LATEST_OFFSET_HOURS="${DEMO_REPLAY_LATEST_OFFSET_HOURS:-7}"

  DATA_INGEST_TOKEN="${DATA_INGEST_TOKEN:-}"
  INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:-}"
}

wait_http() {
  local url="$1"
  local label="$2"
  local timeout="${3:-180}"
  local elapsed=0

  while [ "$elapsed" -lt "$timeout" ]; do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      ok "$label healthy ($url)"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  die "$label did not become healthy within ${timeout}s ($url)"
}

postgres_ready() {
  compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1
}

db_count() {
  local table="$1"
  compose exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT COUNT(*) FROM ${table};" \
    | tr -d '[:space:]'
}

db_query_value() {
  local sql="$1"
  compose exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "$sql" \
    | tr -d '[:space:]'
}

bootstrap_dataset() {
  local dataset="$REPO_ROOT/Data/raw/train_team_track.parquet"
  [ -f "$dataset" ] || die "Bootstrap dataset not found at $dataset"

  log "Seeding bundled Team Track history snapshot"
  local sync_database_url="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}"
  compose run --rm --no-deps -T \
    -e "DATABASE_URL=${sync_database_url}" \
    -v "$REPO_ROOT:/workspace:ro" \
    -w /workspace \
    retraining-service \
    python scripts/seed_status_history.py \
    || die "History bootstrap failed"
  ok "History snapshot seeded"
}

trigger_pipeline() {
  local reference_ts="${1:-}"
  [ -n "$INTERNAL_API_TOKEN" ] || die "INTERNAL_API_TOKEN is empty in .env"

  local url="http://127.0.0.1:${SCHEDULER_PORT}/pipeline/trigger"
  if [ -n "$reference_ts" ]; then
    url="${url}?reference_ts=${reference_ts}"
    log "Triggering prediction and dispatch cycle for reference_ts=${reference_ts}"
  else
    log "Triggering prediction and dispatch cycle"
  fi

  local response
  response=$(
    curl -fsS \
      -X POST \
      -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
      "$url"
  ) || die "Pipeline trigger failed"
  ok "Prediction + dispatch cycle completed"
  printf "%s\n" "$response"
}

historical_reference_ts() {
  local step_seconds=$((STEP_INTERVAL_MINUTES * 60))
  db_query_value "
    SELECT to_char(
      TIMESTAMP 'epoch'
      + FLOOR(
          EXTRACT(EPOCH FROM (MAX(timestamp) - INTERVAL '${DEMO_REPLAY_LATEST_OFFSET_HOURS} hours'))
          / ${step_seconds}
        ) * ${step_seconds} * INTERVAL '1 second',
      'YYYY-MM-DD\"T\"HH24:MI:SS'
    )
    FROM route_status_history;
  "
}

historical_replay_anchors() {
  local step_seconds=$((STEP_INTERVAL_MINUTES * 60))
  local oldest_offset_hours=$(((DEMO_REPLAY_ANCHOR_COUNT - 1) * DEMO_REPLAY_SPACING_HOURS))

  compose exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "
      WITH latest AS (
        SELECT
          TIMESTAMP 'epoch'
          + FLOOR(
              EXTRACT(EPOCH FROM (MAX(timestamp) - INTERVAL '${DEMO_REPLAY_LATEST_OFFSET_HOURS} hours'))
              / ${step_seconds}
            ) * ${step_seconds} * INTERVAL '1 second' AS latest_anchor
        FROM route_status_history
      ),
      anchors AS (
        SELECT latest_anchor - INTERVAL '${oldest_offset_hours} hours' AS oldest_anchor
        FROM latest
      )
      SELECT to_char(
        oldest_anchor + (gs.n * ${DEMO_REPLAY_SPACING_HOURS}) * INTERVAL '1 hour',
        'YYYY-MM-DD\"T\"HH24:MI:SS'
      )
      FROM anchors
      CROSS JOIN generate_series(0, ${DEMO_REPLAY_ANCHOR_COUNT} - 1) AS gs(n)
      WHERE oldest_anchor IS NOT NULL
      ORDER BY gs.n;
    "
}

trigger_backfill() {
  log "Running immediate scheduler backfill for actuals"
  compose exec -T scheduler-service python - <<'PY' || die "Immediate backfill failed"
import asyncio

from app.config import settings
from app.storage.postgres import (
    backfill_target_2h,
    backfill_transport_request_actuals,
    close_engine,
    create_engine_pool,
)


async def main() -> None:
    await create_engine_pool(settings.database_url)
    try:
        target_rows = await backfill_target_2h()
        request_rows = await backfill_transport_request_actuals(
            settings.step_interval_minutes
        )
        print(
            {
                "status": "ok",
                "target_rows_updated": target_rows,
                "request_rows_updated": request_rows,
                "rows_updated": target_rows + request_rows,
            }
        )
    finally:
        await close_engine()


asyncio.run(main())
PY
  ok "Immediate backfill completed"
}

request_status_count() {
  local status="$1"
  db_query_value "SELECT COUNT(*) FROM transport_requests WHERE status = '${status}';"
}

actual_backfilled_count() {
  db_query_value "SELECT COUNT(*) FROM transport_requests WHERE actual_vehicles IS NOT NULL;"
}

print_urls() {
  cat <<EOF
Dashboard:   http://localhost:${DASHBOARD_PORT}
Prediction:  http://localhost:${PREDICTION_PORT}/docs
Dispatcher:  http://localhost:${DISPATCHER_PORT}/docs
Scheduler:   http://localhost:${SCHEDULER_PORT}/docs
Retraining:  http://localhost:${RETRAINING_PORT}/docs
Prometheus:  http://localhost:${PROMETHEUS_PORT}
Grafana:     http://localhost:${GRAFANA_PORT}
EOF
}
