"""Data-access layer for the metric layer.

Repositories only fetch ledger rows. They contain NO business formulas: all metric math lives in
the metric modules. `InMemoryRepository` serves tests; `PostgresRepository` serves production.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, NamedTuple, Protocol, Sequence

_TIER_ALIASES = {"tier1": "tier_1", "tier2": "tier_2", "tier3": "tier_3"}


def normalise_tier(tier: str | None) -> str:
    """Canonical enum metro|tier_1|tier_2|tier_3|unknown (CONTRACTS section 8); accepts legacy tier1..3."""
    if tier is None:
        return "unknown"
    return _TIER_ALIASES.get(tier, tier)


# Row types are NamedTuples (immutable, C-speed construction via `_make`): the metric layer may read 100k+ rows per query.
class OrderRow(NamedTuple):
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


class ItemRow(NamedTuple):
    order_id: str
    sku: str
    qty: int
    unit_price_paise: int


class FeeRow(NamedTuple):
    order_id: str
    type: str  # commission|shipping|payment_gateway|fulfilment|other
    amount_paise: int
    source: str = "api"  # api | csv | estimated


class ReturnRow(NamedTuple):
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
    impressions: int = 0
    clicks: int = 0
    platform_reported_conversions: Decimal | None = None


@dataclass(frozen=True)
class TrafficRow:
    channel: str
    date: date
    sessions: int


class MetricsRepository(Protocol):
    def orders(self, org_id: str, start: date, end: date) -> Sequence[OrderRow]:
        """Orders with ordered_date_ist in [start, end] (all statuses)."""

    def orders_for_customers(self, org_id: str, customer_keys: Iterable[str], up_to: date) -> Sequence[OrderRow]:
        """All orders (any date <= up_to) of the given customer_keys."""

    def items(self, org_id: str, order_ids: Iterable[str]) -> Sequence[ItemRow]: ...
    def fees(self, org_id: str, order_ids: Iterable[str]) -> Sequence[FeeRow]: ...
    def returns(self, org_id: str, order_ids: Iterable[str]) -> Sequence[ReturnRow]: ...
    def items_in_range(self, org_id: str, start: date, end: date) -> Sequence[ItemRow]:
        """Items of all orders with ordered_date_ist in [start, end] (date-scoped; avoids id lists)."""
    def fees_in_range(self, org_id: str, start: date, end: date) -> Sequence[FeeRow]: ...
    def returns_in_range(self, org_id: str, start: date, end: date) -> Sequence[ReturnRow]: ...
    def traffic(self, org_id: str, start: date, end: date) -> Sequence[TrafficRow]: ...
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
    _traffic: dict[str, list[TrafficRow]] = field(default_factory=dict)

    def add(self, org_id: str, *, orders=(), items=(), fees=(), returns=(), sku_cogs=(), skus=(), ad_spend=(), traffic=()) -> "InMemoryRepository":
        for store, rows in (
            (self._orders, orders), (self._items, items), (self._fees, fees), (self._returns, returns),
            (self._sku_cogs, sku_cogs), (self._skus, skus), (self._ad_spend, ad_spend), (self._traffic, traffic),
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

    def _ids_in_range(self, org_id, start, end):
        return {o.id for o in self._orders.get(org_id, []) if start <= o.ordered_date_ist <= end}

    def items_in_range(self, org_id, start, end):
        return self.items(org_id, self._ids_in_range(org_id, start, end))

    def fees_in_range(self, org_id, start, end):
        return self.fees(org_id, self._ids_in_range(org_id, start, end))

    def returns_in_range(self, org_id, start, end):
        return self.returns(org_id, self._ids_in_range(org_id, start, end))

    def traffic(self, org_id, start, end):
        return [r for r in self._traffic.get(org_id, []) if start <= r.date <= end]

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

    # Column order == OrderRow field order, so rows convert with OrderRow._make (no per-row Python).
    _ORDER_COLS = (
        "id::text, channel, external_id, ordered_at, ordered_date_ist, payment_method, status, gross_revenue_paise, "
        "discounts_paise, taxes_paise, shipping_charged_paise, refunds_paise, customer_key, city, city_tier, pincode"
    )

    def __init__(self, conn: Any):
        self._conn = conn

    def _q(self, sql: str, params: tuple) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def orders(self, org_id, start, end):
        rows = self._q(
            f"select {self._ORDER_COLS} from orders where org_id = %s and ordered_date_ist between %s and %s",
            (org_id, start, end),
        )
        return list(map(partial(tuple.__new__, OrderRow), rows))

    def orders_for_customers(self, org_id, customer_keys, up_to):
        keys = list(customer_keys)
        if not keys:
            return []
        rows = self._q(
            f"select {self._ORDER_COLS} from orders where org_id = %s and customer_key = any(%s) and ordered_date_ist <= %s",
            (org_id, keys, up_to),
        )
        return list(map(partial(tuple.__new__, OrderRow), rows))

    def items(self, org_id, order_ids):
        ids = list(order_ids)
        if not ids:
            return []
        rows = self._q(
            "select order_id::text, sku, qty, unit_price_paise from order_items where org_id = %s and order_id = any(%s::uuid[])",
            (org_id, ids),
        )
        return list(map(partial(tuple.__new__, ItemRow), rows))

    def fees(self, org_id, order_ids):
        ids = list(order_ids)
        if not ids:
            return []
        rows = self._q(
            "select order_id::text, type, amount_paise, source from fees where org_id = %s and order_id = any(%s::uuid[])",
            (org_id, ids),
        )
        return list(map(partial(tuple.__new__, FeeRow), rows))

    def returns(self, org_id, order_ids):
        ids = list(order_ids)
        if not ids:
            return []
        rows = self._q(
            "select order_id::text, type, cost_paise from returns where org_id = %s and order_id = any(%s::uuid[])",
            (org_id, ids),
        )
        return list(map(partial(tuple.__new__, ReturnRow), rows))

    def _range_rows(self, sql_cols: str, table: str, org_id, start, end) -> list[tuple]:
        return self._q(
            f"select {sql_cols} from {table} t join orders o on o.id = t.order_id "
            "where t.org_id = %s and o.org_id = %s and o.ordered_date_ist between %s and %s",
            (org_id, org_id, start, end),
        )

    def items_in_range(self, org_id, start, end):
        rows = self._range_rows("t.order_id::text, t.sku, t.qty, t.unit_price_paise", "order_items", org_id, start, end)
        return list(map(partial(tuple.__new__, ItemRow), rows))

    def fees_in_range(self, org_id, start, end):
        rows = self._range_rows("t.order_id::text, t.type, t.amount_paise, t.source", "fees", org_id, start, end)
        return list(map(partial(tuple.__new__, FeeRow), rows))

    def returns_in_range(self, org_id, start, end):
        rows = self._range_rows("t.order_id::text, t.type, t.cost_paise", "returns", org_id, start, end)
        return list(map(partial(tuple.__new__, ReturnRow), rows))

    def traffic(self, org_id, start, end):
        rows = self._q("select channel, date, sessions from traffic_daily where org_id = %s and date between %s and %s",
                       (org_id, start, end))
        return [TrafficRow(*r) for r in rows]

    def sku_cogs(self, org_id):
        return [SkuCogsRow(*r) for r in self._q(
            "select sku, effective_from, cogs_paise from sku_cogs where org_id = %s", (org_id,))]

    def skus(self, org_id):
        return [SkuRow(sku=r[0], category=r[1], name=r[2]) for r in self._q(
            "select sku, category, name from skus where org_id = %s", (org_id,))]

    def ad_spend(self, org_id, start, end):
        rows = self._q(
            "select platform, campaign_id, date, spend_paise, platform_reported_revenue_paise, impressions, clicks, "
            "platform_reported_conversions from ad_spend_daily "
            "where org_id = %s and date between %s and %s",
            (org_id, start, end),
        )
        return [AdSpendRow(*r) for r in rows]
