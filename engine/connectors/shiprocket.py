"""Shiprocket connector (shipments incl. status / RTO as reported by Shiprocket).

Verification status (checked 2026-10-14; full detail in NOTES.md):
- VERIFIED (official helpsheet https://support.shiprocket.in/support/solutions/articles/43000337456 and
  https://apidocs.shiprocket.in/view/metadata/SzYW1zB2): base URL https://apiv2.shiprocket.in/v1/external/,
  POST auth/login with API-user credentials, token valid 240 hours, `Authorization: Bearer <token>`,
  429 is a documented response code, webhook payload field names.
- NOT VERIFIED against official reference (apidocs.shiprocket.in is JS-rendered and returned no endpoint
  content to the fetch tool): the list endpoint path `shipments`, its `page` query parameter, the list
  response shape (`data`, `meta.pagination.{current_page,total_pages}`) and the login response key `token`.
  These come from third-party SDKs (github.com/Niyantra-Labs/shiprocket-gosdk, laravel-shiprocket-api) and
  from a search-engine summary of the helpsheet; they MUST be confirmed on the first real call.
- No verified date filter exists for incremental sync, so every sync re-scans all pages (idempotent
  upsert makes this safe). Tracking-by-AWB (`courier/track/awb/{awb}`) is verified but intentionally
  not called per shipment (cost); RTO/status vocabulary is NOT interpreted here, the raw payload is kept.
Never live-tested.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterator

import httpx

from .base import Connector
from .errors import AuthError, ResponseShapeError
from .httpclient import default_classify
from .store import RawRecord

BASE = "https://apiv2.shiprocket.in/v1/external"
MAX_PAGES_DEFAULT = 2000


class ShiprocketConnector(Connector):
    provider = "shiprocket"
    source = "shiprocket_shipment"
    watermark_path = None  # no verified server-side date filter -> full re-scan

    def __init__(self, *a, max_pages: int = MAX_PAGES_DEFAULT, **kw):
        super().__init__(*a, **kw)
        self.max_pages = max_pages
        self._token: str | None = None

    def authenticate(self) -> None:
        resp = self.http.request(
            "POST", f"{BASE}/auth/login",
            json={"email": self.credentials.get("email", provider=self.provider),
                  "password": self.credentials.get("password", provider=self.provider)},
            classify=self._classify_login)
        token = resp.json().get("token") if resp.headers.get("content-type", "").startswith("application/json") else None
        if not token:
            raise ResponseShapeError("login response had no token (key name unverified against official docs)",
                                     provider=self.provider)
        self._token = token
        self.http._secrets.append(token)

    def _classify_login(self, resp: httpx.Response) -> None:
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            raise AuthError(f"Shiprocket login rejected ({resp.status_code}): check API-user credentials",
                            provider=self.provider, status=resp.status_code)
        default_classify(resp, self.provider)

    def _get(self, path: str, params: dict, _retry_auth: bool = True) -> dict:
        try:
            resp = self.http.request("GET", f"{BASE}/{path}", params=params,
                                     headers={"Authorization": f"Bearer {self._token}"})
        except AuthError as e:
            if e.status == 401 and _retry_auth:   # token (240h) expired mid-run
                self.authenticate()
                return self._get(path, params, _retry_auth=False)
            raise
        body = resp.json()
        if not isinstance(body, dict):
            raise ResponseShapeError("Shiprocket response is not a JSON object", provider=self.provider)
        return body

    def validate(self) -> None:
        self._get("shipments", {"page": 1})

    def fetch(self, mode: str, since: datetime | None) -> Iterator[list[RawRecord]]:
        page = 1
        while page <= self.max_pages:
            body = self._get("shipments", {"page": page})
            rows = body.get("data")
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise ResponseShapeError("Shiprocket list 'data' is not an array", provider=self.provider)
            recs, seen = [], set()
            for r in rows:
                rid = r.get("id") if isinstance(r, dict) else None
                if rid is None:
                    raise ResponseShapeError("shipment row without id", provider=self.provider)
                if str(rid) in seen:
                    continue
                seen.add(str(rid))
                recs.append(RawRecord(self.integration.org_id, self.source, str(rid), r))
            if recs:
                yield recs
            pg = ((body.get("meta") or {}).get("pagination")) or {}
            cur, total = pg.get("current_page", page), pg.get("total_pages")
            if not rows or total is None or cur >= total:
                return
            page = cur + 1
        self.warnings.append(f"stopped at max_pages={self.max_pages}")
