import os, uuid
from datetime import datetime, timezone

import psycopg
import pytest

URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif("localhost" not in URL, reason="needs DATABASE_URL on localhost")

from app.dataquality import run_checks
from seed.loader import load
from worker.main import drain
from worker.queue import enqueue


@pytest.fixture()
def conn():
    c = psycopg.connect(URL, autocommit=True)
    yield c
    c.close()


def codes(rep, severity=None):
    return {c["code"]: c for c in rep["checks"] if c["severity"] != "ok" and (severity is None or c["severity"] == severity)}


def test_empty_org_reports_no_orders(conn):
    org = str(uuid.uuid4())
    conn.execute("insert into orgs(id,name) values (%s,'dq')", (org,))
    assert "no_orders" in codes(run_checks(conn, org))


def test_clean_seed_has_no_errors_but_surfaces_known_gaps(conn):
    org, _ = load(conn, name="dq clean", weeks=3)
    enqueue(conn, "transform", org, {})
    drain(conn)
    rep = run_checks(conn, org)
    assert rep["status"] != "error", codes(rep, "error")
    bad = codes(rep)
    assert "missing_cogs" not in bad and "missing_sku_lines" not in bad


def test_planted_problems_are_each_detected(conn):
    org, _ = load(conn, name="dq dirty", weeks=3)
    enqueue(conn, "transform", org, {})
    drain(conn)
    oid, ch = conn.execute("select id, channel from orders where org_id=%s limit 1", (org,)).fetchone()
    conn.execute("insert into order_items(org_id, order_id, sku, qty, unit_price_paise) values (%s,%s,'__MISSING_SKU__',1,100)", (org, oid))
    conn.execute("insert into order_items(org_id, order_id, sku, qty, unit_price_paise) values (%s,%s,'GHOST-1',1,100)", (org, oid))
    conn.execute("update orders set refunds_paise = gross_revenue_paise + 1 where id=%s", (oid,))
    conn.execute("insert into integrations(org_id, provider, account_ref, status, last_synced_at) values (%s,'shopify','x.myshopify.com','active', now() - interval '3 days')", (org,))
    conn.execute("insert into integrations(org_id, provider, account_ref, status, last_error) values (%s,'meta','act_1','error','boom')", (org,))
    conn.execute("update orders set pincode = null, city_tier = 'unknown' where id = %s", (oid,))
    rep = run_checks(conn, org, now=datetime.now(timezone.utc))
    bad = codes(rep)
    for code in ("missing_sku_lines", "skus_not_in_catalogue", "missing_cogs", "negative_or_impossible_order_amounts",
                 "stale_or_failing_integrations", "orders_without_pincode", "orders_with_unknown_city_tier"):
        assert code in bad, (code, sorted(bad))
    assert rep["status"] == "error"
    assert bad["stale_or_failing_integrations"]["count"] == 2


def test_tenant_scoping(conn):
    a, _ = load(conn, name="dq a", weeks=2)
    enqueue(conn, "transform", a, {})
    drain(conn)
    b = str(uuid.uuid4())
    conn.execute("insert into orgs(id,name) values (%s,'dq b')", (b,))
    assert "no_orders" in codes(run_checks(conn, b)) and "no_orders" not in codes(run_checks(conn, a))
