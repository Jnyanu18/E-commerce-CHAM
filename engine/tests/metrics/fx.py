"""Hand-built ledger fixtures. Nothing here comes from engine/seed; all expected values are computed by hand in the tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from metrics import DateRange, InMemoryRepository
from metrics.repository import AdSpendRow, FeeRow, ItemRow, OrderRow, ReturnRow, SkuCogsRow, SkuRow, TrafficRow

ORG = "org-1"
OTHER_ORG = "org-2"
PERIOD = DateRange(date(2026, 9, 14), date(2026, 9, 20))  # Mon 14 Sep .. Sun 20 Sep 2026 (one IST week)
IST = timedelta(hours=5, minutes=30)


def utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def order(oid, ext, when_utc, **kw) -> OrderRow:
    """ordered_date_ist is derived like the DB generated column: (ordered_at at time zone 'Asia/Kolkata')::date."""
    at = utc(when_utc)
    base = dict(
        id=oid, channel="shopify", external_id=ext, ordered_at=at, ordered_date_ist=(at + IST).date(),
        payment_method="prepaid", status="delivered", gross_revenue_paise=0,
    )
    base.update(kw)
    return OrderRow(**base)


def main_repo() -> InMemoryRepository:
    """
    Catalogue: A (category tee), B (category mug).
    sku_cogs: A 4000 from 2026-01-01, 5000 from 2026-09-15; B 2000 from 2026-01-01.

    O0 amazon  2026-08-01 (before period)  customer c2, prepaid delivered, gross 10000 (makes c2 a returning customer)
    O1 shopify 2026-09-14 15:30 IST  prepaid delivered Mumbai/metro/400001 c1
         gross 100000, discounts 10000, taxes 9000, refunds 0; items A x2 @ 50000
         fees api: commission 5000, shipping 4000, payment_gateway 2000; estimated commission 9999 (api wins, ignored)
    O2 amazon  2026-09-17 01:30 IST (= 2026-09-16 20:00 UTC)  prepaid delivered Delhi/metro/110001 c2
         gross 60000, taxes 5000, refunds 6000; items A x1 @ 40000, B x1 @ 20000
         fees api: commission 6000, shipping 3000; returns: customer_return cost 1500
    O3 shopify 2026-09-20 23:30 IST (= 18:00 UTC)  COD rto Bengaluru/metro/560001 c3
         gross 30000, taxes 2000; items B x1 @ 30000; fees api shipping 3500; returns RTO cost 2500
    O4 shopify 2026-09-22 00:00:00 IST (= 2026-09-21 18:30 UTC) - next week, outside PERIOD
    O5 shopify 2026-09-15 cancelled gross 99999 - excluded everywhere
    """
    r = InMemoryRepository()
    r.add(
        ORG,
        skus=[SkuRow("A", "tee"), SkuRow("B", "mug")],
        sku_cogs=[SkuCogsRow("A", date(2026, 1, 1), 4000), SkuCogsRow("A", date(2026, 9, 15), 5000), SkuCogsRow("B", date(2026, 1, 1), 2000)],
        orders=[
            order("O0", "e0", "2026-08-01 06:00:00", channel="amazon", customer_key="c2", gross_revenue_paise=10000, city="Delhi", city_tier="metro", pincode="110001"),
            order("O1", "e1", "2026-09-14 10:00:00", customer_key="c1", city="Mumbai", city_tier="metro", pincode="400001",
                  gross_revenue_paise=100000, discounts_paise=10000, taxes_paise=9000),
            order("O2", "e2", "2026-09-16 20:00:00", channel="amazon", customer_key="c2", city="Delhi", city_tier="metro", pincode="110001",
                  gross_revenue_paise=60000, taxes_paise=5000, refunds_paise=6000),
            order("O3", "e3", "2026-09-20 18:00:00", customer_key="c3", city="Bengaluru", city_tier="metro", pincode="560001",
                  payment_method="cod", status="rto", gross_revenue_paise=30000, taxes_paise=2000),
            order("O4", "e4", "2026-09-21 18:30:00", customer_key="c4", city="Pune", city_tier="tier_1", pincode="411001", gross_revenue_paise=7000),
            order("O5", "e5", "2026-09-15 06:00:00", customer_key="c5", status="cancelled", gross_revenue_paise=99999),
        ],
        items=[
            ItemRow("O1", "A", 2, 50000), ItemRow("O2", "A", 1, 40000), ItemRow("O2", "B", 1, 20000), ItemRow("O3", "B", 1, 30000),
            ItemRow("O4", "A", 1, 7000), ItemRow("O5", "A", 1, 99999),
        ],
        fees=[
            FeeRow("O1", "commission", 5000), FeeRow("O1", "shipping", 4000), FeeRow("O1", "payment_gateway", 2000),
            FeeRow("O1", "commission", 9999, "estimated"),
            FeeRow("O2", "commission", 6000), FeeRow("O2", "shipping", 3000),
            FeeRow("O3", "shipping", 3500),
        ],
        returns=[ReturnRow("O2", "customer_return", 1500), ReturnRow("O3", "RTO", 2500)],
    )
    return r


def spend_rows() -> list[AdSpendRow]:
    """meta camp1 daily 14..20 Sep: spend 10000, reported revenue 25000 (7 days: 70000 / 175000).
    google g1: 14 Sep spend 20000 revenue 70000; 15 Sep spend 10000 revenue NULL.
    Also: meta daily impressions 1000, clicks 50, conversions 2 (7d: 7000 / 350 / 14);
    google 14 Sep impressions 3000, clicks 100, conversions 4.5; google 15 Sep impressions 2000, clicks 60, conversions NULL."""
    rows = [AdSpendRow("meta", "m1", date(2026, 9, 14) + timedelta(days=i), 10000, 25000, 1000, 50, Decimal(2)) for i in range(7)]
    rows.append(AdSpendRow("google", "g1", date(2026, 9, 14), 20000, 70000, 3000, 100, Decimal("4.5")))
    rows.append(AdSpendRow("google", "g1", date(2026, 9, 15), 10000, None, 2000, 60, None))
    return rows


def repo_with_spend() -> InMemoryRepository:
    r = main_repo()
    r.add(ORG, ad_spend=spend_rows())
    return r


def traffic_rows() -> list[TrafficRow]:
    """shopify 14 Sep 1000 sessions, 15 Sep 1200; amazon 14 Sep 500 (total 2700 over 2 distinct days)."""
    return [TrafficRow("shopify", date(2026, 9, 14), 1000), TrafficRow("shopify", date(2026, 9, 15), 1200), TrafficRow("amazon", date(2026, 9, 14), 500)]
