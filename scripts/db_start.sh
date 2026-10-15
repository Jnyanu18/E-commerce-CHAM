#!/usr/bin/env bash
# Start (initialising on first run) a persistent local Postgres in .localdb/ (gitignored). Trust auth, localhost only.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_pg.sh"
mkdir -p "$ROOT/.localdb"
if [ ! -f "$PGDATA_DIR/PG_VERSION" ]; then
  "$PGBIN/initdb.exe" -D "$PGDATA_DIR" -U postgres --auth=trust -E UTF8 >/dev/null
fi
if "$PGBIN/pg_ctl.exe" -D "$PGDATA_DIR" status >/dev/null 2>&1; then echo "already running on port $PGPORT"; exit 0; fi
"$PGBIN/pg_ctl.exe" -D "$PGDATA_DIR" -o "-p $PGPORT -c listen_addresses=localhost" -l "$ROOT/.localdb/pg.log" -w start >/dev/null 2>&1 </dev/null
echo "postgres up: postgresql://postgres@localhost:$PGPORT/postgres"
