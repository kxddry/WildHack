#!/usr/bin/env bash
# Seed data + run pipeline locally — same as judge-up does in Docker.
#
# Assumes:
#   - make db-init already ran (postgres + migrations)
#   - make up already ran (services healthy)
#
# Steps:
#   1. Seed route_status_history from parquet (if empty)
#   2. Historical replay (multiple pipeline ticks)
#   3. Backfill actuals
#   4. Final pipeline tick

. "$(dirname "$0")/_common.sh"

# Load .env for tokens
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  . "$REPO_ROOT/.env"
  set +a
fi

INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:-}"
STEP_INTERVAL_MINUTES="${STEP_INTERVAL_MINUTES:-30}"
DEMO_REPLAY_ANCHOR_COUNT="${DEMO_REPLAY_ANCHOR_COUNT:-5}"
DEMO_REPLAY_SPACING_HOURS="${DEMO_REPLAY_SPACING_HOURS:-5}"
DEMO_REPLAY_LATEST_OFFSET_HOURS="${DEMO_REPLAY_LATEST_OFFSET_HOURS:-7}"

SCH_URL="http://127.0.0.1:8002"
RETRAIN_URL="http://127.0.0.1:8003"

# ── helpers ──────────────────────────────────────────────────────────

db_count() {
  psql -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -Atqc \
    "SELECT COUNT(*) FROM $1;" 2>/dev/null | tr -d '[:space:]'
}

db_query_value() {
  psql -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -Atqc \
    "$1" 2>/dev/null | tr -d '[:space:]'
}

seed_history() {
  local dataset="$REPO_ROOT/Data/raw/train_team_track.parquet"
  [ -f "$dataset" ] || die "Bootstrap dataset not found at $dataset"

  log "Seeding route_status_history from parquet..."
  PYTHONPATH="$REPO_ROOT/shared:${PYTHONPATH:-}" \
  DATABASE_URL="$DATABASE_URL_SYNC" \
    python3 "$REPO_ROOT/scripts/seed_status_history.py" \
    || die "History seed failed"
  ok "History seeded"
}

trigger_pipeline() {
  local reference_ts="${1:-}"
  [ -n "$INTERNAL_API_TOKEN" ] || die "INTERNAL_API_TOKEN is empty in .env"

  local url="$SCH_URL/pipeline/trigger"
  if [ -n "$reference_ts" ]; then
    url="${url}?reference_ts=${reference_ts}"
    log "Pipeline trigger for reference_ts=$reference_ts"
  else
    log "Pipeline trigger (current time)"
  fi

  curl -fsS -X POST -H "X-Internal-Token: $INTERNAL_API_TOKEN" "$url" >/dev/null \
    || die "Pipeline trigger failed"
  ok "Pipeline tick completed"
}

historical_replay_anchors() {
  local step_seconds=$((STEP_INTERVAL_MINUTES * 60))
  local oldest_offset_hours=$(((DEMO_REPLAY_ANCHOR_COUNT - 1) * DEMO_REPLAY_SPACING_HOURS))

  psql -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -Atqc "
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

backfill_actuals() {
  log "Running backfill..."
  local venv="$REPO_ROOT/services/scheduler-service/.venv"
  [ -d "$venv" ] || die "scheduler-service venv missing"

  DATABASE_URL="$DATABASE_URL_ASYNC" \
  PYTHONPATH="$REPO_ROOT/shared:${PYTHONPATH:-}" \
  "$venv/bin/python" - <<'PY' || die "Backfill failed"
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
        t = await backfill_target_2h()
        r = await backfill_transport_request_actuals(settings.step_interval_minutes)
        print(f"Backfilled: target_2h={t} requests={r}")
    finally:
        await close_engine()

asyncio.run(main())
PY
  ok "Backfill done"
}

# ── main ─────────────────────────────────────────────────────────────

# 1. Seed if empty
history_rows="$(db_count route_status_history 2>/dev/null || echo 0)"
if [ "${history_rows:-0}" -eq 0 ]; then
  seed_history
  did_seed=1
else
  log "route_status_history already has $history_rows rows — skipping seed"
  did_seed=0
fi

# 2. Historical replay
if [ "$did_seed" -eq 1 ]; then
  mapfile -t anchors < <(historical_replay_anchors)
  if [ "${#anchors[@]}" -gt 0 ]; then
    log "Replaying ${#anchors[@]} historical anchors..."
    for ts in "${anchors[@]}"; do
      trigger_pipeline "$ts"
    done
  fi
fi

# 3. Backfill actuals
(cd "$REPO_ROOT/services/scheduler-service" && backfill_actuals)

# 4. Final pipeline tick
trigger_pipeline

# 5. Summary
forecast_rows="$(db_count forecasts 2>/dev/null || echo 0)"
request_rows="$(db_count transport_requests 2>/dev/null || echo 0)"
history_rows="$(db_count route_status_history 2>/dev/null || echo 0)"

ok "Fill complete"
log "Counts: history=$history_rows forecasts=$forecast_rows transport_requests=$request_rows"
