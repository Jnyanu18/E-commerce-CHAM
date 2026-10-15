"""Time-based job enqueueing. Idempotent: safe to call on every poll; never double-enqueues.

* daily sync: every active, non-CSV integration gets a `sync` job per IST day (incremental/backfill chosen by the connector).
* weekly brief: Mondays from 08:00 IST, one `weekly_brief` job per org with orders, for the last complete IST week.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.ai_backends import previous_week_start

IST = timezone(timedelta(hours=5, minutes=30))
BRIEF_HOUR_IST = 8


def enqueue_due(conn: Any, now: datetime | None = None) -> dict[str, int]:
    now_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
    counts = {"sync": 0, "weekly_brief": 0}
    day = now_ist.date().isoformat()

    for integ_id, org_id in conn.execute("select id::text, org_id::text from integrations "
                                         "where status in ('pending','active','error') and provider <> 'csv'").fetchall():
        exists = conn.execute("select 1 from jobs where kind = 'sync' and payload->>'integration_id' = %s "
                              "and payload->>'day' = %s", (integ_id, day)).fetchone()
        if not exists:
            conn.execute("insert into jobs(org_id, kind, payload) values (%s, 'sync', jsonb_build_object('integration_id', %s::text, 'day', %s::text))",
                         (org_id, integ_id, day))
            counts["sync"] += 1

    if now_ist.weekday() == 0 and now_ist.hour >= BRIEF_HOUR_IST:
        week = previous_week_start(now_ist.date()).isoformat()
        for (org_id,) in conn.execute("select distinct org_id::text from orders").fetchall():
            exists = conn.execute("select 1 from jobs where kind = 'weekly_brief' and org_id = %s and payload->>'week_start' = %s",
                                  (org_id, week)).fetchone()
            if not exists:
                conn.execute("insert into jobs(org_id, kind, payload) values (%s, 'weekly_brief', jsonb_build_object('week_start', %s::text))",
                             (org_id, week))
                counts["weekly_brief"] += 1
    return counts
