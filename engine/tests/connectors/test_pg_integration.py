"""PsycopgStore + full mocked-HTTP syncs against a REAL Postgres (migrations 0001-0011 applied).

Skipped unless DATABASE_URL is set and reachable, e.g.
  bash scripts/db_reset.sh connectors_agent
  DATABASE_URL=postgresql://postgres@localhost:54399/connectors_agent python -m pytest tests/connectors/test_pg_integration.py
HTTP is still mocked (nothing is live-tested); only the database is real.
"""
import json
import os
import uuid

import httpx
import pytest

from connectors.csv_import import import_csv
from connectors.shopify import ShopifyConnector
from connectors.store import PsycopgStore, RawRecord

from .conftest import NOW, creds, make_http
from .test_csv_import import F as CSV_FILE, MAPPING as CSV_MAPPING
from .test_shopify import CRED, order, page

URL = os.environ.get("DATABASE_URL")


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    try:
        c = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("database unreachable")
    created = []
    c.created_orgs = created
    yield c
    for o in created:
        c.execute("delete from jobs where org_id=%s", (o,))
        c.execute("delete from orgs where id=%s", (o,))  # cascades
    c.close()


def new_org(conn, provider="shopify", account="syn-shop.myshopify.com"):
    org = str(uuid.uuid4())
    conn.execute("insert into orgs (id, name) values (%s, %s)", (org, "connectors-test"))
    integ = str(uuid.uuid4())
    conn.execute("insert into integrations (id, org_id, provider, account_ref) values (%s,%s,%s,%s)",
                 (integ, org, provider, account))
    conn.created_orgs.append(org)
    return org, integ


def shopify(conn, org_integ, handler, sleeper, **kw):
    org, integ = org_integ
    store = PsycopgStore(conn)
    row = store.get_integration(integ)
    http = make_http(handler, "shopify", sleeper, CRED, attempts=2)
    c = ShopifyConnector(row, CRED, store, http, clock=lambda: NOW, **kw)
    http._classify = c.classify
    return c, store


def handler_for(docs):
    def h(req):
        if "orders(first: 1)" in json.loads(req.content)["query"]:
            return httpx.Response(200, json=page([]))
        return httpx.Response(200, json=page(list(docs)))
    return h


def count(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def test_first_sync_resync_change_status_and_watermark(conn, sleeper):
    org, integ = new_org(conn)
    docs = [order(1, "2026-10-10T10:00:00Z"), order(2, "2026-10-12T08:00:00Z")]
    c, store = shopify(conn, (org, integ), handler_for(docs), sleeper)
    r1 = c.run_sync("incremental")
    assert (r1.inserted, r1.updated, r1.unchanged) == (2, 0, 0) and r1.transform_enqueued
    assert count(conn, "select count(*) from raw_records where org_id=%s", org) == 2
    assert count(conn, "select count(*) from jobs where org_id=%s and kind='transform' and status='queued'", org) == 1
    status, synced, err = conn.execute("select status, last_synced_at, last_error from integrations where id=%s",
                                       (integ,)).fetchone()
    assert status == "active" and synced == NOW and err is None
    # watermark derived by JSON-path max over stored raw
    assert store.max_payload_value(org, "shopify_order", ("updatedAt",)) == "2026-10-12T08:00:00Z"
    assert c.watermark().isoformat() == "2026-10-12T08:00:00+00:00"
    assert store.max_payload_value(org, "nothing", ("updatedAt",)) is None
    # identical re-sync
    r2 = shopify(conn, (org, integ), handler_for(docs), sleeper)[0].run_sync("incremental")
    assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 2) and not r2.transform_enqueued
    assert count(conn, "select count(*) from raw_records where org_id=%s", org) == 2
    assert count(conn, "select count(*) from jobs where org_id=%s", org) == 1
    # changed record
    changed = [order(1, "2026-10-13T00:00:00Z", totalRefundedSet={"shopMoney": {"amount": "5.00", "currencyCode": "INR"}}),
               docs[1]]
    r3 = shopify(conn, (org, integ), handler_for(changed), sleeper)[0].run_sync("incremental")
    assert (r3.inserted, r3.updated, r3.unchanged) == (0, 1, 1) and r3.transform_enqueued
    assert count(conn, "select count(*) from jobs where org_id=%s", org) == 2
    p = conn.execute("select payload from raw_records where org_id=%s and external_id=%s",
                     (org, "gid://shopify/Order/1")).fetchone()[0]
    assert p["totalRefundedSet"]["shopMoney"]["amount"] == "5.00" and "email" not in p


def test_failure_updates_status_and_last_error_without_secrets(conn, sleeper):
    org, integ = new_org(conn)
    h = lambda r: httpx.Response(200, json={"errors": [{"message": "no", "extensions": {"code": "ACCESS_DENIED"}}]})
    c, _ = shopify(conn, (org, integ), h, sleeper)
    from connectors.errors import AuthError
    with pytest.raises(AuthError):
        c.run_sync("incremental")
    status, err = conn.execute("select status, last_error from integrations where id=%s", (integ,)).fetchone()
    assert status == "error" and err.startswith("auth_error") and "shpat_" not in err
    # recovery clears last_error
    shopify(conn, (org, integ), handler_for([order(1)]), sleeper)[0].run_sync("incremental")
    status, err = conn.execute("select status, last_error from integrations where id=%s", (integ,)).fetchone()
    assert status == "active" and err is None


def test_backfill_sets_backfill_done(conn, sleeper):
    org, integ = new_org(conn)
    c, _ = shopify(conn, (org, integ), handler_for([]), sleeper, window_days=10_000,
                   backfill_start=NOW.replace(day=1, hour=0))
    c.run_sync("auto")
    assert conn.execute("select backfill_done from integrations where id=%s", (integ,)).fetchone()[0] is True


def test_two_org_tenant_isolation_same_external_id(conn, sleeper):
    a, b = new_org(conn), new_org(conn)
    shopify(conn, a, handler_for([order(1, "2026-10-10T00:00:00Z")]), sleeper)[0].run_sync("incremental")
    shopify(conn, b, handler_for([order(1, "2026-10-11T00:00:00Z")]), sleeper)[0].run_sync("incremental")
    for org, upd in ((a[0], "2026-10-10T00:00:00Z"), (b[0], "2026-10-11T00:00:00Z")):
        assert count(conn, "select count(*) from raw_records where org_id=%s", org) == 1
        assert PsycopgStore(conn).max_payload_value(org, "shopify_order", ("updatedAt",)) == upd
    assert PsycopgStore(conn).existing_external_ids(a[0], "shopify_order", ["gid://shopify/Order/1"]) == {"gid://shopify/Order/1"}
    assert PsycopgStore(conn).existing_external_ids(a[0], "shopify_order", ["nope"]) == set()
    assert count(conn, "select count(*) from jobs where org_id=%s", b[0]) == 1


def test_upsert_raw_counts_mixed_batch(conn):
    org, _ = new_org(conn)
    s = PsycopgStore(conn)
    mk = lambda i, v: RawRecord(org, "x", str(i), {"v": v})
    assert s.upsert_raw([mk(1, 1), mk(2, 1)]).inserted == 2
    r = s.upsert_raw([mk(1, 1), mk(2, 2), mk(3, 1)])
    assert (r.inserted, r.updated, r.unchanged) == (1, 1, 1)
    assert s.upsert_raw([]).changed == 0


def test_csv_mapping_save_load_and_import_idempotent(conn):
    org, _ = new_org(conn, "csv", "upload")
    s = PsycopgStore(conn)
    assert s.get_csv_mapping(org, "syntheticmkt", "orders") is None
    r1 = import_csv(CSV_FILE, CSV_MAPPING, "syntheticmkt", "orders", org, s)
    assert r1.imported.inserted == 2 and r1.transform_enqueued
    assert s.get_csv_mapping(org, "syntheticmkt", "orders") == CSV_MAPPING
    r2 = import_csv(CSV_FILE, CSV_MAPPING, "syntheticmkt", "orders", org, s)
    assert r2.imported.unchanged == 2 and r2.already_imported == 2 and not r2.transform_enqueued
    assert count(conn, "select count(*) from raw_records where org_id=%s and source='csv_syntheticmkt'", org) == 2
    assert count(conn, "select count(*) from jobs where org_id=%s", org) == 1
    s.save_csv_mapping(org, "syntheticmkt", "orders", {**CSV_MAPPING, "delimiter": ","})  # upsert, no duplicate
    assert count(conn, "select count(*) from csv_mappings where org_id=%s", org) == 1
    assert "delimiter" in s.get_csv_mapping(org, "syntheticmkt", "orders")


def test_ledger_run_transform_consumes_shopify_raw(conn, sleeper):
    """Shape-compat check (import-only use of the ledger). See PROGRESS.md > Integration Needed."""
    ledger = pytest.importorskip("ledger.pipeline")
    from ledger.pg_repository import PsycopgRepository
    org, integ = new_org(conn)
    o = order(7, "2026-10-12T08:00:00Z")
    o["lineItems"]["nodes"] = [{"id": "gid://shopify/LineItem/1", "name": "Tee", "title": "Tee", "sku": "TEE-1",
                                "quantity": 2, "currentQuantity": 2,
                                "originalUnitPriceSet": {"shopMoney": {"amount": "499.50", "currencyCode": "INR"}},
                                "discountedUnitPriceSet": {"shopMoney": {"amount": "499.50", "currencyCode": "INR"}},
                                "totalDiscountSet": {"shopMoney": {"amount": "0.00", "currencyCode": "INR"}}}]
    shopify(conn, (org, integ), handler_for([o]), sleeper)[0].run_sync("incremental")
    summary = ledger.run_transform(PsycopgRepository(conn), org)
    n_orders = count(conn, "select count(*) from orders where org_id=%s", org)
    n_items = count(conn, "select count(*) from order_items where org_id=%s", org)
    print("LEDGER SUMMARY", summary.as_dict(), "orders", n_orders, "items", n_items)
    assert n_orders == 1 and n_items == 1, summary.as_dict()
    row = conn.execute("select gross_revenue_paise, shipping_charged_paise, pincode, customer_key from orders "
                       "where org_id=%s", (org,)).fetchone()
    assert row[0] == 99900 and row[1] == 5000 and row[2] == "411001" and row[3] is not None
