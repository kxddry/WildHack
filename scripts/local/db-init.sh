#!/usr/bin/env bash
# Initialize the local Postgres database used by the WildHack stack.
#
# Prerequisites
# -------------
#   - Postgres is running on $PG_HOST:$PG_PORT (this script does NOT start it)
#   - psql is on PATH
#   - The current Unix user can connect to the 'postgres' maintenance DB with
#     superuser rights (the default for Homebrew installs). Override via
#     POSTGRES_SUPERUSER_DSN if that's not the case.
#
# Install Postgres first:
#   macOS:  brew install postgresql@16 && brew services start postgresql@16
#   Debian: sudo apt install postgresql && sudo service postgresql start
#
# Idempotent. Creates the wildhack role + database if missing, then applies
# init.sql and every file under infrastructure/postgres/migrations/ in
# lexical order.

. "$(dirname "$0")/_common.sh"

command -v psql >/dev/null 2>&1 || die "psql not found in PATH. Install Postgres first (see header of $0)."

# Default superuser DSN matches Homebrew on macOS (current user owns the
# cluster). On Debian/apt you'd typically `sudo -u postgres` — in that case
# set POSTGRES_SUPERUSER_DSN explicitly before running this script.
SUPER_DSN="${POSTGRES_SUPERUSER_DSN:-postgres://$(id -un)@${PG_HOST}:${PG_PORT}/postgres}"

log "Checking Postgres reachability at $PG_HOST:$PG_PORT"
if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
  die "Postgres not reachable at $PG_HOST:$PG_PORT. Start it first (see header of $0)."
fi

# ── Role ─────────────────────────────────────────────────────────────
log "Ensuring role '$PG_USER' exists"
role_exists=$(psql "$SUPER_DSN" -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" 2>/dev/null || true)
if [ -z "$role_exists" ]; then
  psql "$SUPER_DSN" -v ON_ERROR_STOP=1 -c \
    "CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASSWORD'" >/dev/null
  ok "role '$PG_USER' created"
else
  ok "role '$PG_USER' already exists"
fi

# ── Database ─────────────────────────────────────────────────────────
log "Ensuring database '$PG_DB' exists"
db_exists=$(psql "$SUPER_DSN" -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" 2>/dev/null || true)
if [ -z "$db_exists" ]; then
  psql "$SUPER_DSN" -v ON_ERROR_STOP=1 -c \
    "CREATE DATABASE $PG_DB OWNER $PG_USER" >/dev/null
  ok "database '$PG_DB' created"
else
  ok "database '$PG_DB' already exists"
fi

psql "$SUPER_DSN" -v ON_ERROR_STOP=1 -c \
  "GRANT ALL PRIVILEGES ON DATABASE $PG_DB TO $PG_USER" >/dev/null

# ── Schema + migrations (run as the app user, so ownership is correct) ─
APP_DSN="postgres://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}"

log "Applying infrastructure/postgres/init.sql"
PGPASSWORD="$PG_PASSWORD" psql "$APP_DSN" -v ON_ERROR_STOP=1 -q \
  -f "$REPO_ROOT/infrastructure/postgres/init.sql"
ok "init.sql applied"

log "Applying migrations"
count=0
for f in "$REPO_ROOT/infrastructure/postgres/migrations/"*.sql; do
  [ -e "$f" ] || continue
  log "  -> $(basename "$f")"
  PGPASSWORD="$PG_PASSWORD" psql "$APP_DSN" -v ON_ERROR_STOP=1 -q -f "$f"
  count=$((count + 1))
done
ok "$count migration(s) applied"

ok "Database ready — DSN: postgresql://${PG_USER}:***@${PG_HOST}:${PG_PORT}/${PG_DB}"
