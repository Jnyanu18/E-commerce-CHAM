"""Postgres job queue (jobs table, claim_job() uses FOR UPDATE SKIP LOCKED)."""
from datetime import timedelta
from typing import Callable

import psycopg
from psycopg.rows import dict_row

Handler = Callable[[dict, psycopg.Connection], None]


def enqueue(conn: psycopg.Connection, kind: str, org_id: str | None = None, payload: dict | None = None) -> None:
    conn.execute(
        "insert into jobs (org_id, kind, payload) values (%s, %s, %s::jsonb)",
        (org_id, kind, psycopg.types.json.Jsonb(payload or {})),
    )


def run_one(conn: psycopg.Connection, handlers: dict[str, Handler]) -> bool:
    """Claim and run one job. Returns False if the queue was empty."""
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:  # per-cursor: never mutate the caller's connection
            job = cur.execute("select * from claim_job(%s)", (list(handlers),)).fetchone()
    if not job or job["id"] is None:
        return False
    try:
        handlers[job["kind"]](job, conn)
        conn.execute("update jobs set status='done', finished_at=now() where id=%s", (job["id"],))
    except Exception as e:  # retry with exponential backoff, then fail permanently
        if job["attempts"] >= job["max_attempts"]:
            conn.execute("update jobs set status='failed', last_error=%s, finished_at=now() where id=%s",
                         (str(e)[:2000], job["id"]))
        else:
            delay = timedelta(seconds=30 * 2 ** (job["attempts"] - 1))
            conn.execute("update jobs set status='queued', last_error=%s, run_after=now()+%s where id=%s",
                         (str(e)[:2000], delay, job["id"]))
    return True


def reap(conn: psycopg.Connection, stale_after: str = "15 minutes") -> int:
    """Requeue (or fail, once attempts are exhausted) jobs stuck in `running`. Returns rows affected."""
    row = conn.execute("select reap_stale_jobs(%s::interval)", (stale_after,)).fetchone()
    return int(row[0] if not isinstance(row, dict) else next(iter(row.values())) or 0)
