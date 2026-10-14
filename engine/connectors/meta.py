"""Meta Marketing API insights connector (daily insights at campaign/adset/ad level).

Verified 2026-10-14 against official docs (details and gaps in NOTES.md):
- https://developers.facebook.com/docs/marketing-api/reference/ad-account/insights/  (params, fields)
- https://developers.facebook.com/docs/marketing-api/insights  (act_<AD_ACCOUNT_ID>/insights path, paging)
- https://developers.facebook.com/docs/graph-api/overview/rate-limiting (error codes 4/17/613/80000-series)
- https://developers.facebook.com/docs/marketing-api/overview/rate-limiting  (insights throttle headers)
- https://developers.facebook.com/docs/marketing-api/get-started/authorization (ads_read, access levels)
- https://developers.facebook.com/docs/graph-api/guides/versioning (versioned graph.facebook.com URL)
UNVERIFIED and therefore not implemented: OAuth login/long-lived-token exchange, token-expiry error code,
async report jobs, units of `estimated_time_to_regain_access`. Graph version: the docs showed both v25.0
(insights page examples) and v26.0 (versioning page, "latest"); v26.0 is the default and configurable.
Never live-tested.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Iterator

import httpx

from .base import Connector
from .errors import AuthError, ConfigError, PermanentError, RateLimitError, ResponseShapeError, TransientError
from .httpclient import default_classify
from .store import RawRecord

GRAPH_VERSION = "v26.0"
# Throttle codes from the Graph API rate-limiting page: app (4), user/ad-account (17), custom (613),
# business-use-case 80000-series (Ads Insights 80000, Custom Audience 80003, Ads Management 80004, Catalog Batch 80014).
THROTTLE_CODES = {4, 17, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80009, 80014}
LEVEL_ID_FIELDS = {
    "account": [],
    "campaign": ["campaign_id", "campaign_name"],
    "adset": ["campaign_id", "campaign_name", "adset_id"],
    "ad": ["campaign_id", "campaign_name", "adset_id", "ad_id"],
}
BASE_FIELDS = ["date_start", "date_stop", "account_id", "account_currency", "spend", "impressions", "clicks",
               "reach", "actions", "action_values", "purchase_roas"]
LEVEL_KEY = {"account": "account_id", "campaign": "campaign_id", "adset": "adset_id", "ad": "ad_id"}


class MetaInsightsConnector(Connector):
    provider = "meta"
    source = "meta_insights"
    watermark_path = ("date_start",)
    # Attribution windows default to 7d_click + 1d_view (docs); conversions get revised after the day,
    # so re-pull a trailing window each run (idempotent upsert makes this safe). Configurable.
    overlap = timedelta(days=14)

    def __init__(self, *a, graph_version: str = GRAPH_VERSION, level: str = "campaign", page_limit: int = 100,
                 window_days: int = 30, backfill_start: date | None = None, **kw):
        super().__init__(*a, **kw)
        if level not in LEVEL_ID_FIELDS:
            raise ConfigError(f"level must be one of {sorted(LEVEL_ID_FIELDS)}", provider=self.provider)
        acct = self.integration.account_ref.strip()
        acct = acct[4:] if acct.startswith("act_") else acct
        if not re.fullmatch(r"\d{5,20}", acct):
            raise ConfigError("account_ref must be a numeric ad account id (optionally prefixed act_)",
                              provider=self.provider)
        self.url = f"https://graph.facebook.com/{graph_version}/act_{acct}/insights"
        self.level, self.page_limit, self.window_days = level, page_limit, window_days
        self.backfill_start = backfill_start
        self.fields = ",".join(LEVEL_ID_FIELDS[level] + BASE_FIELDS)
        self.source = "meta_insights"

    def classify(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        err = None
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
        except ValueError:
            pass
        kw = {"provider": self.provider, "status": resp.status_code}
        if isinstance(err, dict):
            code = err.get("code")
            msg = str(err.get("message", ""))[:300]
            if code in THROTTLE_CODES:
                raise RateLimitError(f"Meta throttle code {code}", **kw, detail={"code": code})
            if resp.status_code in (401, 403):
                raise AuthError(f"Meta auth/permission error (code {code}): {msg}", **kw)
            if resp.status_code >= 500:
                raise TransientError(f"Meta server error (code {code})", **kw)
            raise PermanentError(f"Meta API error code {code}: {msg}", **kw, detail={"code": code})
        default_classify(resp, self.provider)

    def _get(self, params: dict) -> dict:
        params = {**params, "access_token": self.credentials.get("access_token", provider=self.provider)}
        resp = self.http.request("GET", self.url, params=params, classify=self.classify)
        body = resp.json()
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ResponseShapeError("Meta insights response missing 'data' array", provider=self.provider)
        return body

    def validate(self) -> None:
        self._get({"fields": "spend", "date_preset": "last_7d", "limit": 1})

    def _windows(self, since: date | None, until: date) -> list[tuple[date, date]]:
        if since is None:
            # date_preset=maximum returns up to 37 months (docs); start slightly inside that bound
            since = self.backfill_start or (until - timedelta(days=36 * 30))
        out, lo = [], since
        while lo <= until:
            hi = min(lo + timedelta(days=self.window_days - 1), until)
            out.append((lo, hi))
            lo = hi + timedelta(days=1)
        return out

    def _to_record(self, row: dict) -> RawRecord:
        obj = row.get(LEVEL_KEY[self.level])
        ds = row.get("date_start")
        if not obj or not ds:
            raise ResponseShapeError(f"insights row missing {LEVEL_KEY[self.level]}/date_start", provider=self.provider)
        ext = f"{self.level}:{row.get('account_id', '')}:{obj}:{ds}"
        return RawRecord(self.integration.org_id, self.source, ext, row)

    def fetch(self, mode: str, since: datetime | None) -> Iterator[list[RawRecord]]:
        until = self.clock().date()
        start = since.date() if (mode == "incremental" and since) else None
        for lo, hi in self._windows(start, until):
            params = {"level": self.level, "fields": self.fields, "time_increment": 1, "limit": self.page_limit,
                      "time_range": json.dumps({"since": lo.isoformat(), "until": hi.isoformat()})}
            while True:
                body = self._get(params)
                recs = {}
                for r in body["data"]:
                    rec = self._to_record(r)
                    recs[rec.external_id] = rec
                if recs:
                    yield list(recs.values())
                paging = body.get("paging") or {}
                after = (paging.get("cursors") or {}).get("after")
                if not paging.get("next") or not after:
                    break
                params = {**params, "after": after}
