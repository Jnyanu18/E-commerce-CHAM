"""expected_rto_loss (the only implementation).

At-risk orders = non-cancelled COD orders in the period with status 'placed' or 'shipped' (not yet delivered).
Order value = gross_revenue - discounts (cash to be collected). Expected loss per order = order value x RTO rate.

RTO rate of a segment = rto orders / resolved orders, where resolved = status in (delivered, returned, rto), measured over
the lookback window of `lookback_days` ending at the period end (all orders, unfiltered by the request filters).
Segment ladder (finest first); the first level with >= min_history resolved orders is used:
  0 category x city_tier x payment_method
  1 city_tier x payment_method
  2 payment_method
If no level qualifies for some at-risk order the whole result is null / 'insufficient' - no fake number.
An order's category is the category with the highest line value in that order.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .core import GROUP_KEYS, ITEM_FILTERS, ORDER_FILTERS, aggregate_orders, load_orders, order_field, primary_category, validate_request
from .repository import MetricsRepository, normalise_tier
from .result import NO_ORDERS, make_result
from .types import DateRange, Filters, MetricResult, normalise_filters

LOOKBACK_DAYS = 180
MIN_HISTORY = 30
AT_RISK_STATUSES = ("placed", "shipped")
RESOLVED_STATUSES = ("delivered", "returned", "rto")
LEVELS = ("category+city_tier+payment_method", "city_tier+payment_method", "payment_method")

DEFINITION = (
    "expected_rto_loss = sum over undelivered (placed/shipped) COD orders of order value (gross - discounts) x historical RTO "
    "rate, where rate = rto orders / resolved (delivered+returned+rto) orders for the segment category x city tier x payment "
    "method over the trailing 180 days. Falls back to city tier x payment method, then payment method, when a segment has "
    "fewer than 30 resolved orders (reported in data_quality). If no segment has enough history the value is null."
)


def _segment_keys(category: str, tier: str, pm: str):
    return [(category, tier, pm), (tier, pm), (pm,)]


def expected_rto_loss(
    org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository,
    min_history: int = MIN_HISTORY, lookback_days: int = LOOKBACK_DAYS,
) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = normalise_filters(filters)
    validate_request(group_by, nf, GROUP_KEYS, ORDER_FILTERS + ITEM_FILTERS)
    order_filters = {k: v for k, v in nf.items() if k in ORDER_FILTERS}
    issues: list[str] = []
    orders = load_orders(repo, org_id, dr, order_filters, issues)

    at_risk = [o for o in orders if o.payment_method == "cod" and o.status in AT_RISK_STATUSES]
    needs_hist = bool(at_risk)

    # Category lookup for at-risk and history orders.
    category_of = {s.sku: s.category for s in repo.skus(org_id)} if needs_hist else {}
    hist_counts: list[dict[tuple, list[int]]] = [defaultdict(lambda: [0, 0]) for _ in LEVELS]
    if needs_hist:
        lo = dr.end - timedelta(days=lookback_days)
        hist = [o for o in repo.orders(org_id, lo, dr.end) if o.status in RESOLVED_STATUSES]
        seen: set[str] = set()
        hist = [o for o in hist if not (o.id in seen or seen.add(o.id))]
        items: dict[str, list] = defaultdict(list)
        wanted = {o.id for o in hist} | {o.id for o in at_risk}
        for it in repo.items_in_range(org_id, lo, dr.end):
            if it.order_id in wanted:
                items[it.order_id].append(it)
        for o in hist:
            cat = primary_category(items.get(o.id, []), category_of)
            for lvl, key in enumerate(_segment_keys(cat, normalise_tier(o.city_tier), o.payment_method)):
                cell = hist_counts[lvl][key]
                cell[0] += 1
                if o.status == "rto":
                    cell[1] += 1

    losses: dict[str, dict[str, int]] = {}
    fallback_use: dict[str, int] = defaultdict(int)
    unrated = 0
    for o in at_risk:
        cat = primary_category(items.get(o.id, []), category_of)
        rate = None
        for lvl, key in enumerate(_segment_keys(cat, normalise_tier(o.city_tier), o.payment_method)):
            resolved, rto = hist_counts[lvl].get(key, [0, 0])
            if resolved >= min_history:
                rate = (rto, resolved)
                if lvl:
                    fallback_use[LEVELS[lvl]] += 1
                break
        if rate is None:
            unrated += 1
            continue
        value = o.gross_revenue_paise - o.discounts_paise
        rto, resolved = rate
        losses[o.id] = {"rto_loss": (2 * value * rto + resolved) // (2 * resolved)}

    if fallback_use:
        issues.append("rto_rate_fallback_segments_used: " + ", ".join(f"{k}={v} orders" for k, v in sorted(fallback_use.items())))
    if unrated:
        issues.append(f"rto_history_insufficient: {unrated} at-risk orders have no segment with >= {min_history} resolved orders in the last {lookback_days} days")

    agg = aggregate_orders(repo, org_id, dr, orders, nf, group_by, set(), issues, extra_order_comps=losses)
    if not orders:
        issues.append(NO_ORDERS)
    value = None if (unrated or not orders) else agg.totals.get("rto_loss", 0)
    gv = {k: v.get("rto_loss", 0) for k, v in agg.groups.items()} if value is not None else {}
    return make_result(metric="expected_rto_loss", value=value, unit="paise", dr=dr, filters=filters, group_by=group_by,
                       group_values=gv, definition=DEFINITION, issues=issues, row_count=len(at_risk))
