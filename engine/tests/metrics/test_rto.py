"""expected_rto_loss with hand-calculated segment rates."""
from datetime import date

from metrics import DateRange, InMemoryRepository, expected_rto_loss
from metrics.repository import ItemRow, SkuRow
from tests.metrics.fx import ORG, order

SEPT = DateRange(date(2026, 9, 1), date(2026, 9, 30))


def cod(oid, when, sku, status, tier, gross=1000, disc=0, city="X", channel="shopify"):
    return order(oid, oid, when, channel=channel, payment_method="cod", status=status, city_tier=tier, city=city,
                 gross_revenue_paise=gross, discounts_paise=disc), ItemRow(oid, sku, 1, gross)


def build(extra_orders=()):
    rows = [
        # History (all within 180 days before 30 Sep 2026, i.e. on/after 2026-04-03)
        cod("H1", "2026-08-01 06:00:00", "M", "delivered", "tier_2"),
        cod("H2", "2026-08-02 06:00:00", "M", "delivered", "tier_2"),
        cod("H3", "2026-08-03 06:00:00", "M", "delivered", "tier_2"),
        cod("H4", "2026-08-04 06:00:00", "M", "rto", "tier_2"),
        cod("H5", "2026-08-05 06:00:00", "T", "delivered", "tier_1"),
        cod("H6", "2026-08-06 06:00:00", "M", "rto", "tier_1"),
        cod("H7", "2026-08-07 06:00:00", "M", "rto", "tier_1"),
        cod("H8", "2026-01-01 06:00:00", "M", "rto", "tier_2"),  # older than the 180-day window: ignored
        # still undelivered history rows must not count as resolved
        cod("H9", "2026-08-08 06:00:00", "M", "shipped", "tier_2"),
        *extra_orders,
    ]
    repo = InMemoryRepository().add(
        ORG, skus=[SkuRow("M", "mug"), SkuRow("T", "tee")], orders=[o for o, _ in rows], items=[i for _, i in rows]
    )
    return repo


def test_full_segment_rate():
    # Segment mug x tier_2 x cod in window: H1,H2,H3 delivered + H4 rto = 4 resolved, 1 rto -> 25% (H8 too old, H9 unresolved)
    # R1 value = 100000 - 20000 = 80000 -> expected loss 80000 x 1/4 = 20000
    repo = build([cod("R1", "2026-09-10 06:00:00", "M", "placed", "tier_2", gross=100000, disc=20000)])
    r = expected_rto_loss(ORG, SEPT, repo=repo, min_history=3)
    assert r.value == 20000
    assert r.unit == "paise" and r.data_quality.status == "ok" and r.data_quality.row_count == 1


def test_fallback_to_coarser_segment_is_reported():
    # R2: tee x tier_1 x cod has 1 resolved (< 3) -> fall back to tier_1 x cod: H5 delivered, H6,H7 rto -> 2/3
    # R2 value 60000 -> 60000 x 2/3 = 40000 ; R1 = 20000 (full segment) ; total 60000
    repo = build([
        cod("R1", "2026-09-10 06:00:00", "M", "placed", "tier_2", gross=100000, disc=20000),
        cod("R2", "2026-09-11 06:00:00", "T", "shipped", "tier_1", gross=60000),
    ])
    r = expected_rto_loss(ORG, SEPT, repo=repo, min_history=3)
    assert r.value == 60000
    assert r.data_quality.status == "degraded"
    assert any("city_tier+payment_method=1 orders" in i for i in r.data_quality.issues)


def test_fallback_to_payment_method_level():
    # R3 in tier_3 (no history at all): fall back to cod overall: resolved = H1..H7 = 7, rto = H4,H6,H7 = 3 -> 3/7
    # value 7000 x 3/7 = 3000
    repo = build([cod("R3", "2026-09-12 06:00:00", "M", "placed", "tier_3", gross=7000)])
    r = expected_rto_loss(ORG, SEPT, repo=repo, min_history=3)
    assert r.value == 3000
    assert any("payment_method=1 orders" in i for i in r.data_quality.issues)


def test_rounding_half_up():
    # tier_1 cod rate 2/3 ; value 1000 -> 666.67 -> 667
    repo = build([cod("R4", "2026-09-12 06:00:00", "T", "placed", "tier_1", gross=1000)])
    assert expected_rto_loss(ORG, SEPT, repo=repo, min_history=3).value == 667


def test_insufficient_history_returns_null_never_a_number():
    repo = build([cod("R1", "2026-09-10 06:00:00", "M", "placed", "tier_2", gross=100000)])
    r = expected_rto_loss(ORG, SEPT, repo=repo, min_history=10)  # only 7 resolved COD orders exist in total
    assert r.value is None
    assert r.data_quality.status == "insufficient"
    assert any(i.startswith("rto_history_insufficient: 1 at-risk") for i in r.data_quality.issues)


def test_no_history_at_all():
    repo = InMemoryRepository().add(ORG, skus=[SkuRow("M", "mug")], orders=[cod("R1", "2026-09-10 06:00:00", "M", "placed", "tier_2")[0]],
                                    items=[ItemRow("R1", "M", 1, 1000)])
    assert expected_rto_loss(ORG, SEPT, repo=repo).value is None


def test_no_undelivered_cod_is_a_true_zero():
    # Only delivered COD and prepaid placed orders in the period: nothing at risk -> 0 (defined), not null.
    prepaid = order("P1", "P1", "2026-09-10 06:00:00", status="placed", gross_revenue_paise=5000)
    repo = build([cod("D1", "2026-09-09 06:00:00", "M", "delivered", "tier_2")])
    repo.add(ORG, orders=[prepaid])
    r = expected_rto_loss(ORG, SEPT, repo=repo, min_history=3)
    assert r.value == 0 and r.data_quality.status == "ok" and r.data_quality.row_count == 0


def test_empty_period_is_null():
    r = expected_rto_loss(ORG, SEPT, repo=InMemoryRepository())
    assert r.value is None and "no_orders_in_period" in r.data_quality.issues


def test_group_by_city_and_filter_do_not_change_rates():
    repo = build([
        cod("R1", "2026-09-10 06:00:00", "M", "placed", "tier_2", gross=100000, disc=20000, city="Jaipur"),
        cod("R2", "2026-09-11 06:00:00", "T", "shipped", "tier_1", gross=60000, city="Delhi", channel="amazon"),
    ])
    r = expected_rto_loss(ORG, SEPT, group_by="city", repo=repo, min_history=3)
    got = {g.key: g.value for g in r.groups}
    assert got["Jaipur"] == 20000 and got["Delhi"] == 40000 and r.value == 60000
    # filtering to amazon only: rate still comes from unfiltered history
    r2 = expected_rto_loss(ORG, SEPT, {"channel": ["amazon"]}, repo=repo, min_history=3)
    assert r2.value == 40000


def test_group_by_sku_and_category_allocate_loss():
    # Two-item order: M 30000 + T 10000 (value 40000, tier_2 cod). Primary category = mug (highest value).
    # Segment mug x tier_2 x cod = 25% -> loss 10000, split 3:1 -> M 7500, T 2500
    o = order("R5", "R5", "2026-09-10 06:00:00", payment_method="cod", status="placed", city_tier="tier_2", gross_revenue_paise=40000)
    repo = build()
    repo.add(ORG, orders=[o], items=[ItemRow("R5", "M", 1, 30000), ItemRow("R5", "T", 1, 10000)])
    r = expected_rto_loss(ORG, SEPT, group_by="sku", repo=repo, min_history=3)
    assert {g.key: g.value for g in r.groups} == {"M": 7500, "T": 2500} and r.value == 10000
    r2 = expected_rto_loss(ORG, SEPT, group_by="category", repo=repo, min_history=3)
    assert {g.key: g.value for g in r2.groups} == {"mug": 7500, "tee": 2500}
