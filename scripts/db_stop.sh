#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_pg.sh"
"$PGBIN/pg_ctl.exe" -D "$PGDATA_DIR" -m fast -w stop >/dev/null 2>&1 </dev/null && echo stopped || echo "not running"
