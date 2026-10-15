"""Time-based job enqueueing. Idempotent: safe to call on every poll; never double-enqueues.

* daily sync: every active, non-CSV integration gets a `sync` job per IST day (incremental/backfill chosen by the connector).
* weekly pipeline, Mondays, one job per org with orders for the last complete IST week, in dependency order:
  06:00 IST `mmm_run` -> 07:00 `detect_anomalies` -> 08:00 `weekly_brief` (the brief reads what the first two stored).
  Each stage is independent: if one fails the next still runs and the brief states the missing input.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.ai_backends import previous_week_start

IST = timezone(timedelta(hours=5, minutes=30))
BRIEF_HOUR_IST = 8
PIPELINE = (("mmm_run", 6), ("detect_anomalies", 7), ("weekly_brief", BRIEF_HOUR_IST))  # (kind, earliest IST hour)


def enqueue_due(conn: Any, now: datetime | None = None) -> dict[str, int]:
    now_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
    counts = {"sync": 0, "weekly_brief": 0, "detect_anomalies": 0, "mmm_run": 0}
    day = now_ist.date().isoformat()

    for integ_id, org_id in conn.execute("select id::text, org_id::text from integrations "
                                         "where status in ('pending','active','error') and provider <> 'csv'").fetchall():
        exists = conn.execute("select 1 from jobs where kind = 'sync' and payload->>'integration_id' = %s "
                              "and payload->>'day' = %s", (integ_id, day)).fetchone()
        if not exists:
            conn.execute("insert into jobs(org_id, kind, payload) values (%s, 'sync', jsonb_build_object('integration_id', %s::text, 'day', %s::text))",
                         (org_id, integ_id, day))
            counts["sync"] += 1

    if now_ist.weekday() == 0:
        week = previous_week_start(now_ist.date()).isoformat()
        orgs = [r[0] for r in conn.execute("select distinct org_id::text from orders").fetchall()]
        for kind, hour in PIPELINE:
            if now_ist.hour < hour:
                continue
            for org_id in orgs:
                exists = conn.execute("select 1 from jobs where kind = %s and org_id = %s and payload->>'week_start' = %s",
                                      (kind, org_id, week)).fetchone()
                if not exists:
                    conn.execute("insert into jobs(org_id, kind, payload) values (%s, %s, jsonb_build_object('week_start', %s::text))",
                                 (org_id, kind, week))
                    counts[kind] += 1
    return counts
