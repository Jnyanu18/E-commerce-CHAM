"""Synthetic Shiprocket fixtures. Shapes follow secondary sources (see shiprocket.py docstring)."""
import httpx
import pytest

from connectors.errors import AuthError, RateLimitError, ResponseShapeError
from connectors.shiprocket import BASE, ShiprocketConnector

from .conftest import NOW, ORG, creds, make_http, make_integration

CRED = creds(email="api-user@example.invalid", password="syn-password-123")


def ship(i, status="DELIVERED"):
    return {"id": i, "order_id": 1000 + i, "awb": f"AWB{i}", "status": status, "channel_id": 7,
            "payment_method": "cod", "created_at": "2026-10-01 10:00:00"}


def build(store, handler, sleeper, **kw):
    integ = make_integration(store, "shiprocket", "acct")
    return ShiprocketConnector(integ, CRED, store, make_http(handler, "shiprocket", sleeper, CRED, attempts=3),
                               clock=lambda: NOW, **kw)


def router(pages, login=None):
    log = []

    def h(req):
        log.append(req)
        if req.url.path.endswith("/auth/login"):
            return login(req) if login else httpx.Response(200, json={"token": "tok-SYN-0001"})
        assert req.headers["Authorization"] == "Bearer tok-SYN-0001"
        p = int(req.url.params["page"])
        rows = pages[p - 1] if p <= len(pages) else []
        return httpx.Response(200, json={"data": rows, "meta": {"pagination": {
            "current_page": p, "total_pages": max(len(pages), 1)}}})
    h.log = log
    return h


def test_pagination_status_rto_kept_verbatim(store, sleeper):
    h = router([[ship(1), ship(2, "RTO DELIVERED")], [ship(3, "RTO INITIATED")]])
    res = build(store, h, sleeper).run_sync("auto")
    assert res.inserted == 3 and res.pages == 2
    assert store.raw[(ORG, "shiprocket_shipment", "2")]["status"] == "RTO DELIVERED"
    assert str(h.log[0].url) == f"{BASE}/auth/login"
    assert store.integrations["int-shiprocket"].backfill_done


def test_empty_data(store, sleeper):
    res = build(store, router([[]]), sleeper).run_sync()
    assert res.fetched == 0 and not store.jobs and store.integrations["int-shiprocket"].status == "active"


def test_rerun_is_idempotent_and_status_change_updates(store, sleeper):
    build(store, router([[ship(1, "IN TRANSIT")]]), sleeper).run_sync()
    res = build(store, router([[ship(1, "RTO INITIATED")]]), sleeper).run_sync()
    assert res.updated == 1 and len(store.raw) == 1


def test_duplicate_ids_in_page_deduped(store, sleeper):
    res = build(store, router([[ship(1), ship(1)]]), sleeper).run_sync()
    assert res.fetched == 1


def test_bad_login_is_auth_error_not_retried(store, sleeper):
    h = router([[]], login=lambda r: httpx.Response(422, json={"message": "bad"}))
    with pytest.raises(AuthError):
        build(store, h, sleeper).run_sync()
    assert len(h.log) == 1 and store.integrations["int-shiprocket"].status == "error"


def test_login_without_token_shape_error(store, sleeper):
    h = router([[]], login=lambda r: httpx.Response(200, json={"nope": 1}))
    with pytest.raises(ResponseShapeError):
        build(store, h, sleeper).run_sync()


def test_429_backs_off_then_succeeds(store, sleeper):
    base = router([[ship(1)]])
    n = {"i": 0}

    def h(req):
        if not req.url.path.endswith("/auth/login"):
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(429)
        return base(req)
    build(store, h, sleeper).run_sync()
    assert sleeper.calls == [0.5]


def test_expired_token_reauthenticates_once(store, sleeper):
    base = router([[ship(1)]])
    state = {"calls": 0, "logins": 0}

    def h(req):
        if req.url.path.endswith("/auth/login"):
            state["logins"] += 1
        else:
            state["calls"] += 1
            if state["calls"] == 2:   # first list page after validate -> expired
                return httpx.Response(401)
        return base(req)
    res = build(store, h, sleeper).run_sync()
    assert state["logins"] == 2 and res.inserted == 1


def test_max_pages_warning(store, sleeper):
    res = build(store, router([[ship(1)], [ship(2)], [ship(3)]]), sleeper, max_pages=1).run_sync()
    assert res.fetched == 1 and res.warnings
