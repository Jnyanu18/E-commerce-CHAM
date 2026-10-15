"""Synthetic recorded-shape Shopify fixtures (NOT real data, never live-tested)."""
import json

import httpx
import pytest

from connectors.errors import AuthError, ConfigError, PermanentError, RateLimitError
from app.identity import hash_email, hash_phone
from connectors.shopify import API_VERSION, ShopifyConnector, build_orders_query

from .conftest import NOW, ORG, creds, make_http, make_integration

CRED = creds(access_token="shpat_SYNTHETIC_TOKEN_0001")


def money(a):
    return {"shopMoney": {"amount": a, "currencyCode": "INR"}}


def order(n, updated="2026-10-10T10:00:00Z", **kw):
    o = {"id": f"gid://shopify/Order/{n}", "name": f"#SYN{n}", "createdAt": "2026-10-01T10:00:00Z",
         "updatedAt": updated, "processedAt": "2026-10-01T10:00:00Z", "cancelledAt": None,
         "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED", "currencyCode": "INR",
         "paymentGatewayNames": ["razorpay"], "tags": [], "sourceName": "web",
         "email": "Synthetic.Buyer@example.invalid", "phone": "+91 98765 43210", "customer": {"id": "gid://shopify/Customer/9"},
         "subtotalPriceSet": money("999.00"), "totalPriceSet": money("1049.00"), "totalDiscountsSet": money("0.00"),
         "totalShippingPriceSet": money("50.00"), "totalTaxSet": money("0.00"), "totalRefundedSet": money("0.00"),
         "shippingAddress": {"city": "Pune", "provinceCode": "MH", "zip": "411001", "countryCodeV2": "IN"},
         "transactions": [], "lineItems": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
         "refunds": []}
    o.update(kw)
    return o


def page(nodes, has_next=False, cursor=None, cost=None):
    body = {"data": {"orders": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}}
    if cost:
        body["extensions"] = {"cost": cost}
    return body


def build(store, handler, sleeper, **kw):
    integ = make_integration(store, "shopify", "syn-shop.myshopify.com")
    http = make_http(handler, "shopify", sleeper, CRED, attempts=3)
    c = ShopifyConnector(integ, CRED, store, http, clock=lambda: NOW, **kw)
    http._classify = c.classify
    return c


def test_query_uses_only_verified_fields_and_no_name_address():
    q = build_orders_query(15, 3, 10, 5)
    for must in ("totalRefundedSet", "refundLineItems", "paymentGatewayNames", "pageInfo", "shopMoney"):
        assert must in q
    for must_not in ("address1", "firstName", "lastName", "paymentDetails", "customer {{"):
        assert must_not not in q


def test_query_requests_taxes_included_and_payload_keeps_it(store, sleeper):
    assert "taxesIncluded" in build_orders_query(15, 3, 10, 5)
    build(store, lambda r: httpx.Response(200, json=page([order(1, taxesIncluded=True)])), sleeper).run_sync("incremental")
    assert store.raw[(ORG, "shopify_order", "gid://shopify/Order/1")]["taxesIncluded"] is True


def test_missing_customer_hash_key_fails_at_construction(store, sleeper, monkeypatch):
    monkeypatch.delenv("CUSTOMER_HASH_KEY", raising=False)
    calls = []
    with pytest.raises(ConfigError, match="CUSTOMER_HASH_KEY"):
        build(store, lambda r: calls.append(r) or httpx.Response(200), sleeper)
    assert calls == []  # no request was made
    monkeypatch.setenv("CUSTOMER_HASH_KEY", "short")
    with pytest.raises(ConfigError):
        build(store, lambda r: httpx.Response(200), sleeper)
    build(store, lambda r: httpx.Response(200), sleeper, hash_key="explicit-key-0123456789")  # explicit key ok


def test_bad_shop_domain_rejected(store, sleeper):
    integ = make_integration(store, "shopify", "evil.example.com")
    with pytest.raises(ConfigError):
        ShopifyConnector(integ, CRED, store, make_http(lambda r: httpx.Response(200), "shopify", sleeper))


def test_backfill_pagination_pii_stripped_and_request_shape(store, sleeper):
    reqs = []

    def h(req):
        body = json.loads(req.content)
        reqs.append((req, body))
        v = body["variables"]
        if "orders(first: 1)" in body["query"]:
            return httpx.Response(200, json=page([{"id": "gid://shopify/Order/1"}]))
        if v["after"] is None:
            return httpx.Response(200, json=page([order(1), order(2)], True, "CUR1"))
        assert v["after"] == "CUR1"
        return httpx.Response(200, json=page([order(3)]))

    c = build(store, h, sleeper, window_days=10_000, backfill_start=NOW.replace(month=9, day=1, hour=0))
    res = c.run_sync("backfill")
    assert res.inserted == 3 and res.pages == 2 and res.transform_enqueued
    r0 = reqs[0][0]
    assert str(r0.url) == f"https://syn-shop.myshopify.com/admin/api/{API_VERSION}/graphql.json"
    assert r0.headers["X-Shopify-Access-Token"] == "shpat_SYNTHETIC_TOKEN_0001"
    qs = [b["variables"]["query"] for _, b in reqs[1:]]
    assert qs[0].startswith("created_at:>='2026-09-01T00:00:00Z' created_at:<'")
    stored = store.raw[(ORG, "shopify_order", "gid://shopify/Order/1")]
    assert "email" not in stored and "phone" not in stored
    assert stored["customerIdentity"]["emailHash"] == hash_email("synthetic.buyer@example.invalid")
    assert stored["customerIdentity"]["phoneHash"] == hash_phone("9876543210")
    assert stored["totalRefundedSet"]["shopMoney"]["amount"] == "0.00"  # verbatim otherwise
    assert "SYNTHETIC_TOKEN" not in json.dumps(stored)
    assert store.integrations["int-shopify"].backfill_done


def test_backfill_windows_cover_range_without_gaps(store, sleeper):
    searches = []

    def h(req):
        b = json.loads(req.content)
        if b["variables"].get("query"):
            searches.append(b["variables"]["query"])
        return httpx.Response(200, json=page([]))

    c = build(store, h, sleeper, window_days=30, backfill_start=NOW.replace(month=7, day=1, hour=0))
    c.run_sync("backfill")
    assert len(searches) == 4  # Jul1->Jul31->Aug30->Sep29->Oct14
    assert searches[0].split()[0] == "created_at:>='2026-07-01T00:00:00Z'"
    # each window starts where the previous ended
    import re
    hi = [re.findall(r"<'([^']+)'", s)[0] for s in searches]
    lo = [re.findall(r">='([^']+)'", s)[0] for s in searches]
    assert lo[1:] == hi[:-1]


def test_incremental_uses_watermark_and_is_idempotent(store, sleeper):
    seen = []

    def h(req):
        b = json.loads(req.content)
        seen.append(b["variables"].get("query"))
        return httpx.Response(200, json=page([order(1, "2026-10-12T08:00:00Z")]))

    c = build(store, h, sleeper)
    c.run_sync("incremental")           # empty store -> full pull (query None)
    assert seen[-1] is None
    c.run_sync("incremental")
    assert seen[-1] == "updated_at:>='2026-10-12T07:50:00Z'"  # watermark - 10 min overlap
    assert len(store.raw) == 1 and len(store.jobs) == 1  # second run: unchanged -> no extra job


def test_throttled_in_200_waits_by_cost_deficit_then_succeeds(store, sleeper):
    state = {"n": 0}

    def h(req):
        b = json.loads(req.content)
        if "orders(first: 1)" in b["query"]:
            return httpx.Response(200, json=page([]))
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                                             "extensions": {"cost": {"requestedQueryCost": 600, "actualQueryCost": None,
                                              "throttleStatus": {"maximumAvailable": 1000, "currentlyAvailable": 100, "restoreRate": 50}}}})
        return httpx.Response(200, json=page([order(1)]))

    c = build(store, h, sleeper, window_days=10_000, backfill_start=NOW.replace(day=1))
    c.run_sync("backfill")
    assert sleeper.calls == [10.0]  # (600-100)/50 = 10s, > jittered backoff of 0.5
    assert len(store.raw) == 1


def test_proactive_pacing_when_bucket_low(store, sleeper):
    n = {"i": 0}

    def h(req):
        b = json.loads(req.content)
        if "orders(first: 1)" in b["query"]:
            return httpx.Response(200, json=page([]))
        n["i"] += 1
        cost = {"requestedQueryCost": 400, "actualQueryCost": 120,
                "throttleStatus": {"maximumAvailable": 1000, "currentlyAvailable": 100, "restoreRate": 50}}
        return httpx.Response(200, json=page([order(n["i"])], n["i"] < 2, "C", cost))

    c = build(store, h, sleeper, window_days=10_000, backfill_start=NOW.replace(day=1))
    c.run_sync("backfill")
    assert sleeper.calls == [6.0]  # (400-100)/50 before 2nd page


def test_access_denied_is_auth_error_and_marks_integration(store, sleeper):
    h = lambda r: httpx.Response(200, json={"errors": [{"message": "Access denied for orders field.",
                                                         "extensions": {"code": "ACCESS_DENIED"}}]})
    c = build(store, h, sleeper)
    with pytest.raises(AuthError):
        c.run_sync("incremental")
    i = store.integrations["int-shopify"]
    assert i.status == "error" and "shpat_" not in i.last_error


def test_max_cost_exceeded_permanent_not_retried(store, sleeper):
    calls = {"n": 0}

    def h(r):
        calls["n"] += 1
        return httpx.Response(200, json={"errors": [{"message": "cost", "extensions": {"code": "MAX_COST_EXCEEDED"}}]})
    with pytest.raises(PermanentError):
        build(store, h, sleeper).run_sync("incremental")
    assert calls["n"] == 1


def test_http_401_and_5xx(store, sleeper):
    with pytest.raises(AuthError):
        build(store, lambda r: httpx.Response(401), sleeper).run_sync("incremental")
    seq = iter([500, 500, 500])
    from connectors.errors import TransientError
    with pytest.raises(TransientError):
        build(store, lambda r: httpx.Response(next(seq)), sleeper).run_sync("incremental")


def test_truncated_line_items_warning_and_missing_id(store, sleeper):
    o = order(1, lineItems={"pageInfo": {"hasNextPage": True, "endCursor": "x"}, "nodes": []})
    h = lambda r: httpx.Response(200, json=page([o]))
    res = build(store, h, sleeper).run_sync("incremental")
    assert any("lineItems truncated" in w for w in res.warnings)


def test_guest_order_without_contact_hashes_none(store, sleeper):
    o = order(1, email=None, phone=None, customer=None)
    build(store, lambda r: httpx.Response(200, json=page([o])), sleeper).run_sync("incremental")
    s = store.raw[(ORG, "shopify_order", "gid://shopify/Order/1")]
    assert s["customerIdentity"] == {"emailHash": None, "phoneHash": None}


def test_refund_update_changes_payload_and_requeues(store, sleeper):
    first = order(1, "2026-10-10T00:00:00Z")
    second = order(1, "2026-10-11T00:00:00Z", totalRefundedSet=money("500.00"))
    docs = iter([first, second])

    def h(r):
        if "orders(first: 1)" in json.loads(r.content)["query"]:
            return httpx.Response(200, json=page([]))
        return httpx.Response(200, json=page([next(docs)]))
    c = build(store, h, sleeper)
    c.run_sync("incremental")
    res = c.run_sync("incremental")
    assert res.updated == 1 and len(store.jobs) == 2
