"""Worker process: reap stale jobs, then claim and run jobs until the queue is empty, then sleep.

Run: `python -m worker.main` (DATABASE_URL from env/.env). Handlers are registered here (main owns wiring);
`transform`/`reconcile` come from the ledger agent's `ledger.jobs`.
"""
from __future__ import annotations

import logging
import time

import psycopg

from app.config import settings
from ledger.jobs import handle_reconcile, handle_transform

from .queue import Handler, reap, run_one

log = logging.getLogger("worker")


def handlers() -> dict[str, Handler]:
    return {"transform": handle_transform, "reconcile": handle_reconcile}


def drain(conn: psycopg.Connection, hs: dict[str, Handler] | None = None, max_jobs: int = 1000) -> int:
    """Reap stale jobs, then run jobs until none are runnable. Returns jobs processed."""
    hs = hs or handlers()
    reaped = reap(conn)
    if reaped:
        log.warning("reaped %d stale jobs", reaped)
    n = 0
    while n < max_jobs and run_one(conn, hs):
        n += 1
    return n


def main(poll_seconds: float = 5.0) -> None:  # pragma: no cover - process loop
    logging.basicConfig(level=logging.INFO)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        while True:
            if drain(conn) == 0:
                time.sleep(poll_seconds)


if __name__ == "__main__":  # pragma: no cover
    main()
