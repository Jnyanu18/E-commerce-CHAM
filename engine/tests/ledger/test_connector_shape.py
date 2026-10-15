"""Ledger fed with the CONNECTORS' synthetic Shopify GraphQL fixtures (tests/connectors/test_shopify.py::order),
passed through the connector's own ``sanitize_order`` (PII -> HMAC identity). Test-only imports of connector code.

Runs on the in-memory repository always, and on real Postgres when DATABASE_URL is reachable.
All data SYNTHETIC; nothing live-tested.
"""
import os

import pytest

from app import identity
from connectors.shopify import sanitize_order
from ledger.pipeline import run_transform
from ledger.repository import InMemoryRepository
from tests.connectors.test_shopify import money, order
from tests.ledger.fixtures import ORG

LINE = {"id": "gid://shopify/LineItem/1", "name": "Tee", "title": "Tee", "sku": "TEE-1", "quantity": 2, "currentQuantity": 2,
        "originalUnitPriceSet": money("499.50"), "discountedUnitPriceSet": money("499.50"), "totalDiscountSet": money("0.00")}


def docs():
    prepaid = order(7, "2026-10-12T08:00:00Z")
    prepaid["lineItems"]["nodes"] = [LINE]
    cod = order(8, "2026-10-12T09:00:00Z", paymentGatewayNames=["Cash on Delivery (COD)"], email=None,
                displayFulfillmentStatus="FULFILLED", totalPriceSet=money("499.50"), subtotalPriceSet=money("499.50"),
                totalShippingPriceSet=money("0.00"))
    cod["lineItems"]["nodes"] = [dict(LINE, quantity=1, currentQuantity=1, sku="TEE-2")]
    refunded = order(9, "2026-10-12T10:00:00Z", totalRefundedSet=money("200.00"),
                     refunds=[{"id": "gid://shopify/Refund/1", "totalRefundedSet": money("200.00")}])
    refunded["lineItems"]["nodes"] = [LINE]
    cancelled = order(10, "2026-10-12T11:00:00Z", cancelledAt="2026-10-12T12:00:00Z", phone=None)
    cancelled["lineItems"]["nodes"] = [LINE]
    return [sanitize_order(d) for d in (prepaid, cod, refunded, cancelled)]


def check(rows):
    """rows: {external_id: dict(status, payment_method, gross, shipping, refunds, pincode, city, customer_key, items)}"""
    assert set(rows) == {"7", "8", "9", "10"}
    p, c, r, x = rows["7"], rows["8"], rows["9"], rows["10"]
    assert (p["gross"], p["shipping"], p["pincode"], p["city"], p["payment_method"], p["status"]) == (99900, 5000, "411001", "Pune", "prepaid", "placed")
    assert p["items"] == {("TEE-1", 2, 49950)}
    assert p["customer_key"] == identity.hash_email("synthetic.buyer@example.invalid")      # == connector's HMAC, same key
    assert c["payment_method"] == "cod" and c["status"] == "shipped" and c["items"] == {("TEE-2", 1, 49950)}
    assert c["customer_key"] == identity.hash_phone("9876543210")                           # email absent -> phone hash
    assert r["refunds"] == 20000 and p["refunds"] == 0
    assert x["status"] == "cancelled" and x["customer_key"] == identity.hash_email("synthetic.buyer@example.invalid")


def test_connector_fixtures_through_run_transform_in_memory():
    repo = InMemoryRepository()
    for d in docs():
        repo.add_raw_record(ORG, "shopify_order", d["id"], d)
    s = run_transform(repo, ORG)
    assert s.errors == [] and s.orders_applied == 4
    rows = {}
    for (_, ch, ext), o in repo.orders.items():
        assert ch == "shopify"
        rows[ext] = dict(status=o["status"], payment_method=o["payment_method"], gross=o["gross_revenue_paise"],
                         shipping=o["shipping_charged_paise"], refunds=o["refunds_paise"], pincode=o["pincode"], city=o["city"],
                         customer_key=o["customer_key"],
                         items={(i["sku"], i["qty"], i["unit_price_paise"]) for i in repo.items.values() if i["order_id"] == o["id"]})
    check(rows)
    assert any(i["issue"].startswith("taxes_included_unknown") for i in s.issues)        # documented assumption, surfaced
    assert repo.orders[(ORG, "shopify", "7")]["ordered_at"] == "2026-10-01T10:00:00+00:00"


def test_connector_fixtures_rerun_is_idempotent_in_memory():
    repo = InMemoryRepository()
    for d in docs():
        repo.add_raw_record(ORG, "shopify_order", d["id"], d)
    run_transform(repo, ORG)
    snap = (dict(repo.orders), dict(repo.items))
    run_transform(repo, ORG)
    assert (dict(repo.orders), dict(repo.items)) == snap


def test_connector_fixtures_through_run_transform_on_real_postgres():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.types.json import Jsonb
    from ledger.pg_repository import PsycopgRepository
    try:
        conn = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("database unreachable")
    try:
        conn.execute("delete from orgs where id=%s", (ORG,))
        conn.execute("insert into orgs (id, name) values (%s, 'connector-shape')", (ORG,))
        for d in docs():
            conn.execute("insert into raw_records (org_id, source, external_id, payload) values (%s,'shopify_order',%s,%s)",
                         (ORG, d["id"], Jsonb(d)))
        repo = PsycopgRepository(conn)
        s = run_transform(repo, ORG)
        assert s.errors == [] and s.orders_applied == 4
        run_transform(repo, ORG)                                                          # idempotent
        rows = {}
        for ext, st, pm, gross, ship, ref, pin, city, ck, oid in conn.execute(
                "select external_id, status, payment_method, gross_revenue_paise, shipping_charged_paise, refunds_paise, pincode, "
                "city, customer_key, id from orders where org_id=%s", (ORG,)).fetchall():
            items = {(a, b, c) for a, b, c in conn.execute("select sku, qty, unit_price_paise from order_items where order_id=%s", (oid,))}
            rows[ext] = dict(status=st, payment_method=pm, gross=gross, shipping=ship, refunds=ref, pincode=pin, city=city,
                             customer_key=ck, items=items)
        check(rows)
        assert conn.execute("select count(*) from order_items where org_id=%s", (ORG,)).fetchone()[0] == 4
    finally:
        conn.execute("delete from orgs where id=%s", (ORG,))
        conn.close()
