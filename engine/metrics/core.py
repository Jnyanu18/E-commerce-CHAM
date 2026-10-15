"""Order-level building blocks shared by every ledger-based metric.

This is the ONLY place that decides which orders count, how COGS/fees/returns are resolved, and how
order amounts are split across SKU/category groups. Metric modules combine the components below.

Components (all integer paise, per order):
  gross, discounts, taxes, refunds   - zero for status 'rto' (parcel never delivered: sale not realised)
  cogs                               - sum(qty x effective-dated unit COGS); zero for 'rto'
  fees                               - fees of every type except 'shipping'
  shipping                           - fees of type 'shipping'
  returns                            - returns.cost_paise (RTO + customer_return logistics loss)
  orders                             - 1 per realised order (non-cancelled, non-rto)
Cancelled orders are excluded from every metric.
"""
from __future__ import annotations

import gc
from bisect import bisect_right
from contextlib import contextmanager
from functools import lru_cache
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Iterable, Mapping

from .periods import week_start
from .repository import ItemRow, MetricsRepository, OrderRow, normalise_tier
from .types import DateRange, MetricError

ORDER_FILTERS = ("channel", "city", "city_tier", "pincode", "payment_method")
ITEM_FILTERS = ("sku", "category")
ORDER_GROUPS = ("channel", "city", "city_tier", "pincode", "payment_method", "week", "day")
ITEM_GROUPS = ("sku", "category")
GROUP_KEYS = ORDER_GROUPS + ITEM_GROUPS
UNKNOWN = "unknown"
MISSING_SKU = "__MISSING_SKU__"  # ledger convention: item whose SKU could not be resolved -> COGS unknown
_FEE_SOURCE_PRIORITY = {"api": 0, "csv": 1, "estimated": 2}

Comps = dict[str, int]
COMP_ORDER = ("orders", "gross", "discounts", "taxes", "refunds", "cogs", "fees", "shipping", "returns")


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


@lru_cache(maxsize=4096)
def _day_key(d) -> str:
    return d.isoformat()


@lru_cache(maxsize=4096)
def _week_key(d) -> str:
    return week_start(d).isoformat()


def order_field(order: OrderRow, key: str) -> str:
    if key == "day":
        return _day_key(order.ordered_date_ist)
    if key == "week":
        return _week_key(order.ordered_date_ist)
    if key == "city_tier":
        return normalise_tier(order.city_tier)
    val = getattr(order, key)
    return val if val else UNKNOWN


@contextmanager
def paused_gc():
    """Metric queries allocate 100k+ short-lived containers; the cyclic GC then dominates runtime (~40%). Pause it."""
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


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
    return out


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
    dr: DateRange,
    orders: list[OrderRow],
    filters: Mapping[str, list[str]],
    group_by: str | None,
    comps: set[str],
    issues: list[str],
    extra_order_comps: Mapping[str, Comps] | None = None,
) -> Aggregate:
    """Build per-order components, expand to SKU lines when needed, filter and group.

    Hot path: components are positional int vectors (aligned with `names`), accumulated directly into totals/groups.
    """
    expand = group_by in ITEM_GROUPS or any(k in filters for k in ITEM_FILTERS)
    need_items = expand or "cogs" in comps
    ids = {o.id for o in orders}

    items_by_order: dict[str, list[ItemRow]] = defaultdict(list)
    if need_items:
        seen: set[tuple[str, str]] = set()
        dup = 0
        for it in repo.items_in_range(org_id, dr.start, dr.end):
            if it.order_id not in ids:
                continue
            key = (it.order_id, it.sku)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            items_by_order[it.order_id].append(it)
        if dup:
            issues.append(f"duplicate_order_items_ignored: {dup}")

    category_of: dict[str, str | None] = {}
    if expand and ("category" in filters or group_by == "category"):
        category_of = {s.sku: s.category for s in repo.skus(org_id)}
        used = {it.sku for lst in items_by_order.values() for it in lst}
        no_cat = sorted(s for s in used if not category_of.get(s))
        if no_cat:
            issues.append(f"category_missing_for_skus: {no_cat}")

    # fees per order: [non-shipping fees, shipping fee]; per (order, type) the best source wins (api > csv > estimated)
    fee_vec: dict[str, list[int]] = {}
    est_orders: set[str] = set()
    if comps & {"fees", "shipping"}:
        best: dict[tuple[str, str], tuple[int, int, str]] = {}
        seen_f: set[tuple[str, str, str]] = set()
        dup = 0
        for f in repo.fees_in_range(org_id, dr.start, dr.end):
            if f.order_id not in ids:
                continue
            k3 = (f.order_id, f.type, f.source)
            if k3 in seen_f:
                dup += 1
                continue
            seen_f.add(k3)
            pr = _FEE_SOURCE_PRIORITY.get(f.source, 3)
            k2 = (f.order_id, f.type)
            cur = best.get(k2)
            if cur is None or pr < cur[0]:
                best[k2] = (pr, f.amount_paise, f.source)
        for (oid, ftype), (_, amt, src) in best.items():
            v = fee_vec.setdefault(oid, [0, 0])
            v[1 if ftype == "shipping" else 0] += amt
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
        for r in repo.returns_in_range(org_id, dr.start, dr.end):
            if r.order_id not in ids:
                continue
            if (r.order_id, r.type) in seen_r:
                dup += 1
                continue
            seen_r.add((r.order_id, r.type))
            returns_by_order[r.order_id] += r.cost_paise
        if dup:
            issues.append(f"duplicate_returns_ignored: {dup}")

    resolver = CogsResolver(repo.sku_cogs(org_id)) if "cogs" in comps else None
    cogs_cache: dict[tuple, int | None] = {}
    missing_cogs: set[str] = set()
    no_items = 0

    extra_names = sorted({k for v in (extra_order_comps or {}).values() for k in v})
    names = [c for c in COMP_ORDER if c in comps] + extra_names
    idx = {c: i for i, c in enumerate(names)}
    n = len(names)
    i_orders, i_gross, i_disc, i_tax, i_ref = (idx.get(c, -1) for c in ("orders", "gross", "discounts", "taxes", "refunds"))
    i_fees, i_ship, i_ret, i_cogs = (idx.get(c, -1) for c in ("fees", "shipping", "returns", "cogs"))
    extra_idx = [(k, idx[k]) for k in extra_names]
    split_idx = [i for i in range(n) if i != i_cogs]  # components allocated across item lines (COGS is direct)

    totals = [0] * n
    groups: dict[str, list[int]] = {}
    sku_f = filters.get("sku")
    cat_f = filters.get("category")
    rows: set[str] = set()
    rng = range(n)

    def add(key, vec, oid):
        rows.add(oid)
        for j in rng:
            totals[j] += vec[j]
        if key is not None:
            g = groups.get(key)
            if g is None:
                groups[key] = list(vec)
            else:
                for j in rng:
                    g[j] += vec[j]

    by_item_key = group_by in ITEM_GROUPS
    for o in orders:
        realised = o.status != "rto"
        items = items_by_order.get(o.id, ())
        vec = [0] * n
        if realised:
            if i_orders >= 0:
                vec[i_orders] = 1
            if i_gross >= 0:
                vec[i_gross] = o.gross_revenue_paise
            if i_disc >= 0:
                vec[i_disc] = o.discounts_paise
            if i_tax >= 0:
                vec[i_tax] = o.taxes_paise
            if i_ref >= 0:
                vec[i_ref] = o.refunds_paise
        if i_fees >= 0 or i_ship >= 0:
            fv = fee_vec.get(o.id)
            if fv is not None:
                if i_fees >= 0:
                    vec[i_fees] = fv[0]
                if i_ship >= 0:
                    vec[i_ship] = fv[1]
        if i_ret >= 0:
            vec[i_ret] = returns_by_order.get(o.id, 0)
        if extra_idx:
            ex = extra_order_comps.get(o.id)  # type: ignore[union-attr]
            if ex:
                for k, j in extra_idx:
                    vec[j] = ex[k]

        item_cogs: dict[str, int] = {}
        cogs_total = 0
        if resolver is not None:
            if not items:
                no_items += 1
            for it in items:
                if not realised:
                    item_cogs[it.sku] = 0
                    continue
                ck = (it.sku, o.ordered_date_ist)
                if ck in cogs_cache:
                    unit = cogs_cache[ck]
                else:
                    unit = None if it.sku == MISSING_SKU else resolver.unit_cogs(it.sku, o.ordered_date_ist)
                    cogs_cache[ck] = unit
                if unit is None:
                    missing_cogs.add(it.sku)
                    item_cogs[it.sku] = 0
                else:
                    line_cogs = it.qty * unit
                    item_cogs[it.sku] = line_cogs
                    cogs_total += line_cogs

        if not expand:
            if i_cogs >= 0:
                vec[i_cogs] = cogs_total
            add(order_field(o, group_by) if group_by else None, vec, o.id)
            continue
        okey = None if (group_by is None or by_item_key) else order_field(o, group_by)
        if not items:
            lines = [(UNKNOWN, UNKNOWN, vec)]  # cogs stays 0 (flagged above)
        else:
            items = sorted(items, key=lambda i: i.sku)
            weights = [it.qty * it.unit_price_paise for it in items]
            split = {j: allocate(vec[j], weights) for j in split_idx}
            lines = []
            for k, it in enumerate(items):
                lv = [0] * n
                for j in split_idx:
                    lv[j] = split[j][k]
                if i_cogs >= 0:
                    lv[i_cogs] = item_cogs[it.sku]
                lines.append((it.sku, category_of.get(it.sku) or UNKNOWN, lv))
        for sku, cat, lv in lines:
            if sku_f is not None and sku not in sku_f:
                continue
            if cat_f is not None and cat not in cat_f:
                continue
            key = okey if not by_item_key else (sku if group_by == "sku" else cat)
            add(key, lv, o.id)

    if missing_cogs:
        issues.append(f"cogs_missing_for_skus: {sorted(missing_cogs)}")
    if no_items:
        issues.append(f"orders_without_items_cogs_unknown: {no_items}")
    return Aggregate(dict(zip(names, totals)), {k: dict(zip(names, v)) for k, v in groups.items()}, len(rows), issues)


def aggregate_ledger(
    repo: MetricsRepository, org_id: str, dr: DateRange, filters: Mapping[str, list[str]], group_by: str | None, comps: set[str]
) -> Aggregate:
    issues: list[str] = []
    with paused_gc():
        orders = load_orders(repo, org_id, dr, filters, issues)
        return aggregate_orders(repo, org_id, dr, orders, filters, group_by, comps, issues)


def history_start(end, days: int):
    return end - timedelta(days=days)


def gc_paused(fn):
    """Decorator form of paused_gc for whole metric functions."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **k):
        with paused_gc():
            return fn(*a, **k)

    return wrapper
