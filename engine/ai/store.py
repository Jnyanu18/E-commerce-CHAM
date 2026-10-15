"""Persistence of generated briefs into `insights` (in-memory and psycopg).

`insights` has no prompt_version / model_id columns, so they are stored inside `tool_calls`
({"schema_version", "run_id", "meta": {"prompt_version", "model_id", ...}, "calls": [...]}). A schema change request
for first-class columns is logged in PROGRESS.md (Integration Needed). Nothing secret is ever written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


@dataclass
class InsightRecord:
    org_id: str
    week_start: date
    brief_markdown: str
    anomalies: list[dict[str, Any]]
    tool_calls: dict[str, Any]
    generated_at: datetime


class InsightStore(Protocol):
    def save(self, rec: InsightRecord) -> None: ...
    def get(self, org_id: str, week_start: date) -> InsightRecord | None: ...


@dataclass
class InMemoryInsightStore:
    rows: dict[tuple[str, date], InsightRecord] = field(default_factory=dict)

    def save(self, rec: InsightRecord) -> None:
        self.rows[(rec.org_id, rec.week_start)] = rec  # upsert on (org_id, week_start), like the table's unique key

    def get(self, org_id: str, week_start: date) -> InsightRecord | None:
        return self.rows.get((org_id, week_start))


class PostgresInsightStore:
    """Writes with the caller's connection (service role in production: the pipeline writes `insights`; members only read).
    `emailed_at` is never touched by a re-generation."""

    def __init__(self, conn: Any):
        self._conn = conn

    def save(self, rec: InsightRecord) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "insert into insights (org_id, week_start, anomalies, brief_markdown, tool_calls, generated_at)"
                " values (%s::uuid, %s, %s::jsonb, %s, %s::jsonb, %s)"
                " on conflict (org_id, week_start) do update set anomalies = excluded.anomalies,"
                " brief_markdown = excluded.brief_markdown, tool_calls = excluded.tool_calls, generated_at = excluded.generated_at",
                (rec.org_id, rec.week_start, json.dumps(rec.anomalies), rec.brief_markdown, json.dumps(rec.tool_calls), rec.generated_at),
            )

    def get(self, org_id: str, week_start: date) -> InsightRecord | None:
        with self._conn.cursor() as cur:
            cur.execute("select org_id::text, week_start, anomalies, brief_markdown, tool_calls, generated_at"
                        " from insights where org_id = %s::uuid and week_start = %s", (org_id, week_start))
            r = cur.fetchone()
        return InsightRecord(r[0], r[1], r[3], r[2], r[4], r[5]) if r else None
