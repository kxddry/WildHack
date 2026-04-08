#!/usr/bin/env bash

. "$(dirname "$0")/_common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/judge/up.sh [--fresh]

  --fresh   Stop the stack and drop the Postgres volume before bootstrapping.
EOF
}

fresh=0
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh)
      fresh=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Unknown argument: $1"
      ;;
  esac
  shift
done

ensure_prereqs
load_env

if [ "$fresh" -eq 1 ]; then
  warn "--fresh requested: stopping stack and deleting Postgres volume"
  compose down -v --remove-orphans
fi

log "Starting full Docker stack"
compose up -d --build

wait_http "http://127.0.0.1:${PREDICTION_PORT}/health" "prediction-service" 240
wait_http "http://127.0.0.1:${DISPATCHER_PORT}/health" "dispatcher-service" 240
wait_http "http://127.0.0.1:${SCHEDULER_PORT}/health" "scheduler-service" 240
wait_http "http://127.0.0.1:${RETRAINING_PORT}/health" "retraining-service" 240
wait_http "http://127.0.0.1:${DASHBOARD_PORT}/" "dashboard" 240

history_rows="$(db_count route_status_history)"
warehouse_rows="$(db_count warehouses)"
route_rows="$(db_count routes)"
forecast_rows="$(db_count forecasts)"
request_rows="$(db_count transport_requests)"

if [ "${history_rows:-0}" -eq 0 ] || [ "${warehouse_rows:-0}" -eq 0 ] || [ "${route_rows:-0}" -eq 0 ]; then
  bootstrap_dataset >/dev/null
  history_rows="$(db_count route_status_history)"
  warehouse_rows="$(db_count warehouses)"
  route_rows="$(db_count routes)"
  forecast_rows="$(db_count forecasts)"
  request_rows="$(db_count transport_requests)"
fi

if [ "${forecast_rows:-0}" -eq 0 ] || [ "${request_rows:-0}" -eq 0 ]; then
  trigger_pipeline >/dev/null
fi

ready=0
for _ in $(seq 1 30); do
  forecast_rows="$(db_count forecasts)"
  request_rows="$(db_count transport_requests)"
  if [ "${forecast_rows:-0}" -gt 0 ] && [ "${request_rows:-0}" -gt 0 ]; then
    ready=1
    break
  fi
  sleep 2
done

[ "$ready" -eq 1 ] || die "Stack is up, but demo tables are still empty. Check 'make judge-status' and compose logs."

ok "Judge/demo stack is ready"
log "Counts: warehouses=${warehouse_rows} routes=${route_rows} history=${history_rows} forecasts=${forecast_rows} transport_requests=${request_rows}"
log ""
print_urls >&2
log ""
log "Next commands:"
log "  make judge-status   # inspect services + seeded table counts"
log "  make judge-down     # stop the stack"
