"""Brief e-mail delivery over SMTP (stdlib). Configured by env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD,
SMTP_FROM. If SMTP_HOST is unset nothing is sent and `send` returns a clear 'not_configured' result (no silent success).
NOT live-tested: no SMTP account exists in this environment."""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable


@dataclass
class SendResult:
    status: str  # sent | not_configured | no_recipients | failed
    recipients: list[str]
    error: str | None = None


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send(subject: str, body: str, recipients: list[str], *, smtp_factory: Callable | None = None) -> SendResult:
    if not recipients:
        return SendResult("no_recipients", [])
    if not configured() and smtp_factory is None:
        return SendResult("not_configured", recipients)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", "briefs@localhost"), ", ".join(recipients)
    msg.set_content(body)
    try:
        factory = smtp_factory or (lambda: smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30))
        with factory() as s:
            if smtp_factory is None:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return SendResult("sent", recipients)
    except Exception as e:  # report, never swallow: the caller records the failure and the job is retried
        return SendResult("failed", recipients, error=f"{type(e).__name__}: {e}"[:300])
