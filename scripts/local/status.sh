#!/usr/bin/env bash
# Print a one-line-per-service status table. Useful for sanity-checking the
# stack before running E2E or after a flaky restart.

. "$(dirname "$0")/_common.sh"

printf "%-22s %-8s %-10s %-16s %s\n" SERVICE PID STATUS PORT HEALTH
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name port kind <<< "$entry"
  pidfile="$RUN_DIR/$name.pid"
  pid="-"
  status="stopped"

  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile" 2>/dev/null || echo "-")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      status="running"
    else
      status="stale"
    fi
  fi

  port_str="$port:closed"
  port_in_use "$port" && port_str="$port:open"

  health="-"
  if [ "$kind" = python ]; then
    if curl -fs --max-time 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      health="200"
    fi
  else
    if curl -fs --max-time 1 "http://127.0.0.1:$port/" >/dev/null 2>&1; then
      health="200"
    fi
  fi

  printf "%-22s %-8s %-10s %-16s %s\n" "$name" "$pid" "$status" "$port_str" "$health"
done
