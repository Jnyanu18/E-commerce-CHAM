"""Supabase JWT (HS256) verification + org membership check. stdlib only.

Secure by default: a valid bearer token whose `sub` is a member of the requested org is REQUIRED.
`API_DEV_AUTH_BYPASS=1` skips authentication for local development only and is logged loudly.
Unverified here: tokens issued by a real Supabase project (algorithm, aud, claims) - see PROGRESS.md.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

log = logging.getLogger("auth")


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_jwt(token: str, secret: str, now: float | None = None) -> dict:
    if not secret:
        raise AuthError(500, "server misconfigured: SUPABASE_JWT_SECRET not set")
    try:
        h, p, s = token.split(".")
        header = json.loads(_b64(h))
        payload = json.loads(_b64(p))
    except Exception:
        raise AuthError(401, "malformed token")
    if header.get("alg") != "HS256":  # never accept 'none' or an alg the caller picks
        raise AuthError(401, "unsupported token algorithm")
    good = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(good, _b64(s)):
        raise AuthError(401, "bad signature")
    if payload.get("exp") is None or float(payload["exp"]) < (now if now is not None else time.time()):
        raise AuthError(401, "token expired")
    if not payload.get("sub"):
        raise AuthError(401, "token has no subject")
    return payload


def dev_bypass() -> bool:
    return os.environ.get("API_DEV_AUTH_BYPASS") == "1"


def is_member(conn, user_id: str, org_id: str) -> bool:
    row = conn.execute("select 1 from org_members where user_id = %s and org_id = %s", (user_id, org_id)).fetchone()
    return row is not None
