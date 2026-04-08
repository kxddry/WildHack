#!/usr/bin/env bash

. "$(dirname "$0")/_common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/judge/down.sh [--volumes]

  --volumes   Also delete the Postgres volume (full reset).
EOF
}

drop_volumes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --volumes)
      drop_volumes=1
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

if [ "$drop_volumes" -eq 1 ]; then
  warn "Stopping stack and deleting Postgres volume"
  compose down -v --remove-orphans
else
  log "Stopping stack"
  compose down --remove-orphans
fi

ok "Judge/demo stack stopped"
