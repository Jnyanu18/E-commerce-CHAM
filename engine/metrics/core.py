"""Order-level building blocks shared by every ledger-based metric.

This is the ONLY place that decides which orders count, how COGS/fees/returns are resolved, and how
order amounts are split across SKU/category groups. Metric modules combine the components below.

Components (all integer paise, per order):
  gross, discounts, taxes, refunds   - zero for status 'rto' (parcel never delivered: sale not realised)
  cogs                               - sum(qty x effective-dated unit COGS); zero for 'rto'
  fees                               - fees of every type except 'shipping'
  shipping                           - fees of type 'shipping'
  returns                            - returns.cost_paise (RTO + customer_return logistics loss)
Cancelled orders are excluded from every metric.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Iterable, Mapping

from .periods import week_start
from .repository import ItemRow, MetricsRepository, OrderRow
from .types import DateRange, MetricError

ORDER_FILTERS = ("channel", "city", "city_tier", "pincode", "payment_method")
ITEM_FILTERS = ("sku", "category")
ORDER_GROUPS = ("channel", "city", "city_tier", "pincode", "payment_method", "week")
ITEM_GROUPS = ("sku", "category")
GROUP_KEYS = ORDER_GROUPS + ITEM_GROUPS
UNKNOWN = "unknown"
MISSING_SKU = "__MISSING_SKU__"  # ledger convention: item whose SKU could not be resolved -> COGS unknown
_FEE_SOURCE_PRIORITY = {"api": 0, "csv": 1, "estimated": 2}

Comps = dict[str, int]


def validate_request(group_by: str | None, filters: Mapping[str, list[str]], allowed_groups: tuple[str, ...], allowed_filters: tuple[str, ...]) -> None:
    if group_by is not None and group_by not in allowed_groups:
        raise MetricError(f"unsupported group_by {group_by!r}; allowed: {sorted(allowed_groups)}")
    bad = sorted(set(filters) - set(allowed_filters))
    if bad:
        raise MetricError(f"unsupported filter(s) {bad}; allowed: {sorted(allowed_filters)}")


def allocate(amount: int, weights: list[int]) -> list[int]:
    """Split integer `amount` proportionally to `weights` so parts sum exactly (largest remainder)."""
    n = len(weights)
    total = sum(weights)
    if total <= 0:
        weights, total = [1] * n, n
    parts = [amount * w // total for w in weights]
    rest = amount - sum(parts)
    by_remainder = sorted(range(n), key=lambda i: (-((amount * weights[i]) % total), i))
    for i in by_remainder[:rest]:
        parts[i] += 1
    return parts


def order_field(order: OrderRow, key: str) -> str:
    if key == "week":
        return week_start(order.ordered_date_ist).isoformat()
    if key == "city_tier":
        from .repository import normalise_tier

        return normalise_tier(order.city_tier)
    val = getattr(order, key)
    return val if val else UNKNOWN


def load_orders(repo: MetricsRepository, org_id: str, dr: DateRange, filters: Mapping[str, list[str]], issues: list[str]) -> list[OrderRow]:
    """Orders in range, de-duplicated, non-cancelled, order-level filters applied."""
    seen_ids: set[str] = set()
    seen_nat: set[tuple[str, str]] = set()
    out: list[OrderRow] = []
    dupes = 0
    for o in repo.orders(org_id, dr.start, dr.end):
        nat = (o.channel, o.external_id)
        if o.id in seen_ids or nat in seen_nat:
            dupes += 1
            continue
        seen_ids.add(o.id)
        seen_nat.add(nat)
        if o.status == "cancelled":
            continue
        if any(order_field(o, k) not in filters[k] for k in ORDER_FILTERS if k in filters):
            continue
        out.append(o)
    if dupes:
        issues.append(f"duplicate_orders_ignored: {dupes}")
    return sorted(out, key=lambda o: (o.ordered_at, o.id))


class CogsResolver:
    """Effective-dated COGS: the row with the latest effective_from <= order date."""

    def __init__(self, rows: Iterable) -> None:
        by_sku: dict[str, dict] = defaultdict(dict)
        for r in rows:
            by_sku[r.sku][r.effective_from] = r.cogs_paise
        self._dates = {s: sorted(d) for s, d in by_sku.items()}
        self._vals = {s: [d[k] for k in sorted(d)] for s, d in by_sku.items()}

    def unit_cogs(self, sku: str, on) -> int | None:
        dates = self._dates.get(sku)
        if not dates:
            return None
        i = bisect_right(dates, on)
        return self._vals[sku][i - 1] if i else None


@dataclass
class Line:
    order: OrderRow
    sku: str | None
    category: str | None
    comps: Comps


@dataclass
class Aggregate:
    totals: Comps
    groups: dict[str, Comps]
    row_count: int
    issues: list[str] = field(default_factory=list)


def primary_category(items: list[ItemRow], category_of: Mapping[str, str | None]) -> str:
    """Category with the highest line value in the order (ties: alphabetical); 'unknown' if none."""
    value: dict[str, int] = defaultdict(int)
    for it in items:
        value[category_of.get(it.sku) or UNKNOWN] += it.qty * it.unit_price_paise
    if not value:
        return UNKNOWN
    return sorted(value.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def aggregate_orders(
    repo: MetricsRepository,
    org_id: str,
    orders: list[OrderRow],
    filters: Mapping[str, list[str]],
    group_by: str | None,
    comps: set[str],
    issues: list[str],
    extra_order_comps: Mapping[str, Comps] | None = None,
) -> Aggregate:
    """Build per-order components, expand to SKU lines when needed, filter and group."""
    expand = group_by in ITEM_GROUPS or any(k in filters for k in ITEM_FILTERS)
    need_items = expand or "cogs" in comps
    ids = [o.id for o in orders]

    items_by_order: dict[str, list[ItemRow]] = defaultdict(list)
    if need_items:
        seen: set[tuple[str, str]] = set()
        dup = 0
        for it in repo.items(org_id, ids):
            if (it.order_id, it.sku) in seen:
                dup += 1
                continue
            seen.add((it.order_id, it.sku))
            items_by_order[it.order_id].append(it)
        if dup:
            issues.append(f"duplicate_order_items_ignored: {dup}")
        for lst in items_by_order.values():
            lst.sort(key=lambda i: i.sku)

    category_of: dict[str, str | None] = {}
    if expand and ("category" in filters or group_by == "category"):
        category_of = {s.sku: s.category for s in repo.skus(org_id)}
        used = {it.sku for lst in items_by_order.values() for it in lst}
        no_cat = sorted(s for s in used if not category_of.get(s))
        if no_cat:
            issues.append(f"category_missing_for_skus: {no_cat}")

    fees_by_order: dict[str, dict[str, int]] = defaultdict(dict)
    est_orders: set[str] = set()
    if comps & {"fees", "shipping"}:
        best: dict[tuple[str, str], tuple[int, int, str]] = {}
        seen_f: set[tuple[str, str, str]] = set()
        dup = 0
        for f in repo.fees(org_id, ids):
            if (f.order_id, f.type, f.source) in seen_f:
                dup += 1
                continue
            seen_f.add((f.order_id, f.type, f.source))
            pr = _FEE_SOURCE_PRIORITY.get(f.source, 3)
            cur = best.get((f.order_id, f.type))
            if cur is None or pr < cur[0]:
                best[(f.order_id, f.type)] = (pr, f.amount_paise, f.source)
        for (oid, ftype), (_, amt, src) in best.items():
            fees_by_order[oid][ftype] = amt
            if src == "estimated":
                est_orders.add(oid)
        if dup:
            issues.append(f"duplicate_fees_ignored: {dup}")
        if est_orders:
            issues.append(f"fees_estimated_for_orders: {len(est_orders)}")

    returns_by_order: dict[str, int] = defaultdict(int)
    if "returns" in comps:
        seen_r: set[tuple[str, str]] = set()
        dup = 0
        for r in repo.returns(org_id, ids):
            if (r.order_id, r.type) in seen_r:
                dup += 1
                continue
            seen_r.add((r.order_id, r.type))
            returns_by_order[r.order_id] += r.cost_paise
        if dup:
            issues.append(f"duplicate_returns_ignored: {dup}")

    resolver = CogsResolver(repo.sku_cogs(org_id)) if "cogs" in comps else None
    missing_cogs: set[str] = set()
    no_items = 0

    lines: list[Line] = []
    for o in orders:
        realised = o.status != "rto"
        items = items_by_order.get(o.id, [])
        oc: Comps = {}
        if "gross" in comps:
            oc["gross"] = o.gross_revenue_paise if realised else 0
        if "discounts" in comps:
            oc["discounts"] = o.discounts_paise if realised else 0
        if "taxes" in comps:
            oc["taxes"] = o.taxes_paise if realised else 0
        if "refunds" in comps:
            oc["refunds"] = o.refunds_paise if realised else 0
        fo = fees_by_order.get(o.id, {})
        if "fees" in comps:
            oc["fees"] = sum(v for k, v in fo.items() if k != "shipping")
        if "shipping" in comps:
            oc["shipping"] = fo.get("shipping", 0)
        if "returns" in comps:
            oc["returns"] = returns_by_order.get(o.id, 0)
        for k, v in (extra_order_comps or {}).get(o.id, {}).items():
            oc[k] = v

        item_cogs: dict[str, int] = {}
        if resolver is not None:
            if not items:
                no_items += 1
            for it in items:
                if not realised:
                    item_cogs[it.sku] = 0
                    continue
                unit = None if it.sku == MISSING_SKU else resolver.unit_cogs(it.sku, o.ordered_date_ist)
                if unit is None:
                    missing_cogs.add(it.sku)
                    item_cogs[it.sku] = 0
                else:
                    item_cogs[it.sku] = it.qty * unit

        if not expand:
            c = dict(oc)
            if resolver is not None:
                c["cogs"] = sum(item_cogs.values())
            lines.append(Line(o, None, None, c))
            continue
        if not items:
            c = dict(oc)
            if resolver is not None:
                c["cogs"] = 0
            lines.append(Line(o, UNKNOWN, UNKNOWN, c))
            continue
        weights = [it.qty * it.unit_price_paise for it in items]
        split = {k: allocate(v, weights) for k, v in oc.items()}
        for i, it in enumerate(items):
            c = {k: parts[i] for k, parts in split.items()}
            if resolver is not None:
                c["cogs"] = item_cogs[it.sku]
            lines.append(Line(o, it.sku, category_of.get(it.sku) or UNKNOWN, c))

    if expand:
        if "sku" in filters:
            lines = [ln for ln in lines if ln.sku in filters["sku"]]
        if "category" in filters:
            lines = [ln for ln in lines if ln.category in filters["category"]]

    if missing_cogs:
        issues.append(f"cogs_missing_for_skus: {sorted(missing_cogs)}")
    if no_items:
        issues.append(f"orders_without_items_cogs_unknown: {no_items}")

    totals: Comps = defaultdict(int)
    groups: dict[str, Comps] = {}
    for ln in lines:
        if group_by is None:
            key = None
        elif group_by == "sku":
            key = ln.sku
        elif group_by == "category":
            key = ln.category
        else:
            key = order_field(ln.order, group_by)
        for k, v in ln.comps.items():
            totals[k] += v
            if key is not None:
                g = groups.setdefault(key, defaultdict(int))
                g[k] += v
        if key is not None:
            groups.setdefault(key, defaultdict(int))
    return Aggregate(dict(totals), {k: dict(v) for k, v in groups.items()}, len({ln.order.id for ln in lines}), issues)


def aggregate_ledger(
    repo: MetricsRepository, org_id: str, dr: DateRange, filters: Mapping[str, list[str]], group_by: str | None, comps: set[str]
) -> Aggregate:
    issues: list[str] = []
    orders = load_orders(repo, org_id, dr, filters, issues)
    agg = aggregate_orders(repo, org_id, orders, filters, group_by, comps, issues)
    return agg


def history_start(end, days: int):
    return end - timedelta(days=days)
