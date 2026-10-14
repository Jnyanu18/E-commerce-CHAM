"""blended_cac, mer, platform_roas, contribution_after_marketing (fixtures in fx.py)."""
from datetime import date
from decimal import Decimal

import pytest

from metrics import (DateRange, InMemoryRepository, MetricError, blended_cac, contribution_after_marketing, mer, platform_roas)
from metrics.repository import AdSpendRow
from tests.metrics.fx import ORG, PERIOD, main_repo, order, repo_with_spend


def groups(res):
    return {g.key: g.value for g in res.groups}


def test_mer():
    # net revenue 130000 / ad spend (meta 7 x 10000 = 70000 + google 20000 + 10000 = 100000) = 1.3000
    r = mer(ORG, PERIOD, repo=repo_with_spend())
    assert r.value == Decimal("1.3000") and r.unit == "ratio" and r.currency is None
    assert r.data_quality.status == "ok"  # all 7 days have spend rows


def test_mer_by_week():
    r = mer(ORG, PERIOD, group_by="week", repo=repo_with_spend())
    assert groups(r) == {"2026-09-14": Decimal("1.3000")}


def test_mer_zero_spend_is_null_not_inf():
    repo = main_repo()
    repo.add(ORG, ad_spend=[AdSpendRow("meta", "m1", date(2026, 9, 14), 0, 0)])
    r = mer(ORG, PERIOD, repo=repo)
    assert r.value is None and r.data_quality.status == "insufficient"


def test_mer_no_spend_rows_is_flagged():
    r = mer(ORG, PERIOD, repo=main_repo())
    assert r.value is None and "ad_spend_missing_no_rows_in_period" in r.data_quality.issues


def test_mer_with_order_filter_flags_unfiltered_spend():
    r = mer(ORG, PERIOD, {"channel": ["amazon"]}, repo=repo_with_spend())
    assert r.value == Decimal("0.4900")  # 49000 / 100000
    assert any(i.startswith("ad_spend_not_filtered_by_order_filters") for i in r.data_quality.issues)
    assert r.data_quality.status == "degraded"


def test_mer_platform_filter_only_filters_spend():
    # meta spend 70000 only ; net revenue unfiltered 130000 -> 1.857142... -> 1.8571
    r = mer(ORG, PERIOD, {"platform": ["meta"]}, repo=repo_with_spend())
    assert r.value == Decimal("1.8571")


def test_mer_empty_orders():
    repo = InMemoryRepository().add(ORG, ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 100)])
    assert mer(ORG, PERIOD, repo=repo).value is None


def test_platform_roas():
    # Rows reporting revenue: meta 7 x (10000 spend / 25000 rev), google 14 Sep (20000 / 70000).
    #   revenue = 175000 + 70000 = 245000 ; spend of those rows = 70000 + 20000 = 90000 -> 2.7222
    # google 15 Sep (10000 spend, revenue NULL) excluded from both sides and flagged.
    r = platform_roas(ORG, PERIOD, repo=repo_with_spend())
    assert r.value == Decimal("2.7222")
    assert "platform-reported, not causal" in r.definition
    assert "platform_revenue_missing_for_spend_paise: 10000 (excluded from ratio)" in r.data_quality.issues
    assert r.data_quality.status == "degraded"
    assert r.data_quality.row_count == 9


def test_platform_roas_by_platform_and_filter():
    repo = repo_with_spend()
    assert groups(platform_roas(ORG, PERIOD, group_by="platform", repo=repo)) == {"meta": Decimal("2.5000"), "google": Decimal("3.5000")}
    assert platform_roas(ORG, PERIOD, {"platform": ["meta"]}, repo=repo).value == Decimal("2.5000")
    assert groups(platform_roas(ORG, PERIOD, group_by="week", repo=repo)) == {"2026-09-14": Decimal("2.7222")}


def test_platform_roas_no_spend_or_zero_spend_is_null():
    assert platform_roas(ORG, PERIOD, repo=InMemoryRepository()).value is None
    repo = InMemoryRepository().add(ORG, ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 0, 500)])
    r = platform_roas(ORG, PERIOD, repo=repo)
    assert r.value is None and r.data_quality.status == "insufficient"


def test_blended_cac():
    # New customers in 14..20 Sep: c1 (first order O1) and c3 (first order O3, rto still an acquired customer).
    # c2 first ordered 1 Aug (O0) -> returning. c5 cancelled -> not a customer. c4 first order 22 Sep -> outside.
    # CAC = 100000 / 2 = 50000
    r = blended_cac(ORG, PERIOD, repo=repo_with_spend())
    assert r.value == 50000 and r.unit == "paise" and r.currency == "INR"
    assert "first non-cancelled order" in r.definition
    assert r.data_quality.status == "ok"


def test_blended_cac_rounds_half_up():
    # spend 100000 / 3 new customers = 33333.33 -> 33333 ; 100001 / 2 = 50000.5 -> 50001
    repo = InMemoryRepository().add(
        ORG,
        orders=[order(f"n{i}", f"n{i}", "2026-09-14 06:00:00", customer_key=f"k{i}", gross_revenue_paise=100) for i in range(3)],
        ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 100000)],
    )
    assert blended_cac(ORG, DateRange(date(2026, 9, 14), date(2026, 9, 14)), repo=repo).value == 33333
    repo2 = InMemoryRepository().add(
        ORG, orders=[order(f"n{i}", f"n{i}", "2026-09-14 06:00:00", customer_key=f"k{i}", gross_revenue_paise=100) for i in range(2)],
        ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 100001)],
    )
    assert blended_cac(ORG, DateRange(date(2026, 9, 14), date(2026, 9, 14)), repo=repo2).value == 50001


def test_blended_cac_first_order_is_across_channels_and_uses_earliest_timestamp():
    # k1 buys on amazon 14 Sep 06:00 UTC then shopify 14 Sep 09:00 UTC: first order = amazon.
    repo = InMemoryRepository().add(
        ORG,
        orders=[order("a", "a", "2026-09-14 09:00:00", customer_key="k1", gross_revenue_paise=1),
                order("b", "b", "2026-09-14 06:00:00", channel="amazon", customer_key="k1", gross_revenue_paise=1)],
        ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 900)],
    )
    d = DateRange(date(2026, 9, 14), date(2026, 9, 14))
    assert blended_cac(ORG, d, repo=repo).value == 900  # one new customer
    assert blended_cac(ORG, d, {"channel": ["amazon"]}, repo=repo).value == 900  # first order was amazon
    r = blended_cac(ORG, d, {"channel": ["shopify"]}, repo=repo)
    assert r.value is None and "no_new_customers_in_period" in r.data_quality.issues


def test_blended_cac_no_new_customers_and_no_spend_are_null():
    only_returning = DateRange(date(2026, 9, 16), date(2026, 9, 16))  # nothing happens
    r = blended_cac(ORG, only_returning, repo=repo_with_spend())
    assert r.value is None and r.data_quality.status == "insufficient"
    r2 = blended_cac(ORG, PERIOD, repo=main_repo())  # new customers but no spend rows
    assert r2.value is None and "ad_spend_missing_no_rows_in_period" in r2.data_quality.issues


def test_blended_cac_orders_without_customer_key_flagged():
    repo = InMemoryRepository().add(
        ORG,
        orders=[order("a", "a", "2026-09-14 06:00:00", customer_key="k1", gross_revenue_paise=1),
                order("b", "b", "2026-09-14 06:00:00", gross_revenue_paise=1)],
        ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 500)],
    )
    r = blended_cac(ORG, DateRange(date(2026, 9, 14), date(2026, 9, 14)), repo=repo)
    assert r.value == 500
    assert "orders_without_customer_key_not_counted: 1" in r.data_quality.issues


def test_blended_cac_by_week():
    # c4 first orders on 22 Sep (week of 21 Sep); spend in that week 5000.
    repo = repo_with_spend()
    repo.add(ORG, ad_spend=[AdSpendRow("meta", "m1", date(2026, 9, 22), 5000)])
    r = blended_cac(ORG, DateRange(date(2026, 9, 14), date(2026, 9, 27)), group_by="week", repo=repo)
    # week 14 Sep: 100000/2 = 50000 ; week 21 Sep: 5000/1 = 5000 ; total 105000/3 = 35000
    assert groups(r) == {"2026-09-14": 50000, "2026-09-21": 5000}
    assert r.value == 35000


def test_contribution_after_marketing():
    # contribution_margin 87500 (see test_finance) - ad spend 100000 = -12500
    r = contribution_after_marketing(ORG, PERIOD, repo=repo_with_spend())
    assert r.value == -12500
    assert r.data_quality.status == "ok"
    assert groups(contribution_after_marketing(ORG, PERIOD, group_by="week", repo=repo_with_spend())) == {"2026-09-14": -12500}


def test_contribution_after_marketing_requires_spend():
    r = contribution_after_marketing(ORG, PERIOD, repo=main_repo())
    assert r.value is None and r.data_quality.status == "insufficient"
    assert "ad_spend_missing_no_rows_in_period" in r.data_quality.issues


def test_missing_spend_days_flagged():
    repo = main_repo()
    repo.add(ORG, ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 1000)])
    r = contribution_after_marketing(ORG, PERIOD, repo=repo)
    assert r.value == 86500 and "ad_spend_missing_days: 6 of 7" in r.data_quality.issues


def test_duplicate_spend_rows_ignored():
    repo = main_repo()
    row = AdSpendRow("meta", "m", date(2026, 9, 14), 1000)
    repo.add(ORG, ad_spend=[row, row])
    r = contribution_after_marketing(ORG, PERIOD, repo=repo)
    assert r.value == 86500 and "duplicate_ad_spend_rows_ignored: 1" in r.data_quality.issues


def test_unsupported_group_by_for_spend_metrics():
    repo = repo_with_spend()
    for fn in (mer, blended_cac, contribution_after_marketing):
        with pytest.raises(MetricError):
            fn(ORG, PERIOD, group_by="channel", repo=repo)
    with pytest.raises(MetricError):
        platform_roas(ORG, PERIOD, {"channel": ["shopify"]}, repo=repo)
