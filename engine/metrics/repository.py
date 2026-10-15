"""Data-access layer for the metric layer.

Repositories only fetch ledger rows. They contain NO business formulas: all metric math lives in
the metric modules. `InMemoryRepository` serves tests; `PostgresRepository` serves production.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Protocol, Sequence

_TIER_ALIASES = {"tier1": "tier_1", "tier2": "tier_2", "tier3": "tier_3"}


def normalise_tier(tier: str | None) -> str:
    """Canonical enum metro|tier_1|tier_2|tier_3|unknown (CONTRACTS section 8); accepts legacy tier1..3."""
    if tier is None:
        return "unknown"
    return _TIER_ALIASES.get(tier, tier)


@dataclass(frozen=True)
class OrderRow:
    id: str
    channel: str
    external_id: str
    ordered_at: datetime
    ordered_date_ist: date
    payment_method: str  # prepaid | cod
    status: str  # placed|shipped|delivered|rto|cancelled|returned
    gross_revenue_paise: int
    discounts_paise: int = 0
    taxes_paise: int = 0
    shipping_charged_paise: int = 0
    refunds_paise: int = 0
    customer_key: str | None = None
    city: str | None = None
    city_tier: str | None = None
    pincode: str | None = None


@dataclass(frozen=True)
class ItemRow:
    order_id: str
    sku: str
    qty: int
    unit_price_paise: int


@dataclass(frozen=True)
class FeeRow:
    order_id: str
    type: str  # commission|shipping|payment_gateway|fulfilment|other
    amount_paise: int
    source: str = "api"  # api | csv | estimated


@dataclass(frozen=True)
class ReturnRow:
    order_id: str
    type: str  # RTO | customer_return
    cost_paise: int = 0


@dataclass(frozen=True)
class SkuCogsRow:
    sku: str
    effective_from: date
    cogs_paise: int


@dataclass(frozen=True)
class SkuRow:
    sku: str
    category: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class AdSpendRow:
    platform: str
    campaign_id: str
    date: date
    spend_paise: int
    platform_reported_revenue_paise: int | None = None


class MetricsRepository(Protocol):
    def orders(self, org_id: str, start: date, end: date) -> Sequence[OrderRow]:
        """Orders with ordered_date_ist in [start, end] (all statuses)."""

    def orders_for_customers(self, org_id: str, customer_keys: Iterable[str], up_to: date) -> Sequence[OrderRow]:
        """All orders (any date <= up_to) of the given customer_keys."""

    def items(self, org_id: str, order_ids: Iterable[str]) -> Sequence[ItemRow]: ...
    def fees(self, org_id: str, order_ids: Iterable[str]) -> Sequence[FeeRow]: ...
    def returns(self, org_id: str, order_ids: Iterable[str]) -> Sequence[ReturnRow]: ...
    def sku_cogs(self, org_id: str) -> Sequence[SkuCogsRow]: ...
    def skus(self, org_id: str) -> Sequence[SkuRow]: ...
    def ad_spend(self, org_id: str, start: date, end: date) -> Sequence[AdSpendRow]: ...


@dataclass
class InMemoryRepository:
    """Fixture-backed repository. Rows are stored per org_id."""

    _orders: dict[str, list[OrderRow]] = field(default_factory=dict)
    _items: dict[str, list[ItemRow]] = field(default_factory=dict)
    _fees: dict[str, list[FeeRow]] = field(default_factory=dict)
    _returns: dict[str, list[ReturnRow]] = field(default_factory=dict)
    _sku_cogs: dict[str, list[SkuCogsRow]] = field(default_factory=dict)
    _skus: dict[str, list[SkuRow]] = field(default_factory=dict)
    _ad_spend: dict[str, list[AdSpendRow]] = field(default_factory=dict)

    def add(self, org_id: str, *, orders=(), items=(), fees=(), returns=(), sku_cogs=(), skus=(), ad_spend=()) -> "InMemoryRepository":
        for store, rows in (
            (self._orders, orders), (self._items, items), (self._fees, fees), (self._returns, returns),
            (self._sku_cogs, sku_cogs), (self._skus, skus), (self._ad_spend, ad_spend),
        ):
            store.setdefault(org_id, []).extend(rows)
        return self

    def orders(self, org_id, start, end):
        return [o for o in self._orders.get(org_id, []) if start <= o.ordered_date_ist <= end]

    def orders_for_customers(self, org_id, customer_keys, up_to):
        keys = set(customer_keys)
        return [o for o in self._orders.get(org_id, []) if o.customer_key in keys and o.ordered_date_ist <= up_to]

    def items(self, org_id, order_ids):
        ids = set(order_ids)
        return [r for r in self._items.get(org_id, []) if r.order_id in ids]

    def fees(self, org_id, order_ids):
        ids = set(order_ids)
        return [r for r in self._fees.get(org_id, []) if r.order_id in ids]

    def returns(self, org_id, order_ids):
        ids = set(order_ids)
        return [r for r in self._returns.get(org_id, []) if r.order_id in ids]

    def sku_cogs(self, org_id):
        return list(self._sku_cogs.get(org_id, []))

    def skus(self, org_id):
        return list(self._skus.get(org_id, []))

    def ad_spend(self, org_id, start, end):
        return [r for r in self._ad_spend.get(org_id, []) if start <= r.date <= end]


class PostgresRepository:
    """Reads ledger tables with plain SELECTs (no aggregation or formulas in SQL).

    `conn` is an open psycopg connection. Every query is scoped by org_id; callers choose the
    role/connection. NOTE: not yet verified against a live Postgres (Docker unavailable when written).
    """

    _ORDER_COLS = (
        "id::text, channel, external_id, ordered_at, ordered_date_ist, customer_key, city, city_tier, pincode, "
        "payment_method, status, gross_revenue_paise, discounts_paise, taxes_paise, shipping_charged_paise, refunds_paise"
    )

    def __init__(self, conn: Any):
        self._conn = conn

    def _q(self, sql: str, params: tuple) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    @staticmethod
    def _order(r: tuple) -> OrderRow:
        return OrderRow(
            id=r[0], channel=r[1], external_id=r[2], ordered_at=r[3], ordered_date_ist=r[4], customer_key=r[5],
            city=r[6], city_tier=normalise_tier(r[7]) if r[7] is not None else None, pincode=r[8],
            payment_method=r[9], status=r[10], gross_revenue_paise=r[11], discounts_paise=r[12],
            taxes_paise=r[13], shipping_charged_paise=r[14], refunds_paise=r[15],
        )

    def orders(self, org_id, start, end):
        rows = self._q(
            f"select {self._ORDER_COLS} from orders where org_id = %s and ordered_date_ist between %s and %s",
            (org_id, start, end),
        )
        return [self._order(r) for r in rows]

    def orders_for_customers(self, org_id, customer_keys, up_to):
        keys = list(customer_keys)
        if not keys:
            return []
        rows = self._q(
            f"select {self._ORDER_COLS} from orders where org_id = %s and customer_key = any(%s) and ordered_date_ist <= %s",
            (org_id, keys, up_to),
        )
        return [self._order(r) for r in rows]

    def items(self, org_id, order_ids):
        ids = list(order_ids)
        if not ids:
            return []
        rows = self._q(
            "select order_id::text, sku, qty, unit_price_paise from order_items where org_id = %s and order_id = any(%s::uuid[])",
            (org_id, ids),
        )
        return [ItemRow(*r) for r in rows]

    def fees(self, org_id, order_ids):
        ids = list(order_ids)
        if not ids:
            return []
        rows = self._q(
            "select order_id::text, type, amount_paise, source from fees where org_id = %s and order_id = any(%s::uuid[])",
            (org_id, ids),
        )
        return [FeeRow(*r) for r in rows]

    def returns(self, org_id, order_ids):
        ids = list(order_ids)
        if not ids:
            return []
        rows = self._q(
            "select order_id::text, type, cost_paise from returns where org_id = %s and order_id = any(%s::uuid[])",
            (org_id, ids),
        )
        return [ReturnRow(*r) for r in rows]

    def sku_cogs(self, org_id):
        return [SkuCogsRow(*r) for r in self._q(
            "select sku, effective_from, cogs_paise from sku_cogs where org_id = %s", (org_id,))]

    def skus(self, org_id):
        return [SkuRow(sku=r[0], category=r[1], name=r[2]) for r in self._q(
            "select sku, category, name from skus where org_id = %s", (org_id,))]

    def ad_spend(self, org_id, start, end):
        rows = self._q(
            "select platform, campaign_id, date, spend_paise, platform_reported_revenue_paise from ad_spend_daily "
            "where org_id = %s and date between %s and %s",
            (org_id, start, end),
        )
        return [AdSpendRow(*r) for r in rows]
