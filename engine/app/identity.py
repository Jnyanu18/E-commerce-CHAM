"""Canonical customer identity hashing. The ONLY place email/phone are normalised and hashed.

customer_key = HMAC-SHA256(CUSTOMER_HASH_KEY, normalised value), hex. Unsalted hashes of phone numbers
are brute-forceable, so the secret key is mandatory (no default). Rotating the key changes every
customer_key: treat it as a migration, not a config tweak.

Rules (Indian MVP):
- email: strip + lowercase; must contain '@'.
- phone: digits only; leading '00' dropped; a 10-digit number, or 11 digits with leading 0, or 12 digits with
  leading 91 (and 13 with 091) reduce to the 10-digit national number. Anything else -> None (unusable).
- customer_key prefers the email hash, else the phone hash. Email and phone are namespaced ('e:' / 'p:')
  before hashing so the two kinds of value can never collide.
Connectors hash at the edge (raw PII never persisted); ledger and seed use the same functions or the
already-computed hashes, never their own.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

_NON_DIGIT = re.compile(r"\D")


class MissingHashKey(RuntimeError):
    pass


def _key(key: str | bytes | None) -> bytes:
    k = key if key is not None else os.environ.get("CUSTOMER_HASH_KEY", "")
    if isinstance(k, str):
        k = k.encode()
    if len(k) < 16:
        raise MissingHashKey("CUSTOMER_HASH_KEY must be set (at least 16 characters)")
    return k


def normalise_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e if "@" in e else None


def normalise_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    d = _NON_DIGIT.sub("", phone)
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 13 and d.startswith("091"):
        d = d[3:]
    elif len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "6789" else None


def _hmac(namespace: str, value: str, key: str | bytes | None) -> str:
    return hmac.new(_key(key), f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()


def hash_email(email: str | None, key: str | bytes | None = None) -> str | None:
    n = normalise_email(email)
    return _hmac("e", n, key) if n else None


def hash_phone(phone: str | None, key: str | bytes | None = None) -> str | None:
    n = normalise_phone(phone)
    return _hmac("p", n, key) if n else None


def customer_key(email: str | None = None, phone: str | None = None, key: str | bytes | None = None) -> str | None:
    return hash_email(email, key) or hash_phone(phone, key)


def customer_key_from_hashes(email_hash: str | None, phone_hash: str | None) -> str | None:
    """Same choice as customer_key(), for callers that only hold already-hashed values."""
    return email_hash or phone_hash
