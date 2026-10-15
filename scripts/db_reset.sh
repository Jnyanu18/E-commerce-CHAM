#!/usr/bin/env bash
# Recreate a test database from local_stub.sql + tests/pre.sql + all migrations.
# usage: scripts/db_reset.sh [dbname]   (default: dev). Refuses protected names.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_pg.sh"
DB="${1:-dev}"
case "$DB" in postgres|template0|template1) echo "refusing protected name $DB" >&2; exit 1;; esac
psql_ -d postgres -c "drop database if exists \"$DB\" with (force)" -c "create database \"$DB\""
for f in "$ROOT/supabase/local_stub.sql" "$ROOT/supabase/tests/pre.sql" "$ROOT"/supabase/migrations/*.sql; do
  psql_ -d "$DB" -f "$f"
done
echo "ready: DATABASE_URL=postgresql://postgres@localhost:$PGPORT/$DB"
