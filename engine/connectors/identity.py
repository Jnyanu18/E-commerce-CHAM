"""Customer identity hashing.

Decision (see DECISIONS.md): connectors fetch email/phone ONLY to derive stable hashed keys; the raw
values are removed before persistence. Keys are sha256(hex) of the normalised value, optionally an
HMAC-SHA256 when a per-deployment pepper is supplied. Unsalted sha256 of a phone number is
brute-forceable (10^10 space); the pepper option exists for that reason, but whichever mode is used
must be identical across all connectors and the ledger or customer_key will not join. Choosing the
mode is a security decision escalated to main (PROGRESS.md > Integration Needed).
"""
from __future__ import annotations

import hashlib
import hmac
import re

_NON_DIGIT = re.compile(r"\D")


def normalise_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e if "@" in e else None


def normalise_phone(phone: str | None) -> str | None:
    """Last 10 digits of the digits-only number (drops +91 / 0 / 00 prefixes).

    Deliberately identical to the ledger's `transforms.common.customer_key` phone rule so that a customer
    hashed here joins with the ledger. Numbers with fewer than 10 digits -> None."""
    if not phone:
        return None
    digits = _NON_DIGIT.sub("", phone)
    return digits[-10:] if len(digits) >= 10 else None


def hash_value(normalised: str | None, pepper: bytes | None = None) -> str | None:
    if normalised is None:
        return None
    if pepper:
        return hmac.new(pepper, normalised.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(normalised.encode()).hexdigest()


def hash_email(email: str | None, pepper: bytes | None = None) -> str | None:
    return hash_value(normalise_email(email), pepper)


def hash_phone(phone: str | None, pepper: bytes | None = None) -> str | None:
    return hash_value(normalise_phone(phone), pepper)
