"""Google Ads API connector (daily campaign performance via GoogleAdsService.Search over REST).

Verified 2026-10-14 (details and gaps in NOTES.md):
- https://developers.google.com/google-ads/api/rest/auth      (developer-token / login-customer-id /
  Authorization headers, scope https://www.googleapis.com/auth/adwords, refresh endpoint, host + /v25/)
- https://developers.google.com/google-ads/api/rest/common/search (POST .../googleAds:search, body `query`,
  `pageToken`, response `results` + `nextPageToken`, 10,000-row pages; lowerCamelCase JSON)
- https://developers.google.com/google-ads/api/docs/query/date-ranges (segments.date BETWEEN 'YYYY-MM-DD' ...)
- https://developers.google.com/google-ads/api/docs/best-practices/rate-limits (RESOURCE_TEMPORARILY_EXHAUSTED)
- https://developers.google.com/google-ads/api/docs/api-policy/access-levels (daily operation limits)
GAQL fields used are limited to those seen in official example queries (campaign.id, campaign.name,
segments.date, metrics.impressions/clicks/cost_micros). Conversion metrics, currency and time-zone fields
could not be confirmed on a fetched reference page and are available only via `extra_metrics`/`extra_fields`
(caller's responsibility). Never live-tested.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterator

import httpx

from .base import Connector
from .errors import AuthError, ConfigError, PermanentError, RateLimitError, ResponseShapeError
from .httpclient import default_classify
from .store import RawRecord

API_VERSION = "v25"
TOKEN_URL = "https://www.googleapis.com/oauth2/v3/token"
HOST = "https://googleads.googleapis.com"
BASE_SELECT = ["campaign.id", "campaign.name", "segments.date", "metrics.impressions", "metrics.clicks",
               "metrics.cost_micros"]


class GoogleAdsConnector(Connector):
    provider = "google_ads"
    source = "google_ads_row"
    watermark_path = ("segments", "date")
    overlap = timedelta(days=14)   # conversion/cost adjustments land late; configurable by subclass/attr

    def __init__(self, *a, api_version: str = API_VERSION, window_days: int = 90,
                 backfill_start: date | None = None, extra_metrics: list[str] | None = None, **kw):
        super().__init__(*a, **kw)
        cid = re.sub(r"[-\s]", "", self.integration.account_ref)
        if not re.fullmatch(r"\d{10}", cid):
            raise ConfigError("account_ref must be a 10-digit Google Ads customer id", provider=self.provider)
        self.customer_id = cid
        self.url = f"{HOST}/{api_version}/customers/{cid}/googleAds:search"
        self.window_days, self.backfill_start = window_days, backfill_start
        fields = BASE_SELECT + list(extra_metrics or [])
        for f in fields:
            if not re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", f):
                raise ConfigError(f"invalid GAQL field name {f!r}", provider=self.provider)
        self.select = ", ".join(fields)
        self._access_token: str | None = None

    # --- auth ----------------------------------------------------------
    def authenticate(self) -> None:
        c = self.credentials
        resp = self.http.request(
            "POST", TOKEN_URL, data={"grant_type": "refresh_token",
                                     "client_id": c.get("client_id", provider=self.provider),
                                     "client_secret": c.get("client_secret", provider=self.provider),
                                     "refresh_token": c.get("refresh_token", provider=self.provider)},
            classify=self._classify_oauth)
        tok = resp.json().get("access_token")
        if not tok:
            raise ResponseShapeError("OAuth refresh response had no access_token", provider=self.provider)
        self._access_token = tok
        self.http._secrets.append(tok)

    def _classify_oauth(self, resp: httpx.Response) -> None:
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            raise AuthError(f"Google OAuth refresh rejected ({resp.status_code}): re-authorise the integration",
                            provider=self.provider, status=resp.status_code)
        default_classify(resp, self.provider)

    def classify(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        status_str = None
        try:
            err = resp.json()
            err = err[0] if isinstance(err, list) and err else err
            status_str = ((err or {}).get("error") or {}).get("status")
        except (ValueError, AttributeError):
            pass
        if status_str == "RESOURCE_TEMPORARILY_EXHAUSTED":   # documented rate-limit error name
            raise RateLimitError("Google Ads RESOURCE_TEMPORARILY_EXHAUSTED", provider=self.provider,
                                 status=resp.status_code)
        if resp.status_code == 400:
            raise PermanentError(f"Google Ads rejected the request ({status_str or 'INVALID_ARGUMENT?'})",
                                 provider=self.provider, status=400)
        default_classify(resp, self.provider)

    # --- requests ------------------------------------------------------------
    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self._access_token}",
             "developer-token": self.credentials.get("developer_token", provider=self.provider)}
        login = self.credentials.get("login_customer_id", required=False)
        if login:
            h["login-customer-id"] = re.sub(r"\D", "", login)
        return h

    def _search(self, gaql: str, page_token: str | None) -> dict:
        body = {"query": gaql}
        if page_token:
            body["pageToken"] = page_token
        resp = self.http.request("POST", self.url, json=body, headers=self._headers(), classify=self.classify)
        j = resp.json()
        if not isinstance(j, dict):
            raise ResponseShapeError("Google Ads search response is not an object", provider=self.provider)
        return j

    def gaql(self, lo: date, hi: date) -> str:
        return (f"SELECT {self.select} FROM campaign "
                f"WHERE segments.date BETWEEN '{lo.isoformat()}' AND '{hi.isoformat()}'")

    def validate(self) -> None:
        d = self.clock().date()
        self._search(f"SELECT campaign.id FROM campaign WHERE segments.date BETWEEN '{d.isoformat()}' "
                     f"AND '{d.isoformat()}' LIMIT 1", None)

    def _windows(self, since: date | None, until: date) -> list[tuple[date, date]]:
        # Daily segments have a 37-month lookback (date-ranges page)
        lo = since or self.backfill_start or (until - timedelta(days=36 * 30))
        out = []
        while lo <= until:
            hi = min(lo + timedelta(days=self.window_days - 1), until)
            out.append((lo, hi))
            lo = hi + timedelta(days=1)
        return out

    def _to_record(self, row: dict) -> RawRecord:
        try:
            ext = f"{self.customer_id}:{row['campaign']['id']}:{row['segments']['date']}"
        except (KeyError, TypeError):
            raise ResponseShapeError("result row missing campaign.id or segments.date", provider=self.provider) from None
        return RawRecord(self.integration.org_id, self.source, ext, row)

    def fetch(self, mode: str, since: datetime | None) -> Iterator[list[RawRecord]]:
        until = self.clock().date()
        start = since.date() if (mode == "incremental" and since) else None
        for lo, hi in self._windows(start, until):
            token = None
            while True:
                j = self._search(self.gaql(lo, hi), token)
                recs = {}
                for r in j.get("results") or []:
                    rec = self._to_record(r)
                    recs[rec.external_id] = rec
                if recs:
                    yield list(recs.values())
                token = j.get("nextPageToken")
                if not token:
                    break
