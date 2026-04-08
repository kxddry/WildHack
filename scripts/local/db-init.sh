#!/usr/bin/env bash
# Bring up Postgres + apply migrations via docker compose.
#
# In the hybrid local-dev setup, Postgres is the ONLY thing that stays
# containerized — every application service runs on the host via
# scripts/local/up.sh. This script is the bridge: it starts the postgres
# container, waits for it to be ready, then runs the one-shot db-migrate
# sidecar defined in infrastructure/docker-compose.yml.
#
# Idempotent. Re-running after schema changes applies any new migrations.

. "$(dirname "$0")/_common.sh"

COMPOSE_FILE="$REPO_ROOT/infrastructure/docker-compose.yml"

command -v docker >/dev/null 2>&1 \
  || die "docker not found in PATH. Install Docker Desktop, colima, or OrbStack."
docker compose version >/dev/null 2>&1 \
  || die "'docker compose' subcommand not available — update your Docker install."

log "Starting postgres container ($COMPOSE_FILE)"
docker compose -f "$COMPOSE_FILE" up -d postgres

# docker compose's --wait flag is v2+ only; fall back to an explicit
# pg_isready poll from inside the container so we don't have to special-case
# docker versions. The helper is guaranteed to exist — db-migrate uses the
# same postgres:16-alpine image.
log "Waiting for postgres to accept connections"
ready=0
for _ in $(seq 1 60); do
  if docker compose -f "$COMPOSE_FILE" exec -T postgres \
       pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" -eq 1 ] || die "postgres did not become ready within 60s — check 'docker compose logs postgres'"
ok "postgres is ready"

# docker compose run spawns a one-shot container using the db-migrate service
# definition (same image, same volume mounts, same entrypoint/command from
# docker-compose.yml). --rm removes it on exit so we don't pile up dead
# containers across re-runs.
log "Applying migrations via db-migrate sidecar"
docker compose -f "$COMPOSE_FILE" run --rm db-migrate

ok "Database ready at 127.0.0.1:${PG_PORT} — DSN: postgresql://${PG_USER}:***@${PG_HOST}:${PG_PORT}/${PG_DB}"
