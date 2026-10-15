import os, uuid
import psycopg
import pytest

URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL or "localhost" not in URL, reason="needs DATABASE_URL on localhost (tests truncate jobs)")

from worker.main import drain, handlers
from worker.queue import enqueue, reap


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(URL, autocommit=True)
    except Exception:
        pytest.skip("database unreachable")
    c.execute("truncate jobs restart identity")
    yield c
    c.close()


def org(conn):
    oid = str(uuid.uuid4())
    conn.execute("insert into orgs(id,name) values (%s,'w')", (oid,))
    return oid


def test_handlers_registered():
    assert {"transform", "reconcile"} <= set(handlers())


def test_drain_runs_transform_job_on_empty_org(conn):
    o = org(conn)
    enqueue(conn, "transform", o)
    assert drain(conn) == 1
    assert conn.execute("select status from jobs").fetchone()[0] == "done"


def test_failed_job_is_requeued_with_backoff_then_not_rerun_immediately(conn):
    enqueue(conn, "transform", None)  # no org_id -> handler raises ValueError
    assert drain(conn) == 1
    st, err, future = conn.execute("select status, last_error, run_after > now() from jobs").fetchone()
    assert st == "queued" and "org_id" in err and future
    assert drain(conn) == 0


def test_reaper_requeues_stale_running_job(conn):
    enqueue(conn, "transform", org(conn))
    conn.execute("update jobs set status='running', locked_at = now() - interval '1 hour', attempts = 1")
    assert reap(conn) == 1
    assert conn.execute("select status, reaped_count from jobs").fetchone() == ("queued", 1)
    assert drain(conn) == 1


def test_queue_does_not_mutate_connection_row_factory(conn):
    enqueue(conn, "transform", org(conn))
    before = conn.row_factory
    drain(conn)
    assert conn.row_factory is before
