# Shared settings for the local dev/test Postgres (sourced by db_*.sh). Not Supabase.
PGBIN="${PGBIN:-/c/Program Files/PostgreSQL/18/bin}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA_DIR="${PGDATA_DIR:-$ROOT/.localdb/data}"
PGPORT="${PGPORT:-54399}"
PGHOST=localhost
PGUSER=postgres
psql_() { "$PGBIN/psql.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -v ON_ERROR_STOP=1 -q "$@"; }
