"""Worker job handlers owned by main: connector sync, weekly brief (+ e-mail). transform/reconcile live in ledger.jobs.

Job kinds: `sync` {integration_id, mode?}, `weekly_brief` {week_start?, send_email?}.
`provider_factory` is the seam for tests (and for swapping providers); production uses the Anthropic provider and
FAILS LOUDLY when ANTHROPIC_API_KEY is missing rather than emitting a pseudo-AI brief.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from ai.brief import generate_brief
from ai.store import PostgresInsightStore
from app import mailer
from app.ai_backends import build_backends, previous_week_start
from app.config import settings
from app.crypto import credentials_aad
from connectors.registry import build_connector
from connectors.store import PsycopgStore

log = logging.getLogger("worker.handlers")
IST = timezone(timedelta(hours=5, minutes=30))


class ConfigurationError(RuntimeError):
    pass


def _default_provider():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ConfigurationError("ANTHROPIC_API_KEY is not set: weekly briefs need the Claude API (no fake briefs)")
    from ai.provider import AnthropicProvider
    return AnthropicProvider()


provider_factory: Callable[[], Any] = _default_provider
smtp_factory: Callable | None = None  # tests inject a fake SMTP


def handle_sync(job: dict, conn: Any) -> None:
    p = job.get("payload") or {}
    integ_id = p["integration_id"]
    org = conn.execute("select org_id::text from integrations where id = %s", (integ_id,)).fetchone()
    if org is None:
        raise ValueError(f"integration {integ_id} not found")
    store = PsycopgStore(conn)
    connector = build_connector(store, integ_id, settings.credentials_key, aad=credentials_aad(org[0], integ_id))
    res = connector.run_sync(p.get("mode", "auto"))
    log.info("sync integration=%s %s", integ_id, res)


def _recipients(conn: Any, org_id: str) -> list[str]:
    rows = conn.execute("select u.email from org_members m join auth.users u on u.id = m.user_id "
                        "where m.org_id = %s and u.email is not null order by 1", (org_id,)).fetchall()
    return [r[0] for r in rows]


def handle_weekly_brief(job: dict, conn: Any) -> None:
    org_id = str(job["org_id"])
    p = job.get("payload") or {}
    week = date.fromisoformat(p["week_start"]) if p.get("week_start") else previous_week_start(datetime.now(IST).date())
    result = generate_brief(org_id, week, provider=provider_factory(), backends=build_backends(conn),
                            store=PostgresInsightStore(conn))
    log.info("brief org=%s week=%s mode=%s", org_id, week, result.mode)
    if p.get("send_email", True):
        sent = mailer.send(f"Your weekly business brief ({week.isoformat()})", result.markdown, _recipients(conn, org_id),
                           smtp_factory=smtp_factory)
        if sent.status == "sent":
            conn.execute("update insights set emailed_at = now() where org_id = %s and week_start = %s", (org_id, week))
        elif sent.status == "failed":
            raise RuntimeError(f"brief generated but e-mail failed: {sent.error}")  # job retries; brief upsert is idempotent
        else:
            log.warning("brief for org=%s not e-mailed: %s", org_id, sent.status)
