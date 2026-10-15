import base64
import os

import httpx
import pytest

from app.crypto import encrypt
from connectors.base import Connector, parse_ts
from connectors.credentials import Credentials, decrypt_credentials, redact
from connectors.errors import (AuthError, PermanentError, RateLimitError, TransientError)
from connectors.httpclient import HttpClient, RetryPolicy, parse_retry_after
from connectors.store import InMemoryStore, RawRecord

from .conftest import NOW, ORG, make_http, make_integration


def client(handler, sleeper, attempts=4):
    return HttpClient(provider="t", transport=httpx.MockTransport(handler),
                      policy=RetryPolicy(max_attempts=attempts, base_delay=1.0, max_delay=8.0),
                      sleep=sleeper, rng=lambda: 0.5)


def test_retry_5xx_then_success_with_backoff(sleeper):
    seq = iter([500, 502, 200])
    c = client(lambda r: httpx.Response(next(seq), json={}), sleeper)
    assert c.request("GET", "https://x.test/").status_code == 200
    assert sleeper.calls == [0.5, 1.0]  # 0.5 * min(8, 1*2**(n-1)) for n=1,2


def test_backoff_capped(sleeper):
    c = client(lambda r: httpx.Response(200), sleeper)
    assert c.backoff(10) == 0.5 * 8.0


def test_429_honours_retry_after(sleeper):
    seq = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    c = client(lambda r: next(seq), sleeper)
    c.request("GET", "https://x.test/")
    assert sleeper.calls == [7.0]


def test_retry_after_beyond_cap_raises(sleeper):
    c = client(lambda r: httpx.Response(429, headers={"Retry-After": "99999"}), sleeper)
    with pytest.raises(RateLimitError):
        c.request("GET", "https://x.test/")
    assert sleeper.calls == []


def test_exhausted_retries_raise_last_error(sleeper):
    c = client(lambda r: httpx.Response(503), sleeper, attempts=3)
    with pytest.raises(TransientError):
        c.request("GET", "https://x.test/")
    assert len(sleeper.calls) == 2 and c.requests_made == 3


def test_auth_and_4xx_not_retried(sleeper):
    for status, exc in [(401, AuthError), (403, AuthError), (400, PermanentError), (404, PermanentError)]:
        c = client(lambda r, s=status: httpx.Response(s), sleeper)
        with pytest.raises(exc):
            c.request("GET", "https://x.test/")
        assert c.requests_made == 1


def test_network_error_retried(sleeper):
    calls = {"n": 0}

    def h(r):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)
    assert client(h, sleeper).request("GET", "https://x.test/").status_code == 200


def test_parse_retry_after():
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None


def test_credentials_never_in_repr_and_redaction():
    c = Credentials({"access_token": "shpat_supersecret"})
    assert "supersecret" not in repr(c) and "supersecret" not in str(c)
    assert redact("oops shpat_supersecret leaked", c.secret_values()) == "oops [REDACTED] leaked"
    with pytest.raises(AuthError):
        c.get("missing")


def test_decrypt_credentials_roundtrip_and_failure():
    key = base64.b64encode(os.urandom(32)).decode()
    blob = encrypt({"access_token": "abc123456"}, key, b"aad")
    assert decrypt_credentials(blob, key, b"aad").get("access_token") == "abc123456"
    with pytest.raises(AuthError):
        decrypt_credentials(blob, key, b"other")
    with pytest.raises(AuthError):
        decrypt_credentials(None, key)


def test_shopify_identity_matches_app_identity_and_drops_raw_pii():
    import app.identity as ai
    from connectors.shopify import sanitize_order
    out = sanitize_order({"id": "x", "email": " A@B.co ", "phone": "+91 98765 43210"}, "k" * 20)
    assert out["customerIdentity"] == {"emailHash": ai.hash_email("a@b.co", "k" * 20),
                                       "phoneHash": ai.hash_phone("9876543210", "k" * 20)}
    assert "email" not in out and "phone" not in out
    assert ai.customer_key_from_hashes(**{"email_hash": out["customerIdentity"]["emailHash"],
                                          "phone_hash": None}) == ai.customer_key("a@b.co", key="k" * 20)


def test_inmemory_upsert_idempotent_and_detects_change():
    s = InMemoryStore()
    r = RawRecord(ORG, "x", "1", {"a": 1})
    assert s.upsert_raw([r]).inserted == 1
    assert s.upsert_raw([r]).unchanged == 1 and len(s.raw) == 1
    res = s.upsert_raw([RawRecord(ORG, "x", "1", {"a": 2})])
    assert res.updated == 1 and s.raw[(ORG, "x", "1")] == {"a": 2}
    assert s.upsert_raw([]).changed == 0


class Dummy(Connector):
    provider, source, watermark_path = "shopify", "dummy", ("ts",)

    def __init__(self, *a, pages=(), fail=None, **kw):
        super().__init__(*a, **kw)
        self.pages, self.fail, self.seen_since = pages, fail, "unset"

    def validate(self):
        if self.fail == "validate":
            raise AuthError("bad token")

    def fetch(self, mode, since):
        self.seen_since = since
        for p in self.pages:
            yield [RawRecord(self.integration.org_id, self.source, i, {"ts": t}) for i, t in p]
        if self.fail == "fetch":
            raise TransientError("boom")


def mk(store, **kw):
    integ = make_integration(store, "shopify", "x.myshopify.com")
    return Dummy(integ, Credentials({"access_token": "tok-123456"}), store, None, clock=lambda: NOW, **kw)


def test_run_sync_backfill_then_incremental_updates_state_and_enqueues():
    s = InMemoryStore()
    c = mk(s, pages=[[("1", "2026-10-01T00:00:00Z")], [("2", "2026-10-02T00:00:00Z")]])
    res = c.run_sync("auto")
    assert res.mode == "backfill" and res.inserted == 2 and res.pages == 2 and res.transform_enqueued
    i = s.integrations["int-shopify"]
    assert i.status == "active" and i.backfill_done and i.last_synced_at == NOW and i.last_error is None
    assert s.jobs == [{"org_id": ORG, "kind": "transform",
                       "payload": {"trigger": "connector", "provider": "shopify", "source": "dummy", "mode": "backfill"}}]
    c2 = Dummy(i, c.credentials, s, None, pages=[[("2", "2026-10-02T00:00:00Z")]], clock=lambda: NOW)
    res2 = c2.run_sync("auto")
    assert res2.mode == "incremental" and res2.unchanged == 1 and not res2.transform_enqueued
    assert len(s.jobs) == 1
    assert c2.seen_since == parse_ts("2026-10-02T00:00:00Z") - c2.overlap


def test_run_sync_empty_data_no_job():
    s = InMemoryStore()
    res = mk(s, pages=[]).run_sync()
    assert res.fetched == 0 and not s.jobs and s.integrations["int-shopify"].status == "active"


def test_auth_failure_sets_error_status_and_redacts():
    s = InMemoryStore()
    c = mk(s, fail="validate")
    with pytest.raises(AuthError):
        c.run_sync()
    i = s.integrations["int-shopify"]
    assert i.status == "error" and "auth_error" in i.last_error and not s.jobs


def test_transient_failure_keeps_status_but_keeps_partial_raw():
    s = InMemoryStore()
    c = mk(s, pages=[[("1", "2026-10-01T00:00:00Z")]], fail="fetch")
    with pytest.raises(TransientError):
        c.run_sync()
    i = s.integrations["int-shopify"]
    assert i.status == "pending" and "transient_error" in i.last_error and not i.backfill_done
    assert len(s.raw) == 1  # raw persisted page-by-page before the failure


def test_registry_builds_connector_from_encrypted_integration():
    from connectors.registry import build_connector
    from connectors.shopify import ShopifyConnector
    key = base64.b64encode(os.urandom(32)).decode()
    s = InMemoryStore()
    integ = make_integration(s, "shopify", "syn.myshopify.com")
    integ.credentials_enc = encrypt({"access_token": "shpat_syn_000001"}, key)
    c = build_connector(s, integ.id, key)
    assert isinstance(c, ShopifyConnector) and "shpat" not in repr(c.credentials)
    assert c.http._classify == c.classify
    csv_i = make_integration(s, "csv", "upload")
    from connectors.errors import ConfigError
    with pytest.raises(ConfigError):
        build_connector(s, csv_i.id, key)
