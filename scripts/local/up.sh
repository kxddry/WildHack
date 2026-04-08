#!/usr/bin/env bash
# Start the WildHack stack locally without Docker.
#
# Idempotent — skips any service whose pidfile still references a running
# process. Each service gets its own log in .local/run/<name>.log and its
# pidfile at .local/run/<name>.pid.
#
# To stop: ./scripts/local/down.sh

. "$(dirname "$0")/_common.sh"

# Service URLs as seen from the host. The docker-compose wiring gives each
# service a hostname like `prediction-service`; on bare metal those do not
# resolve, so every downstream service MUST receive the localhost equivalents
# via env vars (pydantic-settings env vars override the hardcoded defaults in
# each service's config.py).
PRED_URL="http://127.0.0.1:8000"
DISP_URL="http://127.0.0.1:8001"
SCH_URL="http://127.0.0.1:8002"
RETRAIN_URL="http://127.0.0.1:8003"
# Dashboard port MUST stay at 4000 — tests/e2e/test_smoke.py and
# test_dashboard_e2e.py hardcode http://localhost:4000.
DASH_PORT=4000

start_python() {
  local name="$1"
  local port="$2"
  local svc_dir="$REPO_ROOT/services/$name"
  local venv="$svc_dir/.venv"
  local pidfile="$RUN_DIR/$name.pid"
  local logfile="$RUN_DIR/$name.log"

  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    warn "$name already running (pid $(cat "$pidfile")) — skipping"
    return 0
  fi
  rm -f "$pidfile"

  port_in_use "$port" && die "$name port $port already occupied by another process"
  [ -d "$venv" ] || die "$name venv missing — run ./scripts/local/setup.sh first"

  log "Starting $name on :$port"

  # Run in a subshell so the `cd` doesn't leak to the parent, and all env
  # overrides are scoped to this service only. `nohup` makes the child
  # ignore SIGHUP so it survives the parent shell exiting.
  (
    cd "$svc_dir"
    DATABASE_URL="$DATABASE_URL_ASYNC" \
    SYNC_DATABASE_URL="$DATABASE_URL_SYNC" \
    PREDICTION_SERVICE_URL="$PRED_URL" \
    DISPATCHER_SERVICE_URL="$DISP_URL" \
    RETRAINING_SERVICE_URL="$RETRAIN_URL" \
    MODEL_PATH="$REPO_ROOT/models/model.pkl" \
    STATIC_AGGS_PATH="$REPO_ROOT/models/static_aggs.json" \
    FILL_VALUES_PATH="$REPO_ROOT/models/fill_values.json" \
    MODEL_OUTPUT_DIR="$REPO_ROOT/models" \
    MOCK_MODE="${MOCK_MODE:-0}" \
    TRUCK_CAPACITY="${TRUCK_CAPACITY:-33}" \
    BUFFER_PCT="${BUFFER_PCT:-0.10}" \
    MIN_TRUCKS="${MIN_TRUCKS:-1}" \
    ADAPTIVE_BUFFER="${ADAPTIVE_BUFFER:-false}" \
    MIN_BUFFER_PCT="${MIN_BUFFER_PCT:-0.05}" \
    MAX_BUFFER_PCT="${MAX_BUFFER_PCT:-0.25}" \
    PREDICTION_INTERVAL_MINUTES="${PREDICTION_INTERVAL_MINUTES:-30}" \
    QUALITY_CHECK_INTERVAL_MINUTES="${QUALITY_CHECK_INTERVAL_MINUTES:-60}" \
    TRAINING_WINDOW_DAYS="${TRAINING_WINDOW_DAYS:-7}" \
    PYTHONUNBUFFERED=1 \
    nohup "$venv/bin/uvicorn" "app.main:app" \
      --host 127.0.0.1 --port "$port" \
      >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  )
  ok "$name pid $(cat "$pidfile") — log: $logfile"
}

start_dashboard() {
  local name="dashboard-next"
  local svc_dir="$REPO_ROOT/services/$name"
  local pidfile="$RUN_DIR/$name.pid"
  local logfile="$RUN_DIR/$name.log"

  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    warn "$name already running (pid $(cat "$pidfile")) — skipping"
    return 0
  fi
  rm -f "$pidfile"

  port_in_use "$DASH_PORT" && die "dashboard port $DASH_PORT already occupied"
  [ -d "$svc_dir/node_modules" ] || die "dashboard node_modules missing — run setup.sh"

  log "Starting $name on :$DASH_PORT"
  (
    cd "$svc_dir"
    DATABASE_URL="postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
    PREDICTION_SERVICE_URL="$PRED_URL" \
    DISPATCHER_SERVICE_URL="$DISP_URL" \
    nohup npx --no-install next dev --turbopack \
      --port "$DASH_PORT" --hostname 127.0.0.1 \
      >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  )
  ok "$name pid $(cat "$pidfile") — log: $logfile"
}

# Boot order + health gates: prediction must be serving before anything that
# talks to it starts importing it as a dependency.
start_python prediction-service 8000
wait_http "$PRED_URL/health" "prediction-service" 60

start_python dispatcher-service 8001
wait_http "$DISP_URL/health" "dispatcher-service" 60

start_python scheduler-service 8002
wait_http "$SCH_URL/health" "scheduler-service" 60

start_python retraining-service 8003
wait_http "$RETRAIN_URL/health" "retraining-service" 60

start_dashboard
# next dev is slower to warm up than uvicorn, give it extra time.
wait_http "http://127.0.0.1:$DASH_PORT/" "dashboard" 120

ok "Stack is up"
log ""
log "  Prediction  $PRED_URL/docs"
log "  Dispatcher  $DISP_URL/docs"
log "  Scheduler   $SCH_URL/docs"
log "  Retraining  $RETRAIN_URL/docs"
log "  Dashboard   http://127.0.0.1:$DASH_PORT"
log ""
log "  tail -f $RUN_DIR/*.log     # follow every log"
log "  ./scripts/local/down.sh    # stop the stack"
log "  ./scripts/local/e2e.sh     # run E2E tests"
