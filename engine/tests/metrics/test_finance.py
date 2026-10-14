"""net_revenue and contribution_margin against hand-calculated fixtures (see fx.main_repo docstring)."""
from datetime import date

import pytest

from metrics import DateRange, InMemoryRepository, MetricError, contribution_margin, net_revenue
from metrics.repository import FeeRow, ItemRow, SkuCogsRow
from tests.metrics.fx import ORG, OTHER_ORG, PERIOD, main_repo, order


def groups(res):
    return {g.key: g.value for g in res.groups}


def test_net_revenue_total():
    # O1: 100000 - 10000 - 9000 - 0      = 81000
    # O2:  60000 -     0 -  5000 - 6000  = 49000
    # O3: status rto -> sale not realised = 0
    # O0 (Aug) outside period, O4 (22 Sep) outside period, O5 cancelled -> excluded
    r = net_revenue(ORG, PERIOD, repo=main_repo())
    assert r.value == 130000
    assert r.unit == "paise" and r.currency == "INR"
    assert r.data_quality.row_count == 3
    assert r.data_quality.status == "ok"
    assert r.group_by is None and r.groups == []


def test_net_revenue_by_channel_payment_city_pincode_week():
    repo = main_repo()
    assert groups(net_revenue(ORG, PERIOD, group_by="channel", repo=repo)) == {"shopify": 81000, "amazon": 49000}
    assert groups(net_revenue(ORG, PERIOD, group_by="payment_method", repo=repo)) == {"prepaid": 130000, "cod": 0}
    assert groups(net_revenue(ORG, PERIOD, group_by="city", repo=repo)) == {"Mumbai": 81000, "Delhi": 49000, "Bengaluru": 0}
    assert groups(net_revenue(ORG, PERIOD, group_by="pincode", repo=repo)) == {"400001": 81000, "110001": 49000, "560001": 0}
    assert groups(net_revenue(ORG, PERIOD, group_by="city_tier", repo=repo)) == {"metro": 130000}
    assert groups(net_revenue(ORG, PERIOD, group_by="week", repo=repo)) == {"2026-09-14": 130000}


def test_net_revenue_by_sku_and_category_allocates_exactly():
    # O1 all A: 81000.
    # O2 weights A 40000 : B 20000. gross 40000/20000; taxes 5000 -> exact 3333.33/1666.67, largest remainder gives
    # B the extra paisa: A 3333, B 1667; refunds 6000 -> 4000/2000.
    #   A = 40000 - 3333 - 4000 = 32667 ; B = 20000 - 1667 - 2000 = 16333 ; (sum 49000)
    # A total = 81000 + 32667 = 113667 ; B = 16333 (O3 rto = 0) ; sum = 130000
    repo = main_repo()
    expected = {"A": 113667, "B": 16333}
    assert groups(net_revenue(ORG, PERIOD, group_by="sku", repo=repo)) == expected
    assert groups(net_revenue(ORG, PERIOD, group_by="category", repo=repo)) == {"tee": 113667, "mug": 16333}
    assert sum(expected.values()) == 130000


def test_net_revenue_filters():
    repo = main_repo()
    assert net_revenue(ORG, PERIOD, {"channel": ["amazon"]}, repo=repo).value == 49000
    assert net_revenue(ORG, PERIOD, {"channel": "shopify", "payment_method": "prepaid"}, repo=repo).value == 81000
    assert net_revenue(ORG, PERIOD, {"city": ["Delhi", "Mumbai"]}, repo=repo).value == 130000
    assert net_revenue(ORG, PERIOD, {"sku": ["B"]}, repo=repo).value == 16333
    assert net_revenue(ORG, PERIOD, {"category": ["tee"]}, repo=repo).value == 113667
    res = net_revenue(ORG, PERIOD, {"channel": ["amazon"]}, repo=repo)
    assert res.filters == {"channel": ["amazon"]}


def test_week_boundaries_across_ist_midnight():
    # O3: 2026-09-20 18:00 UTC = 23:30 IST on Sun 20 Sep -> week of Mon 14 Sep.
    # O4: 2026-09-21 18:30 UTC = 00:00 IST on Mon 22 Sep -> ordered_date_ist 22 Sep, week of Mon 21 Sep.
    # Range 14..27 Sep: week 14 Sep = 130000 (above); week 21 Sep = O4 only: 7000 gross, 0 else = 7000
    res = net_revenue(ORG, DateRange(date(2026, 9, 14), date(2026, 9, 27)), group_by="week", repo=main_repo())
    assert groups(res) == {"2026-09-14": 130000, "2026-09-21": 7000}
    assert res.value == 137000


def test_range_end_is_inclusive_and_ist_based():
    repo = main_repo()
    # O3 ordered 23:30 IST on the 20th must be in a range ending on the 20th and absent from one ending on the 19th.
    assert net_revenue(ORG, DateRange(date(2026, 9, 20), date(2026, 9, 20)), repo=repo).data_quality.row_count == 1
    assert net_revenue(ORG, DateRange(date(2026, 9, 14), date(2026, 9, 19)), repo=repo).data_quality.row_count == 2
    # O2 (01:30 IST on the 17th, 20:00 UTC on the 16th) belongs to the 17th, not the 16th.
    assert net_revenue(ORG, DateRange(date(2026, 9, 16), date(2026, 9, 16)), repo=repo).value is None
    assert net_revenue(ORG, DateRange(date(2026, 9, 17), date(2026, 9, 17)), repo=repo).value == 49000


def test_contribution_margin_total():
    # O1: net 81000 - COGS 2 x 4000 (A effective 2026-01-01 as of 14 Sep) = 8000
    #     - fees (commission 5000 api; estimated 9999 ignored; payment_gateway 2000) = 7000 - shipping 4000 - returns 0
    #     = 81000 - 8000 = 73000 ; - 7000 = 66000 ; - 4000 = 62000
    # O2: net 49000 - COGS (A 5000 effective 15 Sep, B 2000) 7000 - fees 6000 - shipping 3000 - return cost 1500
    #     = 49000 - 7000 = 42000; -6000 = 36000; -3000 = 33000; -1500 = 31500
    # O3: rto: net 0, COGS 0, fees 0, shipping 3500, RTO cost 2500 -> -6000
    # total = 62000 + 31500 - 6000 = 87500
    r = contribution_margin(ORG, PERIOD, repo=main_repo())
    assert r.value == 87500
    # O1 also has an estimated commission row (9999) next to the api one: the actual wins and nothing estimated is used.
    assert r.data_quality.status == "ok"



def test_contribution_margin_groups():
    repo = main_repo()
    assert groups(contribution_margin(ORG, PERIOD, group_by="channel", repo=repo)) == {
        "shopify": 62000 - 6000,  # O1 + O3
        "amazon": 31500,
    }
    assert groups(contribution_margin(ORG, PERIOD, group_by="payment_method", repo=repo)) == {"prepaid": 93500, "cod": -6000}
    assert groups(contribution_margin(ORG, PERIOD, group_by="city", repo=repo)) == {"Mumbai": 62000, "Delhi": 31500, "Bengaluru": -6000}


def test_contribution_margin_by_sku():
    # O1 (A): 62000.
    # O2 split 40000:20000 (net A 32667, B 16333 as in net revenue test); COGS direct A 5000, B 2000;
    #   commission 6000 -> 4000/2000; shipping 3000 -> 2000/1000; return 1500 -> 1000/500
    #   A = 32667 - 5000 - 4000 - 2000 - 1000 = 20667 ; B = 16333 - 2000 - 2000 - 1000 - 500 = 10833 (sum 31500)
    # O3 (B only item): -6000
    # A = 62000 + 20667 = 82667 ; B = 10833 - 6000 = 4833 ; sum 87500
    res = contribution_margin(ORG, PERIOD, group_by="sku", repo=main_repo())
    assert groups(res) == {"A": 82667, "B": 4833}
    assert sum(groups(res).values()) == res.value == 87500


def test_cogs_effective_date_uses_latest_effective_from_not_after_order_date():
    repo = InMemoryRepository().add(
        ORG,
        orders=[
            order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000),   # 14 Sep -> COGS 4000
            order("b", "b", "2026-09-15 06:00:00", gross_revenue_paise=10000),   # 15 Sep: same-day effective -> 5000
        ],
        items=[ItemRow("a", "A", 1, 10000), ItemRow("b", "A", 1, 10000)],
        sku_cogs=[SkuCogsRow("A", date(2026, 1, 1), 4000), SkuCogsRow("A", date(2026, 9, 15), 5000)],
    )
    # net 10000 each; CM = (10000 - 4000) + (10000 - 5000) = 11000
    r = contribution_margin(ORG, PERIOD, repo=repo)
    assert r.value == 11000 and r.data_quality.status == "ok"


def test_missing_cogs_is_flagged_not_silent():
    repo = InMemoryRepository().add(
        ORG,
        orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000), order("b", "b", "2026-09-14 07:00:00", gross_revenue_paise=5000)],
        items=[ItemRow("a", "A", 1, 10000), ItemRow("b", "ZZ", 1, 5000)],
        sku_cogs=[SkuCogsRow("A", date(2026, 1, 1), 4000)],
    )
    r = contribution_margin(ORG, PERIOD, repo=repo)
    assert r.value == 11000  # (10000-4000) + 5000 with ZZ cogs unknown (counted as 0, flagged)
    assert r.data_quality.status == "degraded"
    assert "cogs_missing_for_skus: ['ZZ']" in r.data_quality.issues
    # COGS effective only after the order date is also "missing"
    repo2 = InMemoryRepository().add(
        ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000)], items=[ItemRow("a", "A", 1, 10000)],
        sku_cogs=[SkuCogsRow("A", date(2026, 10, 1), 4000)],
    )
    assert "cogs_missing_for_skus: ['A']" in contribution_margin(ORG, PERIOD, repo=repo2).data_quality.issues


def test_order_without_items_flags_cogs_unknown():
    repo = InMemoryRepository().add(ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000)])
    r = contribution_margin(ORG, PERIOD, repo=repo)
    assert r.value == 10000
    assert "orders_without_items_cogs_unknown: 1" in r.data_quality.issues


def test_only_estimated_fee_is_used_and_flagged():
    repo = InMemoryRepository().add(
        ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000)], items=[ItemRow("a", "A", 1, 10000)],
        sku_cogs=[SkuCogsRow("A", date(2026, 1, 1), 1000)], fees=[FeeRow("a", "commission", 1500, "estimated")],
    )
    r = contribution_margin(ORG, PERIOD, repo=repo)
    assert r.value == 10000 - 1000 - 1500
    assert "fees_estimated_for_orders: 1" in r.data_quality.issues


def test_empty_data_returns_null_insufficient():
    r = net_revenue(ORG, PERIOD, repo=InMemoryRepository())
    assert r.value is None and r.data_quality.status == "insufficient" and "no_orders_in_period" in r.data_quality.issues
    r2 = contribution_margin(ORG, PERIOD, group_by="channel", repo=InMemoryRepository())
    assert r2.value is None and r2.groups == []
    # filter that matches nothing
    r3 = net_revenue(ORG, PERIOD, {"channel": ["flipkart"]}, repo=main_repo())
    assert r3.value is None and r3.data_quality.row_count == 0


def test_tenant_isolation():
    repo = main_repo()
    assert net_revenue(OTHER_ORG, PERIOD, repo=repo).value is None


def test_duplicate_rows_are_ignored():
    repo = main_repo()
    dup = order("O1dup", "e1", "2026-09-14 10:00:00", customer_key="c1", gross_revenue_paise=100000, discounts_paise=10000, taxes_paise=9000)
    same_id = order("O2", "zz", "2026-09-16 20:00:00", channel="amazon", gross_revenue_paise=999999)
    repo.add(ORG, orders=[dup, same_id], items=[ItemRow("O1", "A", 2, 50000)], fees=[FeeRow("O1", "shipping", 4000)])
    r = net_revenue(ORG, PERIOD, repo=repo)
    assert r.value == 130000  # duplicates (same channel+external_id, same id) not double counted
    assert "duplicate_orders_ignored: 2" in r.data_quality.issues
    cm = contribution_margin(ORG, PERIOD, repo=repo)
    assert cm.value == 87500
    assert "duplicate_order_items_ignored: 1" in cm.data_quality.issues
    assert "duplicate_fees_ignored: 1" in cm.data_quality.issues


def test_category_missing_from_catalogue_is_flagged():
    repo = InMemoryRepository().add(
        ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000)], items=[ItemRow("a", "NEW", 1, 10000)]
    )
    r = net_revenue(ORG, PERIOD, group_by="category", repo=repo)
    assert groups(r) == {"unknown": 10000}
    assert "category_missing_for_skus: ['NEW']" in r.data_quality.issues
    assert r.data_quality.status == "degraded"


def test_invalid_requests_raise():
    repo = main_repo()
    with pytest.raises(MetricError):
        net_revenue(ORG, PERIOD, group_by="color", repo=repo)
    with pytest.raises(MetricError):
        net_revenue(ORG, PERIOD, {"colour": ["red"]}, repo=repo)
    with pytest.raises(MetricError):
        DateRange(date(2026, 9, 20), date(2026, 9, 14))


def test_to_dict_matches_contract_shape():
    d = contribution_margin(ORG, PERIOD, {"channel": ["shopify"]}, "channel", repo=main_repo()).to_dict()
    assert set(d) == {"metric", "value", "unit", "currency", "period", "filters", "group_by", "groups", "definition", "data_quality"}
    assert d["period"] == {"start": "2026-09-14", "end": "2026-09-20"}
    assert d["filters"] == {"channel": ["shopify"]} and d["group_by"] == "channel"
    assert set(d["data_quality"]) == {"status", "issues", "row_count"}
    assert d["groups"] == [{"key": "shopify", "value": 56000}]
    assert d["definition"].startswith("contribution_margin = net_revenue - COGS - fees - shipping cost - RTO/return cost")


def test_ledger_missing_sku_placeholder_is_missing_cogs():
    # Even if someone created a sku_cogs row for the placeholder, it is never used: COGS unknown, flagged.
    repo = InMemoryRepository().add(
        ORG, orders=[order("a", "a", "2026-09-14 06:00:00", gross_revenue_paise=10000)],
        items=[ItemRow("a", "__MISSING_SKU__", 1, 10000)], sku_cogs=[SkuCogsRow("__MISSING_SKU__", date(2026, 1, 1), 1)],
    )
    r = contribution_margin(ORG, PERIOD, repo=repo)
    assert r.value == 10000 and r.data_quality.status == "degraded"
    assert "cogs_missing_for_skus: ['__MISSING_SKU__']" in r.data_quality.issues
