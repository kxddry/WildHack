#!/usr/bin/env bash

. "$(dirname "$0")/_common.sh"

ensure_prereqs
load_env

log "Compose services"
compose ps

if ! postgres_ready; then
  warn "Postgres is not running yet — table counts unavailable."
  exit 0
fi

warehouse_rows="$(db_count warehouses)"
route_rows="$(db_count routes)"
history_rows="$(db_count route_status_history)"
forecast_rows="$(db_count forecasts)"
request_rows="$(db_count transport_requests)"

log ""
log "Seeded table counts"
printf "  %-22s %s\n" "warehouses" "$warehouse_rows"
printf "  %-22s %s\n" "routes" "$route_rows"
printf "  %-22s %s\n" "route_status_history" "$history_rows"
printf "  %-22s %s\n" "forecasts" "$forecast_rows"
printf "  %-22s %s\n" "transport_requests" "$request_rows"
