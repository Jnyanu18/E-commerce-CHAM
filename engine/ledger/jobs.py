"""`transform` and `reconcile` job handlers, signature ``(job: dict, conn) -> None`` for worker.queue.

Registration (done by main, not here):
    from ledger.jobs import handle_transform, handle_reconcile
    handlers = {"transform": handle_transform, "reconcile": handle_reconcile}

Job ``payload`` (all optional): ``{"sources": [...], "since": ISO ts, "fee_rates_bps": {"<channel>": {"<fee type>": bps}}}``
for transform; ``{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "as_of": "YYYY-MM-DD"}`` for reconcile.
Raises on batch-level failure so worker.queue retries; per-record failures are returned in the summary and logged,
and do NOT fail the job (they would never succeed on retry). No schema change: the summary goes to the logger.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Callable

from reconciliation.report import reconcile_snapshot

from .pipeline import run_transform
from .pg_repository import PsycopgRepository
from .repository import Repository

log = logging.getLogger("ledger.jobs")


def _org(job: dict) -> str:
    org = job.get("org_id")
    if not org:
        raise ValueError("transform job requires org_id")
    return str(org)


def run_transform_job(job: dict, repo: Repository) -> dict:
    p = job.get("payload") or {}
    if isinstance(p, str):
        p = json.loads(p)
    summary = run_transform(repo, _org(job), sources=p.get("sources"), since=p.get("since"), fee_rates=p.get("fee_rates_bps"))
    out = summary.as_dict()
    log.info("transform org=%s applied=%d shipments=%d deferred=%d errors=%d issues=%d", job.get("org_id"),
             summary.orders_applied, summary.shipments_applied, len(summary.deferred), len(summary.errors), len(summary.issues))
    for e in summary.errors:
        log.warning("transform error %s", e)
    return out


def run_reconcile_job(job: dict, repo: Repository) -> dict:
    p = job.get("payload") or {}
    if isinstance(p, str):
        p = json.loads(p)
    start, end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
    as_of = date.fromisoformat(p["as_of"]) if p.get("as_of") else date.today()
    return reconcile_snapshot(_org(job), start, end, repo.fetch_snapshot(_org(job), start, end), as_of=as_of)


def handle_transform(job: dict, conn: Any) -> None:
    with conn.transaction():
        run_transform_job(job, PsycopgRepository(conn))


def handle_reconcile(job: dict, conn: Any) -> None:
    report = run_reconcile_job(job, PsycopgRepository(conn))
    for ch in report["channels"]:
        log.info("reconcile org=%s channel=%s status=%s unexplained_abs=%s", report["org_id"], ch["channel"],
                 ch["status"], ch["totals"]["unexplained_abs_paise"])


def make_handler(repo_factory: Callable[[Any], Repository]) -> Callable[[dict, Any], None]:
    """Test seam: a transform handler over any Repository."""
    def handler(job: dict, conn: Any) -> None:
        run_transform_job(job, repo_factory(conn))
    return handler
