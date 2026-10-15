"""net_revenue and contribution_margin (the only implementations)."""
from __future__ import annotations

from .core import GROUP_KEYS, ITEM_FILTERS, ORDER_FILTERS, ORDER_GROUPS, Aggregate, aggregate_ledger, validate_request
from .repository import MetricsRepository
from .result import NO_ORDERS, div_paise, make_result
from .types import DateRange, Filters, MetricResult, normalise_filters

ALLOWED_FILTERS = ORDER_FILTERS + ITEM_FILTERS

NET_REVENUE_DEF = (
    "net_revenue = gross_revenue - discounts - taxes (GST) - refunds, summed over non-cancelled orders by IST order date. "
    "Orders with status 'rto' (never delivered) contribute no revenue. Shipping charged to customers is excluded."
)
CONTRIBUTION_DEF = (
    "contribution_margin = net_revenue - COGS - fees - shipping cost - RTO/return cost. COGS = qty x sku_cogs with the "
    "latest effective_from <= order IST date; fees = all fee types except 'shipping' (actual api/csv fees win over "
    "estimated); shipping cost = fees of type 'shipping'; RTO/return cost = returns.cost_paise (logistics loss, excluding "
    "the revenue refund which is already in net_revenue). Missing COGS is flagged, never silently treated as known."
)


def net_of(c: dict[str, int]) -> int:
    return c.get("gross", 0) - c.get("discounts", 0) - c.get("taxes", 0) - c.get("refunds", 0)


def contribution_of(c: dict[str, int]) -> int:
    return net_of(c) - c.get("cogs", 0) - c.get("fees", 0) - c.get("shipping", 0) - c.get("returns", 0)


_NET_COMPS = {"gross", "discounts", "taxes", "refunds"}
_CM_COMPS = _NET_COMPS | {"cogs", "fees", "shipping", "returns"}


def _run(name, definition, fn, comps, org_id, date_range, filters, group_by, repo: MetricsRepository, *, unit="paise", groups_allowed=GROUP_KEYS, filters_allowed=ALLOWED_FILTERS):
    dr = DateRange.of(date_range)
    nf = normalise_filters(filters)
    validate_request(group_by, nf, groups_allowed, filters_allowed)
    agg = aggregate_ledger(repo, org_id, dr, nf, group_by, comps)
    return _finish(name, definition, fn, agg, dr, filters, group_by, unit)


def _finish(name, definition, fn, agg: Aggregate, dr, filters, group_by, unit="paise") -> MetricResult:
    issues = list(agg.issues)
    if agg.row_count == 0:
        issues.append(NO_ORDERS)
        value, groups = None, {}
    else:
        value, groups = fn(agg.totals), {k: fn(v) for k, v in agg.groups.items()}
        if value is None:
            issues.append("no_realised_orders_in_period")
    return make_result(
        metric=name, value=value, unit=unit, dr=dr, filters=filters, group_by=group_by,
        group_values=groups, definition=definition, issues=issues, row_count=agg.row_count,
    )


def net_revenue(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _run("net_revenue", NET_REVENUE_DEF, net_of, _NET_COMPS, org_id, date_range, filters, group_by, repo)


def contribution_margin(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _run("contribution_margin", CONTRIBUTION_DEF, contribution_of, _CM_COMPS, org_id, date_range, filters, group_by, repo)


ORDERS_DEF = (
    "orders = count of realised orders: non-cancelled orders by IST order date, excluding status 'rto' (same basis as net_revenue)."
)
AOV_DEF = (
    "aov (average order value) = net_revenue / orders, in paise per order (half-up). Null when there are no realised orders."
)
ORDER_LEVEL_FILTERS = ORDER_FILTERS


def orders_of(c: dict[str, int]) -> int:
    return c.get("orders", 0)


def aov_of(c: dict[str, int]) -> int | None:
    return div_paise(net_of(c), orders_of(c))


def orders(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _run("orders", ORDERS_DEF, orders_of, {"orders"}, org_id, date_range, filters, group_by, repo,
                unit="count", groups_allowed=ORDER_GROUPS, filters_allowed=ORDER_LEVEL_FILTERS)


def aov(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _run("aov", AOV_DEF, aov_of, _NET_COMPS | {"orders"}, org_id, date_range, filters, group_by, repo,
                groups_allowed=ORDER_GROUPS, filters_allowed=ORDER_LEVEL_FILTERS)
