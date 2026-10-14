"""Persistence interface for connectors + in-memory and psycopg implementations.

Connectors write ONLY raw_records rows and integration status; they enqueue a `transform` job when
new/changed raw rows landed. They never touch ledger tables.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol


@dataclass(frozen=True)
class RawRecord:
    """Contract section 1: {org_id, source, external_id, payload}."""
    org_id: str
    source: str
    external_id: str
    payload: dict


@dataclass
class IntegrationRow:
    id: str
    org_id: str
    provider: str
    account_ref: str
    credentials_enc: bytes | None = None
    status: str = "pending"
    last_synced_at: datetime | None = None
    last_error: str | None = None
    backfill_done: bool = False


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0     # existed with a different payload
    unchanged: int = 0

    @property
    def changed(self) -> int:
        return self.inserted + self.updated


class Store(Protocol):
    def get_integration(self, integration_id: str) -> IntegrationRow: ...
    def upsert_raw(self, records: Iterable[RawRecord]) -> UpsertResult: ...
    def max_payload_value(self, org_id: str, source: str, path: tuple[str, ...]) -> str | None: ...
    def existing_external_ids(self, org_id: str, source: str, ids: list[str]) -> set[str]: ...
    def update_integration(self, integration_id: str, *, status: str | None = None,
                           last_synced_at: datetime | None = None, last_error: str | None = None,
                           clear_error: bool = False, backfill_done: bool | None = None) -> None: ...
    def enqueue_transform(self, org_id: str, payload: dict) -> None: ...
    def get_csv_mapping(self, org_id: str, source: str, report_type: str) -> dict | None: ...
    def save_csv_mapping(self, org_id: str, source: str, report_type: str, mapping: dict) -> None: ...


class InMemoryStore:
    """Deterministic fake used by tests; same semantics as PsycopgStore."""

    def __init__(self):
        self.integrations: dict[str, IntegrationRow] = {}
        self.raw: dict[tuple[str, str, str], dict] = {}
        self.jobs: list[dict] = []
        self.csv_mappings: dict[tuple[str, str, str], dict] = {}

    def add_integration(self, row: IntegrationRow) -> IntegrationRow:
        self.integrations[row.id] = row
        return row

    def get_integration(self, integration_id: str) -> IntegrationRow:
        return self.integrations[integration_id]

    def upsert_raw(self, records: Iterable[RawRecord]) -> UpsertResult:
        res = UpsertResult()
        for r in records:
            key = (r.org_id, r.source, r.external_id)
            if key not in self.raw:
                res.inserted += 1
            elif self.raw[key] != r.payload:
                res.updated += 1
            else:
                res.unchanged += 1
                continue
            self.raw[key] = copy.deepcopy(r.payload)
        return res

    def max_payload_value(self, org_id, source, path):
        best = None
        for (o, s, _), p in self.raw.items():
            if o != org_id or s != source:
                continue
            v = p
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            if isinstance(v, str) and (best is None or v > best):
                best = v
        return best

    def existing_external_ids(self, org_id, source, ids):
        return {i for i in ids if (org_id, source, i) in self.raw}

    def update_integration(self, integration_id, *, status=None, last_synced_at=None, last_error=None,
                           clear_error=False, backfill_done=None):
        row = self.integrations[integration_id]
        if status is not None:
            row.status = status
        if last_synced_at is not None:
            row.last_synced_at = last_synced_at
        if last_error is not None:
            row.last_error = last_error
        if clear_error:
            row.last_error = None
        if backfill_done is not None:
            row.backfill_done = backfill_done

    def enqueue_transform(self, org_id, payload):
        self.jobs.append({"org_id": org_id, "kind": "transform", "payload": payload})

    def get_csv_mapping(self, org_id, source, report_type):
        return self.csv_mappings.get((org_id, source, report_type))

    def save_csv_mapping(self, org_id, source, report_type, mapping):
        self.csv_mappings[(org_id, source, report_type)] = mapping


class PsycopgStore:
    """psycopg 3 implementation against supabase/migrations 0001/0002.

    NOT exercised against a real Postgres in this branch (no database available): the SQL is
    covered only by statement-shape review. Needs live verification (see PROGRESS.md).
    The connection must run as a role that can write raw_records/integrations/jobs (service role).
    Each public method commits via `conn.transaction()` so a failed page never leaves a partial page.
    """

    def __init__(self, conn):
        self.conn = conn

    def get_integration(self, integration_id: str) -> IntegrationRow:
        from psycopg.rows import dict_row
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select id::text, org_id::text, provider, account_ref, credentials_enc, status, "
                        "last_synced_at, last_error, backfill_done from integrations where id = %s",
                        (integration_id,))
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"integration {integration_id} not found")
        return IntegrationRow(**row)

    def upsert_raw(self, records: Iterable[RawRecord]) -> UpsertResult:
        from psycopg.types.json import Jsonb
        res = UpsertResult()
        sql = ("insert into raw_records (org_id, source, external_id, payload) values (%s,%s,%s,%s) "
               "on conflict (org_id, source, external_id) do update "
               "set payload = excluded.payload, ingested_at = now() "
               "where raw_records.payload is distinct from excluded.payload "
               "returning (xmax = 0) as inserted")
        with self.conn.transaction(), self.conn.cursor() as cur:
            for r in records:
                cur.execute(sql, (r.org_id, r.source, r.external_id, Jsonb(r.payload)))
                row = cur.fetchone()
                if row is None:
                    res.unchanged += 1
                elif row[0]:
                    res.inserted += 1
                else:
                    res.updated += 1
        return res

    def max_payload_value(self, org_id, source, path):
        with self.conn.cursor() as cur:
            cur.execute("select max(payload #>> %s) from raw_records where org_id = %s and source = %s",
                        (list(path), org_id, source))
            row = cur.fetchone()
        return row[0] if row else None

    def existing_external_ids(self, org_id, source, ids):
        if not ids:
            return set()
        with self.conn.cursor() as cur:
            cur.execute("select external_id from raw_records where org_id=%s and source=%s "
                        "and external_id = any(%s)", (org_id, source, list(ids)))
            return {r[0] for r in cur.fetchall()}

    def update_integration(self, integration_id, *, status=None, last_synced_at=None, last_error=None,
                           clear_error=False, backfill_done=None):
        sets, args = [], []
        if status is not None:
            sets.append("status = %s"); args.append(status)
        if last_synced_at is not None:
            sets.append("last_synced_at = %s"); args.append(last_synced_at)
        if last_error is not None:
            sets.append("last_error = %s"); args.append(last_error)
        elif clear_error:
            sets.append("last_error = null")
        if backfill_done is not None:
            sets.append("backfill_done = %s"); args.append(backfill_done)
        if not sets:
            return
        with self.conn.transaction():
            self.conn.execute(f"update integrations set {', '.join(sets)} where id = %s", (*args, integration_id))

    def enqueue_transform(self, org_id, payload):
        from worker.queue import enqueue  # main-owned queue contract
        with self.conn.transaction():
            enqueue(self.conn, "transform", org_id, payload)

    def get_csv_mapping(self, org_id, source, report_type):
        with self.conn.cursor() as cur:
            cur.execute("select mapping from csv_mappings where org_id=%s and source=%s and report_type=%s",
                        (org_id, source, report_type))
            row = cur.fetchone()
        return row[0] if row else None

    def save_csv_mapping(self, org_id, source, report_type, mapping):
        from psycopg.types.json import Jsonb
        with self.conn.transaction():
            self.conn.execute(
                "insert into csv_mappings (org_id, source, report_type, mapping) values (%s,%s,%s,%s) "
                "on conflict (org_id, source, report_type) do update "
                "set mapping = excluded.mapping, updated_at = now()",
                (org_id, source, report_type, Jsonb(mapping)))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
