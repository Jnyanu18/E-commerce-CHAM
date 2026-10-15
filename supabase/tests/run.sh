#!/usr/bin/env bash
# Applies local_stub.sql + tests/pre.sql + all migrations to a FRESH database, then runs the SQL tests.
# Usage: supabase/tests/run.sh            (env: PGHOST PGPORT PGUSER PSQL DBNAME WORKERS)
# It DROPS and recreates $DBNAME (default db_agent); refuses protected names.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPA="$(dirname "$HERE")"
export PGHOST="${PGHOST:-localhost}" PGPORT="${PGPORT:-54399}" PGUSER="${PGUSER:-postgres}"
DBNAME="${DBNAME:-db_agent}"
WORKERS="${WORKERS:-8}"
PSQL="${PSQL:-psql}"
if ! command -v "$PSQL" >/dev/null 2>&1 && [ -x "/c/Program Files/PostgreSQL/18/bin/psql.exe" ]; then
  PSQL="/c/Program Files/PostgreSQL/18/bin/psql.exe"
fi
case "$DBNAME" in postgres|template0|template1|t|t2) echo "refusing to drop protected database '$DBNAME'" >&2; exit 2;; esac

q() { "$PSQL" -X -q -v ON_ERROR_STOP=1 -d "$1" "${@:2}"; }

echo "== recreating database $DBNAME on $PGHOST:$PGPORT"
q postgres -c "drop database if exists $DBNAME" -c "create database $DBNAME"

echo "== stub + Supabase privilege emulation"
q "$DBNAME" -f "$SUPA/local_stub.sql" -f "$HERE/pre.sql"

echo "== migrations"
for f in "$SUPA"/migrations/*.sql; do
  echo "   $(basename "$f")"
  q "$DBNAME" -f "$f"
done

echo "== helpers"
q "$DBNAME" -f "$HERE/helpers.sql"

echo "== tests"
fail=0
for f in "$HERE"/0[1-6]_*.test.sql; do
  if out=$(q "$DBNAME" -t -A -f "$f" 2>&1); then echo "   $(echo "$out" | tail -1)"; else echo "   FAIL $(basename "$f")"; echo "$out" | sed 's/^/      /'; fail=1; fi
done

echo "== claim_job concurrency ($WORKERS parallel sessions)"
q "$DBNAME" -f "$HERE/07_claim_concurrency_setup.sql"
pids=()
for w in $(seq 1 "$WORKERS"); do
  q "$DBNAME" -v worker="$w" -f "$HERE/07_claim_concurrency_worker.sql" >/dev/null & pids+=($!)
done
for p in "${pids[@]}"; do wait "$p" || fail=1; done
if out=$(q "$DBNAME" -t -A -f "$HERE/07_claim_concurrency_verify.sql" 2>&1); then echo "   $(echo "$out" | tail -1)"; else echo "   FAIL 07"; echo "$out" | sed 's/^/      /'; fail=1; fi

if [ "$fail" -ne 0 ]; then echo "== FAILED"; exit 1; fi
echo "== ALL PASSED"
