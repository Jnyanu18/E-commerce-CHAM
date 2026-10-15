"""Intelligence worker handlers on a REAL Postgres: seed -> ledger transform (worker queue) -> handlers -> stored rows.
Needs DATABASE_URL (e.g. postgresql://postgres@localhost:54399/intelligence_agent); skipped otherwise. Run ONE suite at a time per DB."""
import os
import uuid
from datetime import date, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

from app.ai_backends import DbAnomalySource, DbModelSource  # noqa: E402
from intelligence.jobs import anomaly_id, handle_detect_anomalies, handle_mmm_run, run_mmm_job  # noqa: E402
from seed.loader import load  # noqa: E402
from worker.main import drain, handlers  # noqa: E402
from worker.queue import enqueue  # noqa: E402

URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DATABASE_URL not set")
START = date(2025, 4, 1)


def _connect():
    try:
        return psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("database unreachable")


def _ledger(conn, org):
    enqueue(conn, "transform", org, {})
    assert drain(conn, handlers()) >= 1


@pytest.fixture(scope="module")
def brand14():
    conn = _connect()
    org = str(uuid.uuid4())
    load(conn, org, name="intel-14w", weeks=14, start=START)
    _ledger(conn, org)
    yield conn, org
    conn.execute("delete from orgs where id=%s", (org,))
    conn.close()


def _job(org, **p):
    return {"org_id": org, "payload": p}


def test_anomaly_job_finds_planted_outage_is_idempotent_and_keeps_brief(brand14):
    conn, org = brand14
    ws, outage = date(2025, 6, 23), date(2025, 6, 25)
    n = conn.execute("delete from orders where org_id=%s and ordered_date_ist=%s", (org, outage)).rowcount
    assert n > 0
    # a previously stored brief must survive the anomaly upsert
    conn.execute("insert into insights (org_id, week_start, brief_markdown, tool_calls) values (%s,%s,'BRIEF', '[]'::jsonb) "
                 "on conflict (org_id, week_start) do update set brief_markdown='BRIEF'", (org, ws))
    handle_detect_anomalies(_job(org, week_start=ws.isoformat()), conn)
    row = conn.execute("select anomalies, brief_markdown, tool_calls from insights where org_id=%s and week_start=%s", (org, ws)).fetchone()
    anoms, brief, tc = row
    assert brief == "BRIEF" and tc == []
    rev = [a for a in anoms if a["metric"] == "net_revenue" and a["date"] == outage.isoformat()]
    assert rev and rev[0]["direction"] == "down" and rev[0]["id"] == anomaly_id(org, "net_revenue", outage)
    for a in anoms:
        assert {"id", "metric", "date", "direction", "robust_z", "severity", "week_start"} <= set(a)
        assert ws <= date.fromisoformat(a["date"]) <= ws + timedelta(days=6) and a["week_start"] == ws.isoformat()
    # revenue root cause: synthetic brand has no traffic_daily -> sessions undefined -> INSUFFICIENT_DATA, never invented
    assert rev[0]["root_cause"]["status"] == "INSUFFICIENT_DATA" and "sessions" in rev[0]["root_cause"]["reason"]
    # idempotent: rerun gives the same ids, one row
    handle_detect_anomalies(_job(org, week_start=ws.isoformat()), conn)
    row2 = conn.execute("select anomalies, brief_markdown from insights where org_id=%s and week_start=%s", (org, ws)).fetchone()
    assert [a["id"] for a in row2[0]] == [a["id"] for a in anoms] and len({a["id"] for a in row2[0]}) == len(row2[0])
    assert conn.execute("select count(*) from insights where org_id=%s and week_start=%s", (org, ws)).fetchone()[0] == 1
    assert row2[1] == "BRIEF"
    # the AI-side readers see it
    src = DbAnomalySource(conn)
    seen = src.anomalies(org, ws)
    assert seen and all("root_cause" not in a for a in seen)
    assert src.root_causes(org, rev[0]["id"])["status"] == "INSUFFICIENT_DATA"


def test_anomaly_job_inserts_row_when_absent_and_quiet_week_is_empty_list(brand14):
    conn, org = brand14
    ws = date(2025, 5, 12)
    conn.execute("delete from insights where org_id=%s and week_start=%s", (org, ws))
    handle_detect_anomalies(_job(org, week_start=ws.isoformat()), conn)
    row = conn.execute("select anomalies from insights where org_id=%s and week_start=%s", (org, ws)).fetchone()
    assert row is not None and isinstance(row[0], list)


def test_anomaly_job_rejects_non_monday_and_missing_org(brand14):
    conn, org = brand14
    with pytest.raises(ValueError):
        handle_detect_anomalies(_job(org, week_start="2025-06-24"), conn)
    with pytest.raises(ValueError):
        handle_detect_anomalies({"payload": {}}, conn)


def test_mmm_job_short_history_insufficient_with_experiment_or_reason(brand14):
    conn, org = brand14
    s = run_mmm_job(_job(org, end="2025-07-06"), conn, today=date(2025, 7, 7))
    assert s["mmm_status"] == "INSUFFICIENT_DATA" and "weeks" in s["mmm_reason"]
    run = conn.execute("select id::text from model_runs where org_id=%s order by created_at desc limit 1", (org,)).fetchone()
    m = DbModelSource(conn).model_results(org)
    assert m["status"] == "INSUFFICIENT_DATA" and "weeks" in m["reason"] and m["models"][0]["diagnostics"]["verdict"] == "INSUFFICIENT_DATA"
    d = conn.execute("select sufficient_data, diagnostics from model_runs where id=%s", (run[0],)).fetchone()
    assert d[0] is False and d[1]["verdict"] == "INSUFFICIENT_DATA" and d[1]["reason"]
    assert conn.execute("select count(*) from channel_response_curves where model_run_id=%s", (run[0],)).fetchone()[0] == 0
    # experiment path: either a persisted design + run_experiment recommendation, or an explicit stated reason (never invented)
    if s["experiment_id"]:
        r = conn.execute("select type, expected_impact_paise, experiment_id::text from recommendations where org_id=%s order by created_at desc limit 1", (org,)).fetchone()
        assert r[0] == "run_experiment" and r[1] is None and r[2] == s["experiment_id"]
    else:
        assert s["experiment_design"]["status"] == "INSUFFICIENT_DATA"


def test_mmm_job_no_ad_spend_org():
    conn = _connect()
    org = str(uuid.uuid4())
    try:
        conn.execute("insert into orgs(id, name) values (%s,'empty')", (org,))
        s = run_mmm_job(_job(org, end="2025-07-06"), conn, today=date(2025, 7, 7))
        assert s["mmm_status"] == "INSUFFICIENT_DATA" and "no ad spend" in s["mmm_reason"] and s["channels"] == []
    finally:
        conn.execute("delete from orgs where id=%s", (org,))
        conn.close()
