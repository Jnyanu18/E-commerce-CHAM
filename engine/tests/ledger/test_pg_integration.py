"""run_transform end-to-end through PsycopgRepository against a REAL Postgres (migrations 0001-0011 applied).

Skipped unless DATABASE_URL is set and reachable, e.g.
  DATABASE_URL=postgresql://postgres@localhost:54399/ledger_agent
The target DB must be a scratch database prepared as in supabase/tests/run.sh. Test orgs use fixed ids and are
deleted (cascade) before and after each test. Fixtures are SYNTHETIC.
"""
import os
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.types.json import Jsonb  # noqa: E402

from ledger.pg_repository import PsycopgRepository  # noqa: E402
from ledger.pipeline import run_transform  # noqa: E402
from reconciliation.report import reconcile_snapshot  # noqa: E402
from tests.ledger import fixtures as fx  # noqa: E402

URL = os.environ.get("DATABASE_URL")


def _connect():
    if not URL:
        return None
    try:
        return psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except Exception:
        return None


@pytest.fixture()
def conn():
    c = _connect()
    if c is None:
        pytest.skip("DATABASE_URL not set or database unreachable")
    ids = [fx.ORG, fx.ORG2]
    c.execute("delete from orgs where id = any(%s::uuid[])", (ids,))
    for i, n in zip(ids, ("ledger-test-1", "ledger-test-2")):
        c.execute("insert into orgs (id, name) values (%s, %s)", (i, n))
    yield c
    c.execute("delete from orgs where id = any(%s::uuid[])", (ids,))
    c.close()


def raw(conn, org, source, ext, payload):
    conn.execute("insert into raw_records (org_id, source, external_id, payload) values (%s,%s,%s,%s) "
                 "on conflict (org_id, source, external_id) do update set payload = excluded.payload",
                 (org, source, ext, Jsonb(payload)))


def load_all(conn, org=fx.ORG):
    for o in (fx.SHOPIFY_NORMAL, fx.SHOPIFY_COD, fx.SHOPIFY_RTO, fx.SHOPIFY_RETURN, fx.SHOPIFY_REFUND,
              fx.SHOPIFY_MISSING_SKU, fx.SHOPIFY_TAX_EXCLUSIVE, fx.SHOPIFY_CANCELLED):
        raw(conn, org, "shopify_order", str(o["id"]), o)
    for s in (fx.SHIPMENT_DELIVERED, fx.SHIPMENT_RTO, fx.SHIPMENT_RETURN, fx.SHIPMENT_FORWARD_1004, fx.SHIPMENT_ORPHAN):
        raw(conn, org, "shiprocket_shipment", str(s["id"]), s)
    for i, r in enumerate(fx.AMAZON_OK + fx.AMAZON_MISMATCH):
        raw(conn, org, "csv_amazon", f"row{i}", r)
    raw(conn, org, "csv_flipkart", "f0", fx.FLIPKART_UNSETTLED[0])
    raw(conn, org, "csv_blinkit", "b0", fx.BLINKIT_CANCELLED[0])


TABLES = ("orders", "order_items", "fees", "returns", "settlements", "settlement_items", "shipments")


def counts(conn, org=fx.ORG):
    return {t: conn.execute(f"select count(*) from {t} where org_id=%s", (org,)).fetchone()[0] for t in TABLES}


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def order_row(conn, ext, channel="shopify", org=fx.ORG):
    return one(conn, "select id, status, payment_method, refunds_paise, gross_revenue_paise, city_tier, customer_key "
                     "from orders where org_id=%s and channel=%s and external_id=%s", (org, channel, ext))


def test_full_flow_against_real_postgres(conn):
    load_all(conn)
    s = run_transform(PsycopgRepository(conn), fx.ORG)
    assert [e for e in s.errors] == []
    c = counts(conn)
    assert c["orders"] == 12 and c["shipments"] == 5 and c["settlements"] == 3 and c["settlement_items"] == 3
    # normal prepaid multi-item
    oid, status, pm, refunds, gross, tier, ckey = order_row(conn, "5001")
    assert (status, pm, gross) == ("shipped", "prepaid", 199700) and tier == "metro" and len(ckey) == 64
    assert one(conn, "select count(*), sum(qty) from order_items where order_id=%s", (oid,)) == (2, 3)
    # COD delivered via shipment; fees and remittance
    cod = order_row(conn, "5002")
    assert cod[1:3] == ("delivered", "cod")
    assert one(conn, "select type, amount_paise, source from fees where order_id=%s and type='payment_gateway'", (cod[0],)) == ("payment_gateway", 2500, "api")
    h = one(conn, "select gross_paise, fees_paise, net_paise from settlements where org_id=%s and channel='shiprocket_cod' and external_id='REM-1'", (fx.ORG,))
    assert h == (119900, 2500, 117400)
    assert one(conn, "select order_id from settlement_items where org_id=%s and order_external_id='5002'", (fx.ORG,))[0] == cod[0]
    # RTO
    rto = order_row(conn, "5003")
    assert rto[1] == "rto"
    assert one(conn, "select cost_paise from returns where order_id=%s and type='RTO'", (rto[0],))[0] == 7000
    # return + partial refund
    ret = order_row(conn, "5004")
    assert ret[1] == "returned" and ret[3] == 30000
    assert one(conn, "select cost_paise from returns where order_id=%s and type='customer_return'", (ret[0],))[0] == 8000
    # refund-only (goodwill) keeps status, no return row
    ref = order_row(conn, "5005")
    assert ref[3] == 34900 and one(conn, "select count(*) from returns where order_id=%s", (ref[0],))[0] == 0
    # missing sku placeholder
    ms = order_row(conn, "5006")
    assert {r[0] for r in conn.execute("select sku from order_items where order_id=%s", (ms[0],))} == {"__MISSING_SKU__", "OIL-100"}
    # unlinked shipment stored, order-side deferred
    assert one(conn, "select order_id, order_external_id from shipments where external_id='9005'")[0] is None
    assert len(s.deferred) == 1
    # timestamps UTC
    assert one(conn, "select ordered_at at time zone 'UTC' from orders where id=%s", (oid,))[0].isoformat() == "2026-09-10T04:30:00"


def test_duplicate_import_row_counts_unchanged(conn):
    load_all(conn)
    repo = PsycopgRepository(conn)
    run_transform(repo, fx.ORG)
    before = counts(conn)
    snap = one(conn, "select sum(gross_revenue_paise), sum(refunds_paise) from orders where org_id=%s", (fx.ORG,))
    fees = one(conn, "select count(*), sum(amount_paise) from fees where org_id=%s", (fx.ORG,))
    for _ in range(2):
        run_transform(repo, fx.ORG)
    # same Shopify order re-landed under a different raw key must not create a second order
    raw(conn, fx.ORG, "shopify_order", "dup-key", fx.SHOPIFY_NORMAL)
    run_transform(repo, fx.ORG)
    assert counts(conn) == before
    assert one(conn, "select sum(gross_revenue_paise), sum(refunds_paise) from orders where org_id=%s", (fx.ORG,)) == snap
    assert one(conn, "select count(*), sum(amount_paise) from fees where org_id=%s", (fx.ORG,)) == fees


def test_estimated_fees_do_not_overwrite_or_duplicate_actuals(conn):
    raw(conn, fx.ORG, "shopify_order", "5002", fx.SHOPIFY_COD)
    raw(conn, fx.ORG, "shiprocket_shipment", "9001", fx.SHIPMENT_DELIVERED)
    repo = PsycopgRepository(conn)
    rates = {"shopify": {"payment_gateway": 200, "commission": 100}}
    run_transform(repo, fx.ORG, fee_rates=rates)
    run_transform(repo, fx.ORG, fee_rates=rates)
    rows = set(conn.execute("select type, source, amount_paise from fees where org_id=%s", (fx.ORG,)).fetchall())
    assert rows == {("payment_gateway", "api", 2500), ("shipping", "api", 6000), ("commission", "estimated", 1199)}


def test_out_of_order_status_converges(conn):
    repo = PsycopgRepository(conn)
    raw(conn, fx.ORG, "shiprocket_shipment", "9002", fx.SHIPMENT_RTO)
    run_transform(repo, fx.ORG)
    assert one(conn, "select count(*) from orders where org_id=%s", (fx.ORG,))[0] == 0          # nothing invented
    assert one(conn, "select status from shipments where external_id='9002'")[0] == "rto_delivered"
    raw(conn, fx.ORG, "shopify_order", "5003", fx.SHOPIFY_RTO)
    run_transform(repo, fx.ORG)
    o = order_row(conn, "5003")
    assert o[1] == "rto"
    assert one(conn, "select order_id from shipments where external_id='9002'")[0] == o[0]       # late link
    raw(conn, fx.ORG, "shiprocket_shipment", "late", {"id": 77, "channel_order_id": "5003", "status": "DELIVERED"})
    run_transform(repo, fx.ORG)
    assert order_row(conn, "5003")[1] == "rto"                                                   # no regression


def test_rto_orders_do_not_get_full_amount_in_refunds(conn):
    """Answer to the metrics agent: RTO status does NOT imply refunds_paise; it stays what the source reported."""
    raw(conn, fx.ORG, "shopify_order", "5003", fx.SHOPIFY_RTO)
    raw(conn, fx.ORG, "shiprocket_shipment", "9002", fx.SHIPMENT_RTO)
    run_transform(PsycopgRepository(conn), fx.ORG)
    o = order_row(conn, "5003")
    assert o[1] == "rto" and o[3] == 0


def test_tenant_isolation(conn):
    load_all(conn, fx.ORG)
    raw(conn, fx.ORG2, "shopify_order", "5001", fx.SHOPIFY_NORMAL)
    repo = PsycopgRepository(conn)
    run_transform(repo, fx.ORG)
    assert counts(conn, fx.ORG2)["orders"] == 0
    run_transform(repo, fx.ORG2)
    assert counts(conn, fx.ORG2)["orders"] == 1 and counts(conn, fx.ORG)["orders"] == 12
    assert one(conn, "select count(*) from order_items i join orders o on o.id=i.order_id where i.org_id <> o.org_id")[0] == 0


def test_settlement_mismatch_reconciles_from_real_tables(conn):
    load_all(conn)
    repo = PsycopgRepository(conn)
    run_transform(repo, fx.ORG)
    snap = repo.fetch_snapshot(fx.ORG, date(2026, 8, 1), date(2026, 9, 30))
    rep = reconcile_snapshot(fx.ORG, date(2026, 8, 1), date(2026, 9, 30), snap, as_of=date(2026, 10, 14))
    codes = {(c["channel"], d["code"], d["order_external_id"]): d for c in rep["channels"] for d in c["discrepancies"]}
    d = codes[("amazon", "settlement_amount_mismatch", "AMZ-2")]
    assert (d["expected_paise"], d["actual_paise"], d["diff_paise"]) == (68000, 67500, -500)
    assert ("amazon", "settlement_amount_mismatch", "AMZ-1") not in codes
    assert ("shopify", "settlement_amount_mismatch", "5002") not in codes
    assert ("flipkart", "settlement_missing", "FK-1") in codes
    # DB-level cross-check with the database agent's view
    v = one(conn, "select net_paise, items_net_paise, net_diff_paise, unmatched_item_count from settlement_reconciliation "
                  "where org_id=%s and external_id='S-101'", (fx.ORG,))
    assert v == (67500, 67500, 0, 0)


def test_settlement_item_waits_for_its_order_then_links(conn):
    repo = PsycopgRepository(conn)
    raw(conn, fx.ORG, "shiprocket_shipment", "9001", fx.SHIPMENT_DELIVERED)   # remittance arrives before the order
    run_transform(repo, fx.ORG)
    # never stored under the unresolved reference (it would later duplicate the resolved one)
    assert counts(conn)["settlement_items"] == 0 and counts(conn)["shipments"] == 1
    raw(conn, fx.ORG, "shopify_order", "5002", fx.SHOPIFY_COD)
    run_transform(repo, fx.ORG)
    assert one(conn, "select order_external_id, order_id is not null from settlement_items")[0:2] == ("5002", True)
    assert one(conn, "select order_id is not null from shipments where external_id='9001'")[0] is True
