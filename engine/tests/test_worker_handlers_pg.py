import os, uuid
from datetime import datetime, timezone

import psycopg
import pytest

URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif("localhost" not in URL, reason="needs DATABASE_URL on localhost")

from ai.provider import ProviderResponse
from app.crypto import credentials_aad, decrypt, encrypt
from seed.loader import load
from worker import handlers
from worker.main import drain
from worker.queue import enqueue
from worker.scheduler import enqueue_due

UTC = timezone.utc
MONDAY_0830_IST = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)   # Mon 2026-10-12 08:30 IST
MONDAY_0700_IST = datetime(2026, 10, 11, 1, 30, tzinfo=UTC)  # Sun 2026-10-11 07:00 IST... (not Monday 08:00)
TUESDAY = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)


@pytest.fixture()
def conn():
    c = psycopg.connect(URL, autocommit=True)
    c.execute("delete from jobs")
    yield c
    c.close()


def test_credentials_aad_binds_blob_to_integration_and_org():
    import base64
    key = base64.b64encode(os.urandom(32)).decode()
    org, integ = str(uuid.uuid4()), str(uuid.uuid4())
    blob = encrypt({"t": "x"}, key, credentials_aad(org, integ))
    assert decrypt(blob, key, credentials_aad(org, integ)) == {"t": "x"}
    with pytest.raises(Exception):
        decrypt(blob, key, credentials_aad(str(uuid.uuid4()), integ))


def test_scheduler_enqueues_sync_once_per_day_and_skips_csv(conn):
    org = str(uuid.uuid4())
    conn.execute("insert into orgs(id,name) values (%s,'sch')", (org,))
    conn.execute("insert into integrations(org_id, provider, account_ref, status) values (%s,'shopify','a.myshopify.com','active'),(%s,'csv','upload','active')", (org, org))
    n = lambda: conn.execute("select count(*) from jobs where kind='sync' and org_id=%s", (org,)).fetchone()[0]
    enqueue_due(conn, TUESDAY); assert n() == 1
    enqueue_due(conn, TUESDAY); assert n() == 1                    # same IST day: idempotent
    enqueue_due(conn, TUESDAY.replace(day=14)); assert n() == 2    # next day


def test_scheduler_weekly_brief_only_monday_after_8_ist_once(conn):
    org, _ = load(conn, name="sch brief", weeks=2)
    enqueue(conn, "transform", org, {}); drain(conn)
    conn.execute("delete from jobs")
    assert enqueue_due(conn, TUESDAY)["weekly_brief"] == 0
    assert enqueue_due(conn, MONDAY_0700_IST)["weekly_brief"] == 0
    first = enqueue_due(conn, MONDAY_0830_IST)["weekly_brief"]
    assert first >= 1 and enqueue_due(conn, MONDAY_0830_IST)["weekly_brief"] == 0
    wk = conn.execute("select payload->>'week_start' from jobs where kind='weekly_brief' and org_id=%s", (org,)).fetchone()[0]
    assert wk == "2026-10-05"  # Monday of the last complete week before Mon 2026-10-12


class _SMTP:
    sent = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def send_message(self, msg): _SMTP.sent.append(msg)


def test_weekly_brief_handler_saves_brief_emails_and_marks_sent(conn, monkeypatch):
    org, _ = load(conn, name="brief org", weeks=3)
    enqueue(conn, "transform", org, {}); drain(conn)
    user = str(uuid.uuid4())
    conn.execute("insert into auth.users(id,email) values (%s,%s)", (user, "owner@brief.invalid"))
    conn.execute("insert into org_members(org_id,user_id,role) values (%s,%s,'owner')", (org, user))
    # A provider that never calls a tool and states a number: the validator must reject it and the system must fall back
    class Careless:
        model_id = "careless-mock"
        def complete(self, **kw):
            return ProviderResponse([{"type": "text", "text": "Revenue grew 37% last week."}], "end_turn")
    monkeypatch.setattr(handlers, "provider_factory", lambda: Careless())
    monkeypatch.setattr(handlers, "smtp_factory", lambda: _SMTP())
    _SMTP.sent.clear()
    conn.execute("delete from jobs")
    enqueue(conn, "weekly_brief", org, {"week_start": "2025-04-07"})
    assert drain(conn) >= 1
    assert conn.execute("select status from jobs where kind='weekly_brief'").fetchone()[0] == "done"
    md, tc, emailed = conn.execute("select brief_markdown, tool_calls, emailed_at from insights where org_id=%s and week_start='2025-04-07'", (org,)).fetchone()
    assert "37%" not in md and "DATA-ONLY SUMMARY" in md
    assert tc["meta"]["mode"] == "fallback" and emailed is not None
    assert [m["To"] for m in _SMTP.sent] == ["owner@brief.invalid"]


def test_weekly_brief_without_api_key_fails_loudly_not_silently(conn, monkeypatch):
    org = str(uuid.uuid4())
    conn.execute("insert into orgs(id,name) values (%s,'nokey')", (org,))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(handlers, "provider_factory", handlers._default_provider)
    enqueue(conn, "weekly_brief", org, {"week_start": "2025-04-07"})
    drain(conn)
    st, err = conn.execute("select status, last_error from jobs where kind='weekly_brief'").fetchone()
    assert st == "queued" and "ANTHROPIC_API_KEY" in err   # retried with backoff, error visible
    assert conn.execute("select count(*) from insights where org_id=%s", (org,)).fetchone()[0] == 0


def test_sync_handler_unknown_integration_is_a_visible_failure(conn):
    enqueue(conn, "sync", None, {"integration_id": str(uuid.uuid4())})
    drain(conn)
    st, err = conn.execute("select status, last_error from jobs where kind='sync'").fetchone()
    assert st == "queued" and "not found" in err


def test_monday_pipeline_enqueues_in_dependency_order_by_hour(conn):
    org, _ = load(conn, name="pipeline", weeks=2)
    enqueue(conn, "transform", org, {}); drain(conn)
    conn.execute("delete from jobs")
    kinds = lambda: [r[0] for r in conn.execute("select kind from jobs where org_id=%s and kind in ('mmm_run','detect_anomalies','weekly_brief') order by id", (org,)).fetchall()]
    at = lambda h: datetime(2026, 10, 12, h, 0, tzinfo=UTC).replace(tzinfo=UTC) - __import__("datetime").timedelta(hours=5, minutes=30)  # IST hour h
    enqueue_due(conn, at(5));  assert kinds() == []
    enqueue_due(conn, at(6));  assert kinds() == ["mmm_run"]
    enqueue_due(conn, at(7));  assert kinds() == ["mmm_run", "detect_anomalies"]
    enqueue_due(conn, at(9));  assert kinds() == ["mmm_run", "detect_anomalies", "weekly_brief"]
    enqueue_due(conn, at(10)); assert len(kinds()) == 3  # idempotent


def test_all_job_kinds_have_handlers():
    from worker.main import handlers
    assert {"transform", "reconcile", "sync", "weekly_brief", "detect_anomalies", "mmm_run"} <= set(handlers())
