#!/usr/bin/env bash
# Stop every service started by up.sh. Reads pidfiles from .local/run/,
# sends SIGTERM, waits up to 10 s for a clean shutdown, then SIGKILL.
#
# Pidfiles with stale or dead PIDs are cleaned up silently.

. "$(dirname "$0")/_common.sh"

shopt -s nullglob
pidfiles=("$RUN_DIR"/*.pid)

if [ "${#pidfiles[@]}" -eq 0 ]; then
  warn "No pidfiles in $RUN_DIR — nothing to stop."
  exit 0
fi

for pidfile in "${pidfiles[@]}"; do
  name=$(basename "$pidfile" .pid)
  pid=$(cat "$pidfile" 2>/dev/null || true)

  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    warn "$name pid $pid not alive — cleaning pidfile"
    rm -f "$pidfile"
    continue
  fi

  log "Stopping $name (pid $pid)"
  kill "$pid" 2>/dev/null || true

  # Uvicorn + FastAPI handle SIGTERM cleanly via the lifespan context manager;
  # 10 s is generous. Anything still alive after that is buggy and gets
  # SIGKILL'd.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done

  if kill -0 "$pid" 2>/dev/null; then
    warn "$name did not exit on SIGTERM — sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done

ok "Stack stopped"
