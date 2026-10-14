"""Meta + Google Ads with synthetic recorded-shape fixtures (never live-tested)."""
import json
from datetime import date

import httpx
import pytest

from connectors.errors import AuthError, ConfigError, PermanentError, RateLimitError, ResponseShapeError
from connectors.google_ads import GoogleAdsConnector
from connectors.meta import MetaInsightsConnector

from .conftest import NOW, ORG, creds, make_http, make_integration

META_CRED = creds(access_token="EAAG_SYNTHETIC_TOKEN_1")
G_CRED = creds(developer_token="DEVTOK_SYN_12345", client_id="cid-syn.apps.example", client_secret="csecret-syn-1",
               refresh_token="1//refresh-syn-1", login_customer_id="123-456-7890")


# ------------------------------------------------------------------ Meta
def minsight(cid, day, spend="100.50"):
    return {"account_id": "111222333", "account_currency": "INR", "campaign_id": cid, "campaign_name": f"C{cid}",
            "date_start": day, "date_stop": day, "spend": spend, "impressions": "1000", "clicks": "20",
            "actions": [{"action_type": "purchase", "value": "2"}]}


def meta(store, handler, sleeper, **kw):
    integ = make_integration(store, "meta", "act_111222333")
    c = MetaInsightsConnector(integ, META_CRED, store, make_http(handler, "meta", sleeper, META_CRED, attempts=3),
                              clock=lambda: NOW, **kw)
    c.http._classify = c.classify
    return c


def test_meta_request_shape_pagination_and_idempotent_ids(store, sleeper):
    reqs = []

    def h(req):
        reqs.append(req)
        p = req.url.params
        if p.get("date_preset"):
            return httpx.Response(200, json={"data": []})
        if "after" not in p:
            return httpx.Response(200, json={"data": [minsight("1", "2026-10-12"), minsight("2", "2026-10-12")],
                                             "paging": {"cursors": {"after": "AFT"}, "next": "https://x"}})
        assert p["after"] == "AFT"
        return httpx.Response(200, json={"data": [minsight("1", "2026-10-13")], "paging": {"cursors": {"after": "Z"}}})

    c = meta(store, h, sleeper, window_days=10_000, backfill_start=date(2026, 10, 1))
    res = c.run_sync("backfill")
    assert res.inserted == 3 and res.pages == 2
    r = reqs[1]
    assert str(r.url).startswith("https://graph.facebook.com/v26.0/act_111222333/insights?")
    p = r.url.params
    assert p["level"] == "campaign" and p["time_increment"] == "1" and p["access_token"] == "EAAG_SYNTHETIC_TOKEN_1"
    assert json.loads(p["time_range"]) == {"since": "2026-10-01", "until": "2026-10-14"}
    assert "purchase_roas" in p["fields"] and "campaign_id" in p["fields"]
    assert (ORG, "meta_insights", "campaign:111222333:1:2026-10-12") in store.raw


def test_meta_incremental_lookback_and_revision_updates(store, sleeper):
    cur = {"doc": minsight("1", "2026-10-13", "50.00")}
    ranges = []

    def h(req):
        if req.url.params.get("date_preset"):
            return httpx.Response(200, json={"data": []})
        ranges.append(json.loads(req.url.params["time_range"]))
        in_win = ranges[-1]["since"] <= "2026-10-13" <= ranges[-1]["until"]
        return httpx.Response(200, json={"data": [cur["doc"]] if in_win else []})

    c = meta(store, h, sleeper)
    c.run_sync("incremental")                      # nothing stored: long backfill-like window set
    assert ranges[0]["until"] <= "2026-10-14" and len(ranges) > 10
    ranges.clear()
    cur["doc"] = minsight("1", "2026-10-13", "55.00")
    res = c.run_sync("incremental")
    assert ranges == [{"since": "2026-09-29", "until": "2026-10-14"}]  # watermark 10-13 - 14d
    assert res.updated == 1 and len(store.raw) == 1


def test_meta_throttle_and_errors(store, sleeper):
    n = {"i": 0}

    def h(req):
        if req.url.params.get("date_preset"):
            return httpx.Response(200, json={"data": []})
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(400, json={"error": {"code": 80000, "message": "x", "type": "OAuthException"}})
        return httpx.Response(200, json={"data": []})
    meta(store, h, sleeper, window_days=10_000, backfill_start=date(2026, 10, 1)).run_sync("backfill")
    assert sleeper.calls == [0.5]

    with pytest.raises(PermanentError):
        meta(store, lambda r: httpx.Response(400, json={"error": {"code": 100, "message": "bad field"}}),
             sleeper).run_sync("incremental")
    with pytest.raises(AuthError):
        meta(store, lambda r: httpx.Response(401, json={"error": {"code": 999, "message": "nope"}}),
             sleeper).run_sync("incremental")
    assert "EAAG_SYNTHETIC" not in store.integrations["int-meta"].last_error


def test_meta_config_and_shape_errors(store, sleeper):
    integ = make_integration(store, "meta", "not-numeric")
    with pytest.raises(ConfigError):
        MetaInsightsConnector(integ, META_CRED, store, make_http(lambda r: None, "meta", sleeper))
    with pytest.raises(ResponseShapeError):
        meta(store, lambda r: httpx.Response(200, json={"oops": 1}), sleeper).run_sync("incremental")
    with pytest.raises(ResponseShapeError):
        meta(store, lambda r: httpx.Response(200, json={"data": [{"spend": "1"}]}) if not r.url.params.get("date_preset")
             else httpx.Response(200, json={"data": []}), sleeper).run_sync("incremental")


# ------------------------------------------------------------------ Google Ads
def grow(cid, day, micros="12500000"):
    return {"campaign": {"resourceName": f"customers/1234567890/campaigns/{cid}", "id": str(cid), "name": f"G{cid}"},
            "metrics": {"impressions": "500", "clicks": "10", "costMicros": micros}, "segments": {"date": day}}


def gads(store, handler, sleeper, **kw):
    integ = make_integration(store, "google_ads", "123-456-7890")
    c = GoogleAdsConnector(integ, G_CRED, store, make_http(handler, "google_ads", sleeper, G_CRED, attempts=3),
                           clock=lambda: NOW, **kw)
    c.http._classify = c.classify
    return c


def gh(pages, log=None):
    def h(req):
        if log is not None:
            log.append(req)
        if str(req.url) == "https://www.googleapis.com/oauth2/v3/token":
            assert b"grant_type=refresh_token" in req.content
            return httpx.Response(200, json={"access_token": "ya29.SYN_ACCESS", "expires_in": 3599})
        body = json.loads(req.content)
        tok = body.get("pageToken")
        if "LIMIT 1" in body["query"]:
            return httpx.Response(200, json={"results": []})
        i = int(tok) if tok else 0
        out = {"results": pages[i]} if i < len(pages) else {"results": []}
        if i + 1 < len(pages):
            out["nextPageToken"] = str(i + 1)
        return httpx.Response(200, json=out)
    return h


def test_google_headers_gaql_pagination_and_ids(store, sleeper):
    log = []
    c = gads(store, gh([[grow(1, "2026-10-12"), grow(2, "2026-10-12")], [grow(1, "2026-10-13")]], log), sleeper,
             window_days=10_000, backfill_start=date(2026, 10, 1))
    res = c.run_sync("backfill")
    assert res.inserted == 3 and res.pages == 2
    r = [x for x in log if "googleAds:search" in str(x.url)][-1]
    assert str(r.url) == "https://googleads.googleapis.com/v25/customers/1234567890/googleAds:search"
    assert r.headers["developer-token"] == "DEVTOK_SYN_12345" and r.headers["login-customer-id"] == "1234567890"
    assert r.headers["Authorization"] == "Bearer ya29.SYN_ACCESS"
    q = json.loads(r.content)["query"]
    assert "segments.date BETWEEN '2026-10-01' AND '2026-10-14'" in q and "metrics.cost_micros" in q
    assert (ORG, "google_ads_row", "1234567890:1:2026-10-12") in store.raw
    assert store.raw[(ORG, "google_ads_row", "1234567890:1:2026-10-12")]["metrics"]["costMicros"] == "12500000"


def test_google_incremental_window_empty_and_windows(store, sleeper):
    log = []
    c = gads(store, gh([[]], log), sleeper, window_days=90, backfill_start=date(2026, 1, 1))
    res = c.run_sync("backfill")
    assert res.fetched == 0 and not store.jobs
    qs = [json.loads(x.content)["query"] for x in log if b"BETWEEN" in x.content and b"LIMIT" not in x.content]
    assert len(qs) == 4 and qs[0].endswith("'2026-01-01' AND '2026-03-31'")  # 90-day windows, contiguous
    assert qs[1].split("BETWEEN")[1].startswith(" '2026-04-01'")


def test_google_oauth_failure_is_auth_error(store, sleeper):
    h = lambda r: httpx.Response(400, json={"error": "invalid_grant"})
    with pytest.raises(AuthError):
        gads(store, h, sleeper).run_sync()
    i = store.integrations["int-google_ads"]
    assert i.status == "error" and "refresh-syn" not in i.last_error


def test_google_rate_limit_name_and_400(store, sleeper):
    base = gh([[grow(1, "2026-10-13")]])
    n = {"i": 0}

    def h(req):
        if "googleAds:search" in str(req.url) and b"LIMIT" not in req.content:
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(429, json={"error": {"status": "RESOURCE_TEMPORARILY_EXHAUSTED"}})
        return base(req)
    c = gads(store, h, sleeper, window_days=10_000, backfill_start=date(2026, 10, 1))
    assert c.run_sync("backfill").inserted == 1 and sleeper.calls == [0.5]
    with pytest.raises(PermanentError):
        gads(store, lambda r: httpx.Response(200, json={"access_token": "t12345678"}) if "oauth2" in str(r.url)
             else httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}), sleeper).run_sync()


def test_google_config_and_bad_rows(store, sleeper):
    integ = make_integration(store, "google_ads", "12345")
    with pytest.raises(ConfigError):
        GoogleAdsConnector(integ, G_CRED, store, make_http(lambda r: None, "g", sleeper))
    integ2 = make_integration(store, "google_ads", "1234567890")
    with pytest.raises(ConfigError):
        GoogleAdsConnector(integ2, G_CRED, store, make_http(lambda r: None, "g", sleeper),
                           extra_metrics=["metrics.x; DROP"])
    with pytest.raises(ResponseShapeError):
        gads(store, gh([[{"campaign": {"id": "1"}}]]), sleeper, window_days=10_000,
             backfill_start=date(2026, 10, 1)).run_sync("backfill")
