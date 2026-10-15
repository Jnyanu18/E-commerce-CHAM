"""Profit waterfall components and the exact identities (hand-calculated on fx.main_repo)."""
from datetime import date
from decimal import Decimal

import pytest

from metrics import (METRICS, WATERFALL_STEPS, DateRange, InMemoryRepository, MetricError, cogs, contribution_margin,
                     contribution_margin_pct, fees, gross_revenue, net_revenue, shipping_cost)
from metrics.repository import FeeRow, ItemRow, SkuCogsRow
from tests.metrics.fx import ORG, PERIOD, main_repo, order

NET = ("gross_revenue", "discounts", "taxes", "refunds")
CM = ("cogs", "fees", "shipping_cost", "return_cost")
GROUPS = [None, "day", "week", "channel", "city", "city_tier", "pincode", "payment_method", "sku", "category"]


def run(name, gb=None, filters=None, repo=None):
    return METRICS[name].callable(ORG, PERIOD, filters, gb, repo=repo or main_repo())


def test_component_totals_hand_calculated():
    # gross: O1 100000 + O2 60000 + O3 (rto -> 0) = 160000 ; discounts 10000 ; taxes 9000 + 5000 = 14000 ; refunds 6000
    # 160000 - 10000 - 14000 - 6000 = 130000 = net_revenue
    assert [run(n).value for n in NET] == [160000, 10000, 14000, 6000]
    # cogs: O1 2x4000 = 8000 ; O2 5000 + 2000 = 7000 -> 15000 ; fees: O1 5000 + 2000, O2 6000 -> 13000
    # shipping: 4000 + 3000 + 3500 (rto order still pays shipping) = 10500 ; returns 1500 + 2500 = 4000
    assert [run(n).value for n in CM] == [15000, 13000, 10500, 4000]
    # 130000 - 15000 - 13000 - 10500 - 4000 = 87500 = contribution_margin
    assert run("contribution_margin").value == 87500


@pytest.mark.parametrize("gb", GROUPS)
def test_waterfall_identities_hold_exactly_for_every_group(gb):
    r = {n: run(n, gb) for n in NET + CM + ("net_revenue", "contribution_margin")}
    keys = {g.key for g in r["net_revenue"].groups}
    for n, res in r.items():
        assert {g.key for g in res.groups} == keys, n
    def gv(n, k):
        return {g.key: g.value for g in r[n].groups}[k]
    for k in keys:
        assert gv("gross_revenue", k) - gv("discounts", k) - gv("taxes", k) - gv("refunds", k) == gv("net_revenue", k)
        assert gv("net_revenue", k) - gv("cogs", k) - gv("fees", k) - gv("shipping_cost", k) - gv("return_cost", k) == gv("contribution_margin", k)
    t = {n: res.value for n, res in r.items()}
    assert t["gross_revenue"] - t["discounts"] - t["taxes"] - t["refunds"] == t["net_revenue"] == 130000
    assert t["net_revenue"] - t["cogs"] - t["fees"] - t["shipping_cost"] - t["return_cost"] == t["contribution_margin"] == 87500
    # group sums equal totals (SKU/category splits are exact thanks to largest-remainder allocation)
    for n, res in r.items():
        if gb:
            assert sum(g.value for g in res.groups) == res.value, (n, gb)


def test_sku_split_components():
    # O2 taxes 5000 split 40000:20000 -> A 3333, B 1667 (B has the larger remainder) ; refunds 6000 -> 4000 / 2000
    r = {g.key: g.value for g in run("taxes", "sku").groups}
    assert r == {"A": 9000 + 3333, "B": 1667 + 0}
    assert {g.key: g.value for g in run("cogs", "sku").groups} == {"A": 8000 + 5000, "B": 2000}


def test_contribution_margin_pct():
    r = run("contribution_margin_pct")
    assert r.value == Decimal("0.6731") and r.unit == "ratio" and r.currency is None  # 87500 / 130000
    g = {x.key: x.value for x in run("contribution_margin_pct", "channel").groups}
    assert g == {"shopify": Decimal("0.6914"), "amazon": Decimal("0.6429")}  # 56000/81000 ; 31500/49000
    by_city = run("contribution_margin_pct", "city")
    assert {x.key: x.value for x in by_city.groups}["Bengaluru"] is None  # net 0, CM -6000
    assert any(i.startswith("groups_with_undefined_value") for i in by_city.data_quality.issues)


def test_contribution_margin_pct_null_when_net_not_positive():
    repo = InMemoryRepository().add(ORG, orders=[order("a", "a", "2026-09-14 06:00:00", status="rto", gross_revenue_paise=100)])
    r = contribution_margin_pct(ORG, PERIOD, repo=repo)
    assert r.value is None and r.data_quality.status == "insufficient" and "net_revenue_not_positive" in r.data_quality.issues
    assert contribution_margin_pct(ORG, PERIOD, repo=InMemoryRepository()).value is None


def test_quality_issues_propagate_to_the_components_that_cause_them():
    repo = InMemoryRepository().add(
        ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000)],
        items=[ItemRow("a", "ZZ", 1, 10000)], sku_cogs=[SkuCogsRow("A", date(2026, 1, 1), 1)],
        fees=[FeeRow("a", "commission", 1500, "estimated"), FeeRow("a", "shipping", 700, "estimated")],
    )
    assert "cogs_missing_for_skus: ['ZZ']" in cogs(ORG, PERIOD, repo=repo).data_quality.issues
    assert "fees_estimated_for_orders: 1" in fees(ORG, PERIOD, repo=repo).data_quality.issues
    assert "fees_estimated_for_orders: 1" in shipping_cost(ORG, PERIOD, repo=repo).data_quality.issues
    assert gross_revenue(ORG, PERIOD, repo=repo).data_quality.status == "ok"
    assert net_revenue(ORG, PERIOD, repo=repo).data_quality.status == "ok"
    cm = contribution_margin(ORG, PERIOD, repo=repo)
    assert cm.data_quality.status == "degraded" and cm.value == 10000 - 1500 - 700


def test_components_support_filters_and_reject_bad_requests():
    assert run("gross_revenue", filters={"channel": ["amazon"]}).value == 60000
    assert run("cogs", filters={"sku": ["B"]}).value == 2000
    with pytest.raises(MetricError):
        run("fees", "colour")
    assert run("gross_revenue", repo=InMemoryRepository()).value is None


def test_waterfall_steps_constant_matches_registry_and_arithmetic():
    names = [s["metric"] for s in WATERFALL_STEPS]
    assert names == ["gross_revenue", "discounts", "taxes", "refunds", "net_revenue", "cogs", "fees", "shipping_cost",
                     "return_cost", "contribution_margin", "ad_spend", "contribution_after_marketing"]
    assert all(n in METRICS for n in names)
    # walking the steps with their signs reproduces each subtotal
    running = 0
    vals = {n: run(n).value for n in names if n not in ("ad_spend", "contribution_after_marketing")}
    for s in WATERFALL_STEPS[:10]:
        if s.get("subtotal"):
            assert running == vals[s["metric"]]
        else:
            running += s["sign"] * vals[s["metric"]]
