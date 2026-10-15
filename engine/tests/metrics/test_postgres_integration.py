"""PostgresRepository vs InMemoryRepository on identical hand-calculated fixtures.

Skipped unless env DATABASE_URL points at a reachable Postgres with migrations 0001-0011 applied, e.g.
  DATABASE_URL=postgresql://postgres@localhost:54399/metrics_agent
Each test uses fresh random org ids and deletes them afterwards.
"""
import os
import time
import uuid
from datetime import date

import pytest

from metrics import DateRange, METRICS, PostgresRepository, contribution_margin, net_revenue
from tests.metrics.fx import ORG, OTHER_ORG, PERIOD, repo_with_spend

URL = os.environ.get("DATABASE_URL")
psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def conn():
    if not URL:
        pytest.skip("DATABASE_URL not set")
    try:
        c = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database unreachable: {e}")
    yield c
    c.close()


def _uid(ns, s):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ns}/{s}"))


@pytest.fixture()
def loaded(conn):
    """Load fx.repo_with_spend() rows into the real tables under a fresh org; returns (mem_repo, pg_repo, db_org)."""
    mem = repo_with_spend()
    ns = str(uuid.uuid4())
    org = _uid(ns, ORG)
    other = _uid(ns, OTHER_ORG)
    cur = conn.cursor()
    for o in (org, other):
        cur.execute("insert into orgs(id, name) values (%s, 'metrics-it')", (o,))
    for o in mem._orders[ORG]:
        cur.execute(
            "insert into orders(id, org_id, channel, external_id, ordered_at, customer_key, city, city_tier, pincode, payment_method,"
            " status, gross_revenue_paise, discounts_paise, taxes_paise, shipping_charged_paise, refunds_paise)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (_uid(ns, o.id), org, o.channel, o.external_id, o.ordered_at, o.customer_key, o.city, o.city_tier or "unknown", o.pincode,
             o.payment_method, o.status, o.gross_revenue_paise, o.discounts_paise, o.taxes_paise, o.shipping_charged_paise, o.refunds_paise),
        )
    for i in mem._items[ORG]:
        cur.execute("insert into order_items(org_id, order_id, sku, qty, unit_price_paise) values (%s,%s,%s,%s,%s)",
                    (org, _uid(ns, i.order_id), i.sku, i.qty, i.unit_price_paise))
    for f in mem._fees[ORG]:
        cur.execute("insert into fees(org_id, order_id, type, amount_paise, source) values (%s,%s,%s,%s,%s)",
                    (org, _uid(ns, f.order_id), f.type, f.amount_paise, f.source))
    for r in mem._returns[ORG]:
        cur.execute("insert into returns(org_id, order_id, type, cost_paise) values (%s,%s,%s,%s)",
                    (org, _uid(ns, r.order_id), r.type, r.cost_paise))
    for s in mem._skus[ORG]:
        cur.execute("insert into skus(org_id, sku, category) values (%s,%s,%s)", (org, s.sku, s.category))
    for c in mem._sku_cogs[ORG]:
        cur.execute("insert into sku_cogs(org_id, sku, effective_from, cogs_paise) values (%s,%s,%s,%s)", (org, c.sku, c.effective_from, c.cogs_paise))
    for a in mem._ad_spend[ORG]:
        cur.execute("insert into ad_spend_daily(org_id, platform, campaign_id, date, spend_paise, platform_reported_revenue_paise)"
                    " values (%s,%s,%s,%s,%s,%s)", (org, a.platform, a.campaign_id, a.date, a.spend_paise, a.platform_reported_revenue_paise))
    # a row for the other tenant that must never leak
    cur.execute("insert into orders(org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)"
                " values (%s,'shopify','x','2026-09-15 06:00:00+00','prepaid',777777)", (other,))
    yield mem, PostgresRepository(conn), org, other
    cur.execute("delete from orgs where id in (%s,%s)", (org, other))


GROUPS = [None, "channel", "sku", "category", "city", "city_tier", "pincode", "payment_method", "week"]


def _norm(d):
    return d


@pytest.mark.parametrize("name", ["net_revenue", "contribution_margin", "expected_rto_loss"])
@pytest.mark.parametrize("group_by", GROUPS)
def test_ledger_metrics_identical_to_in_memory(loaded, name, group_by):
    mem, pg, org, _ = loaded
    kw = {"min_history": 1} if name == "expected_rto_loss" else {}
    a = METRICS[name].callable(ORG, PERIOD, None, group_by, repo=mem, **kw).to_dict()
    b = METRICS[name].callable(org, PERIOD, None, group_by, repo=pg, **kw).to_dict()
    assert a == b


@pytest.mark.parametrize("name", ["mer", "blended_cac", "contribution_after_marketing"])
@pytest.mark.parametrize("group_by", [None, "week"])
def test_marketing_metrics_identical_to_in_memory(loaded, name, group_by):
    mem, pg, org, _ = loaded
    a = METRICS[name].callable(ORG, PERIOD, None, group_by, repo=mem).to_dict()
    b = METRICS[name].callable(org, PERIOD, None, group_by, repo=pg).to_dict()
    assert a == b


def test_platform_roas_and_filters_identical(loaded):
    mem, pg, org, _ = loaded
    for name in ("ad_spend", "platform_reported_revenue"):
        for gb in (None, "platform", "week"):
            a = METRICS[name].callable(ORG, PERIOD, None, gb, repo=mem).to_dict()
            assert a == METRICS[name].callable(org, PERIOD, None, gb, repo=pg).to_dict()
    assert METRICS["ad_spend"].callable(org, PERIOD, None, "platform", repo=pg).to_dict()["groups"] == [
        {"key": "google", "value": 30000}, {"key": "meta", "value": 70000}]
    assert METRICS["platform_reported_revenue"].callable(org, PERIOD, None, None, repo=pg).value == 245000
    for gb in (None, "platform", "week"):
        assert METRICS["platform_roas"].callable(ORG, PERIOD, None, gb, repo=mem).to_dict() == \
            METRICS["platform_roas"].callable(org, PERIOD, None, gb, repo=pg).to_dict()
    f = {"channel": ["amazon"]}
    assert net_revenue(ORG, PERIOD, f, repo=mem).to_dict() == net_revenue(org, PERIOD, f, repo=pg).to_dict()


def test_exact_hand_calculated_values_from_postgres(loaded):
    _, pg, org, _ = loaded
    # See test_finance for the arithmetic.
    assert net_revenue(org, PERIOD, repo=pg).value == 130000
    cm = contribution_margin(org, PERIOD, repo=pg)
    assert cm.value == 87500  # effective-dated COGS (A 4000 -> 5000 from 15 Sep) and api-over-estimated fee precedence
    assert contribution_margin(org, PERIOD, group_by="sku", repo=pg).to_dict()["groups"] == [
        {"key": "A", "value": 82667}, {"key": "B", "value": 4833}]


def test_ist_week_boundary_through_timestamptz(loaded):
    # O3 = 2026-09-20 18:00 UTC = 23:30 IST on the 20th; O4 = 2026-09-21 18:30 UTC = 00:00 IST on the 22nd (generated column).
    _, pg, org, _ = loaded
    r = net_revenue(org, DateRange(date(2026, 9, 14), date(2026, 9, 27)), group_by="week", repo=pg)
    assert {g.key: g.value for g in r.groups} == {"2026-09-14": 130000, "2026-09-21": 7000}
    assert net_revenue(org, DateRange(date(2026, 9, 20), date(2026, 9, 20)), repo=pg).data_quality.row_count == 1
    assert net_revenue(org, DateRange(date(2026, 9, 16), date(2026, 9, 16)), repo=pg).value is None


def test_tenant_isolation(loaded):
    _, pg, org, other = loaded
    assert net_revenue(other, PERIOD, repo=pg).value == 777777
    assert net_revenue(org, PERIOD, repo=pg).value == 130000
    assert net_revenue(str(uuid.uuid4()), PERIOD, repo=pg).value is None


def test_tier_values_are_canonical(loaded):
    _, pg, org, _ = loaded
    r = net_revenue(org, PERIOD, group_by="city_tier", repo=pg)
    assert {g.key for g in r.groups} == {"metro"}
    assert contribution_margin(org, DateRange(date(2026, 9, 22), date(2026, 9, 22)), group_by="city_tier", repo=pg).groups[0].key == "tier_1"


def test_performance_50k_orders_quarter_by_channel(conn):
    org = str(uuid.uuid4())
    cur = conn.cursor()
    cur.execute("insert into orgs(id, name) values (%s, 'metrics-perf')", (org,))
    try:
        cur.execute(
            "insert into orders(org_id, channel, external_id, ordered_at, customer_key, city, city_tier, payment_method, status,"
            " gross_revenue_paise, discounts_paise, taxes_paise, refunds_paise)"
            " select %s, (array['shopify','amazon','flipkart','blinkit'])[1 + g %% 4], 'o' || g,"
            " timestamptz '2026-07-01 00:00:00+05:30' + (g %% 92) * interval '1 day' + (g %% 1440) * interval '1 minute',"
            " 'c' || (g %% 20000), 'Mumbai', 'metro', case when g %% 3 = 0 then 'cod' else 'prepaid' end, 'delivered',"
            " 100000 + g %% 5000, 5000, 9000, case when g %% 50 = 0 then 10000 else 0 end from generate_series(1, 50000) g", (org,))
        cur.execute("insert into skus(org_id, sku, category) select %s, 'S' || i, 'cat' || (i %% 3) from generate_series(1,20) i", (org,))
        cur.execute("insert into sku_cogs(org_id, sku, effective_from, cogs_paise) select %s, 'S' || i, date '2026-01-01', 20000 from generate_series(1,20) i", (org,))
        cur.execute("insert into order_items(org_id, order_id, sku, qty, unit_price_paise) select org_id, id, 'S' || (1 + (random()*19)::int), 1, gross_revenue_paise from orders where org_id = %s", (org,))
        cur.execute("insert into fees(org_id, order_id, type, amount_paise, source) select org_id, id, t, 3000, 'api' from orders, unnest(array['commission','shipping']) t where org_id = %s", (org,))
        cur.execute("analyze orders"); cur.execute("analyze order_items"); cur.execute("analyze fees")
        q = DateRange(date(2026, 7, 1), date(2026, 9, 30))
        t0 = time.perf_counter()
        r = contribution_margin(org, q, group_by="channel", repo=PostgresRepository(conn))
        dt = time.perf_counter() - t0
        print(f"\nPERF contribution_margin quarter x channel on 50k orders: {dt:.2f}s, rows={r.data_quality.row_count}")
        assert r.data_quality.row_count == 50000 and len(r.groups) == 4
        assert dt < 20
    finally:
        cur.execute("delete from orgs where id = %s", (org,))
