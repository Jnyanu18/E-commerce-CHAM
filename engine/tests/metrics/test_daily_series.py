"""group_by=day and the orders / aov / sessions / ad count metrics (hand-calculated; see fx docstrings)."""
from datetime import date
from decimal import Decimal

import pytest

from metrics import (DateRange, InMemoryRepository, MetricError, ad_clicks, ad_impressions, aov, blended_cac,
                     contribution_after_marketing, contribution_margin, expected_rto_loss, mer, net_revenue, orders,
                     platform_reported_conversions, platform_roas, sessions)
from metrics.repository import AdSpendRow
from tests.metrics import test_rto
from tests.metrics.fx import ORG, PERIOD, main_repo, repo_with_spend, traffic_rows


def g(res):
    return {x.key: x.value for x in res.groups}


def spend_repo():
    r = repo_with_spend()
    r.add(ORG, traffic=traffic_rows())
    return r


def test_ledger_metrics_by_day():
    repo = main_repo()
    # O1 14 Sep (81000 net / 62000 CM), O2 17 Sep IST (49000 / 31500), O3 20 Sep rto (0 / -6000)
    assert g(net_revenue(ORG, PERIOD, group_by="day", repo=repo)) == {"2026-09-14": 81000, "2026-09-17": 49000, "2026-09-20": 0}
    assert g(contribution_margin(ORG, PERIOD, group_by="day", repo=repo)) == {"2026-09-14": 62000, "2026-09-17": 31500, "2026-09-20": -6000}


def test_rto_loss_by_day():
    from datetime import date as d
    repo = test_rto.build([
        test_rto.cod("R1", "2026-09-10 06:00:00", "M", "placed", "tier_2", gross=100000, disc=20000),
        test_rto.cod("R2", "2026-09-11 06:00:00", "T", "shipped", "tier_1", gross=60000),
    ])
    r = expected_rto_loss(ORG, DateRange(d(2026, 9, 1), d(2026, 9, 30)), group_by="day", repo=repo, min_history=3)
    assert g(r)["2026-09-10"] == 20000 and g(r)["2026-09-11"] == 40000 and r.value == 60000


def test_orders_counts_realised_orders_only():
    repo = main_repo()
    # O1, O2 realised; O3 is rto (not realised); O5 cancelled; O0/O4 outside the period
    r = orders(ORG, PERIOD, repo=repo)
    assert r.value == 2 and r.unit == "count" and r.currency is None and r.data_quality.row_count == 3
    assert g(orders(ORG, PERIOD, group_by="channel", repo=repo)) == {"shopify": 1, "amazon": 1}
    assert g(orders(ORG, PERIOD, group_by="day", repo=repo)) == {"2026-09-14": 1, "2026-09-17": 1, "2026-09-20": 0}
    assert orders(ORG, PERIOD, {"channel": ["amazon"]}, repo=repo).value == 1
    assert orders(ORG, PERIOD, repo=InMemoryRepository()).value is None
    with pytest.raises(MetricError):
        orders(ORG, PERIOD, group_by="sku", repo=repo)


def test_aov():
    repo = main_repo()
    # net 130000 / 2 orders = 65000 ; shopify 81000/1 ; amazon 49000/1 ; day 20 Sep has 0 realised orders -> null
    r = aov(ORG, PERIOD, repo=repo)
    assert r.value == 65000 and r.unit == "paise"
    assert g(aov(ORG, PERIOD, group_by="channel", repo=repo)) == {"shopify": 81000, "amazon": 49000}
    by_day = aov(ORG, PERIOD, group_by="day", repo=repo)
    assert g(by_day) == {"2026-09-14": 81000, "2026-09-17": 49000, "2026-09-20": None}
    assert any(i.startswith("groups_with_undefined_value") for i in by_day.data_quality.issues)


def test_aov_rounds_half_up_and_null_without_orders():
    from tests.metrics.fx import order
    repo = InMemoryRepository().add(ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=100),
                                                 order("b", "b", "2026-09-14 06:00:00", gross_revenue_paise=101)])
    assert aov(ORG, PERIOD, repo=repo).value == 101  # 201 / 2 = 100.5 -> 101
    only_rto = InMemoryRepository().add(ORG, orders=[order("a", "a", "2026-09-14 06:00:00", status="rto", gross_revenue_paise=100)])
    r = aov(ORG, PERIOD, repo=only_rto)
    assert r.value is None and r.data_quality.status == "insufficient" and "no_realised_orders_in_period" in r.data_quality.issues
    assert orders(ORG, PERIOD, repo=only_rto).value == 0  # a defined zero
    assert aov(ORG, PERIOD, repo=InMemoryRepository()).value is None


def test_mer_cac_cam_by_day():
    repo = spend_repo()
    # daily spend: 14 Sep meta 10000 + google 20000 = 30000 ; 15 Sep 10000 + 10000 = 20000 ; 16..20 Sep meta 10000
    m = g(mer(ORG, PERIOD, group_by="day", repo=repo))
    assert m["2026-09-14"] == Decimal("2.7000")  # 81000 / 30000
    assert m["2026-09-17"] == Decimal("4.9000")  # 49000 / 10000
    assert m["2026-09-20"] == Decimal("0.0000")  # 0 / 10000 (rto order realises nothing)
    assert m["2026-09-15"] == Decimal("0.0000")  # spend but no orders
    cam = g(contribution_after_marketing(ORG, PERIOD, group_by="day", repo=repo))
    assert cam["2026-09-14"] == 32000 and cam["2026-09-17"] == 21500 and cam["2026-09-20"] == -16000  # 62000-30000 ; 31500-10000 ; -6000-10000
    assert cam["2026-09-15"] is None  # no orders that day: undefined, not guessed
    cac = g(blended_cac(ORG, PERIOD, group_by="day", repo=repo))
    # new customers: c1 on 14 Sep, c3 on 20 Sep; c2 is returning (17 Sep -> 0 new)
    assert cac["2026-09-14"] == 30000 and cac["2026-09-20"] == 10000 and cac["2026-09-17"] is None


def test_ad_metrics_by_day_week_platform():
    repo = spend_repo()
    # platform_roas by day: 14 Sep meta 25000/10000 + google 70000/20000 -> 95000/30000 = 3.1667 ; 15 Sep: meta 25000/10000 (google null excluded)
    r = g(platform_roas(ORG, PERIOD, group_by="day", repo=repo))
    assert r["2026-09-14"] == Decimal("3.1667") and r["2026-09-15"] == Decimal("2.5000")


def test_impressions_clicks_conversions():
    repo = spend_repo()
    imp = ad_impressions(ORG, PERIOD, repo=repo)
    assert imp.value == 12000 and imp.unit == "count"  # 7 x 1000 + 3000 + 2000
    assert g(ad_impressions(ORG, PERIOD, group_by="platform", repo=repo)) == {"meta": 7000, "google": 5000}
    assert g(ad_impressions(ORG, PERIOD, group_by="day", repo=repo))["2026-09-14"] == 4000  # 1000 + 3000
    assert ad_clicks(ORG, PERIOD, repo=repo).value == 510  # 7 x 50 + 100 + 60
    assert ad_clicks(ORG, PERIOD, {"platform": ["google"]}, repo=repo).value == 160
    c = platform_reported_conversions(ORG, PERIOD, repo=repo)
    assert c.value == Decimal("18.5")  # 7 x 2 + 4.5 ; google 15 Sep null excluded
    assert "platform-reported, not causal" in c.definition
    assert "platform_conversions_missing_for_rows: 1 of 9 (excluded)" in c.data_quality.issues
    assert c.data_quality.status == "degraded"
    assert g(platform_reported_conversions(ORG, PERIOD, group_by="platform", repo=repo)) == {"meta": Decimal(14), "google": Decimal("4.5")}
    assert c.to_dict()["value"] == 18.5


def test_ad_count_metrics_empty_and_null():
    assert ad_clicks(ORG, PERIOD, repo=InMemoryRepository()).value is None
    repo = InMemoryRepository().add(ORG, ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 100)])
    r = platform_reported_conversions(ORG, PERIOD, repo=repo)
    assert r.value is None and r.data_quality.status == "insufficient"
    assert ad_clicks(ORG, PERIOD, repo=repo).value == 0  # clicks default 0 = reported zero


def test_sessions():
    repo = spend_repo()
    r = sessions(ORG, PERIOD, repo=repo)
    assert r.value == 2700 and r.unit == "count"
    assert "traffic_missing_days: 5 of 7" in r.data_quality.issues
    assert g(sessions(ORG, PERIOD, group_by="channel", repo=repo)) == {"shopify": 2200, "amazon": 500}
    assert g(sessions(ORG, PERIOD, group_by="day", repo=repo)) == {"2026-09-14": 1500, "2026-09-15": 1200}
    assert g(sessions(ORG, PERIOD, group_by="week", repo=repo)) == {"2026-09-14": 2700}
    assert sessions(ORG, PERIOD, {"channel": ["amazon"]}, repo=repo).value == 500


def test_sessions_without_traffic_data_is_insufficient():
    r = sessions(ORG, PERIOD, repo=main_repo())
    assert r.value is None and r.data_quality.status == "insufficient"
    assert any(i.startswith("no_traffic_data_in_period") for i in r.data_quality.issues)
    with pytest.raises(MetricError):
        sessions(ORG, PERIOD, group_by="platform", repo=main_repo())
