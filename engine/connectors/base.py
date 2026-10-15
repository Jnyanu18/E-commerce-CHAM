"""Connector abstraction and sync lifecycle.

Lifecycle (run_sync):
  authenticate -> validate -> (backfill | incremental) fetch pages -> normalize_to_raw -> persist raw
  (per page, idempotent upsert) -> update integration status -> enqueue `transform` if rows changed.

Raw persistence happens page by page BEFORE any transformation; the connector never writes ledger
tables. Incremental watermark is derived from the stored raw data (`watermark_path`) minus an
overlap window, so no extra sync-state column is needed (see DECISIONS.md); re-fetched rows are
no-ops thanks to the (org_id, source, external_id) upsert.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Literal

from .credentials import Credentials, redact
from .errors import AuthError, ConnectorError
from .httpclient import HttpClient
from .store import IntegrationRow, RawRecord, Store, UpsertResult

log = logging.getLogger("connectors")
Mode = Literal["backfill", "incremental", "auto"]


@dataclass
class SyncResult:
    provider: str
    mode: str
    pages: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)
    transform_enqueued: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (with 'Z' or offset) to aware UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class Connector(ABC):
    provider: str                 # matches integrations.provider
    source: str                   # raw_records.source
    watermark_path: tuple[str, ...] | None = None   # JSON path in payload holding an ISO timestamp/date
    overlap: timedelta = timedelta(hours=1)          # re-fetch margin before the watermark

    def __init__(self, integration: IntegrationRow, credentials: Credentials, store: Store,
                 http: HttpClient, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.integration = integration
        self.credentials = credentials
        self.store = store
        self.http = http
        self.clock = clock
        self.warnings: list[str] = []

    # --- lifecycle hooks -------------------------------------------------
    def authenticate(self) -> None:
        """Obtain/refresh a session token if the provider needs it. Default: no-op."""

    @abstractmethod
    def validate(self) -> None:
        """Cheap call proving credentials + scopes work. Raise AuthError/ConnectorError otherwise."""

    @abstractmethod
    def fetch(self, mode: str, since: datetime | None) -> Iterator[list[RawRecord]]:
        """Yield pages of already-normalised RawRecords (pagination handled inside)."""

    # --- orchestration ----------------------------------------------------
    def watermark(self) -> datetime | None:
        if not self.watermark_path:
            return None
        v = self.store.max_payload_value(self.integration.org_id, self.source, self.watermark_path)
        if not v:
            return None
        return parse_ts(v if "T" in v else v + "T00:00:00+00:00")

    def run_sync(self, mode: Mode = "auto") -> SyncResult:
        integ = self.integration
        if mode == "auto":
            mode = "incremental" if integ.backfill_done else "backfill"
        res = SyncResult(provider=self.provider, mode=mode, started_at=self.clock())
        try:
            self.authenticate()
            self.validate()
            since = None
            if mode == "incremental":
                wm = self.watermark()
                since = (wm - self.overlap) if wm else None   # no data yet -> full pull
            for page in self.fetch(mode, since):
                up: UpsertResult = self.store.upsert_raw(page)
                res.pages += 1
                res.fetched += len(page)
                res.inserted += up.inserted
                res.updated += up.updated
                res.unchanged += up.unchanged
        except ConnectorError as e:
            self._record_failure(e)
            res.finished_at = self.clock()
            raise
        res.warnings = list(self.warnings)
        res.finished_at = self.clock()
        self.store.update_integration(integ.id, status="active", last_synced_at=res.finished_at,
                                      clear_error=True,
                                      backfill_done=True if mode == "backfill" else None)
        if res.inserted + res.updated:
            self.store.enqueue_transform(integ.org_id, {"trigger": "connector", "provider": self.provider,
                                                        "source": self.source, "mode": mode})
            res.transform_enqueued = True
        return res

    def _record_failure(self, e: ConnectorError) -> None:
        msg = redact(f"{e.code}: {e}", self.credentials.secret_values())[:1000]
        # Only conditions needing human action flip status to 'error'; transient ones keep status
        # (the job runner retries) but still record last_error.
        status = "error" if isinstance(e, AuthError) or not e.retryable else None
        self.store.update_integration(self.integration.id, status=status, last_error=msg)
