"""Registry so the API (main) and Claude tools (AI agent) wrap the same functions without redefining them."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import finance, marketing, marketing_spend, rto
from .core import GROUP_KEYS, ITEM_FILTERS, ORDER_FILTERS, ORDER_GROUPS
from .types import MetricResult

COMMON_PARAMS = {
    "org_id": "uuid string (tenant)",
    "date_range": "inclusive IST calendar dates: DateRange(start, end) or (start, end)",
    "filters": "optional {name: value | [values]} using the metric's allowed_filters",
    "group_by": "optional single grouping key from the metric's allowed_group_by",
    "repo": "MetricsRepository (keyword-only; injected by the caller, never by a user or Claude)",
}


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    params: dict[str, str]
    callable: Callable[..., MetricResult]
    allowed_group_by: tuple[str, ...]
    allowed_filters: tuple[str, ...]
    unit: str


def _spec(fn, name, desc, unit, groups, filters, extra=None) -> MetricSpec:
    return MetricSpec(name, desc, {**COMMON_PARAMS, **(extra or {})}, fn, tuple(groups), tuple(filters), unit)


_LEDGER_FILTERS = ORDER_FILTERS + ITEM_FILTERS
METRICS: dict[str, MetricSpec] = {
    s.name: s
    for s in (
        _spec(finance.net_revenue, "net_revenue", finance.NET_REVENUE_DEF, "paise", GROUP_KEYS, _LEDGER_FILTERS),
        _spec(finance.contribution_margin, "contribution_margin", finance.CONTRIBUTION_DEF, "paise", GROUP_KEYS, _LEDGER_FILTERS),
        _spec(rto.expected_rto_loss, "expected_rto_loss", rto.DEFINITION, "paise", GROUP_KEYS, _LEDGER_FILTERS,
              {"min_history": "optional int, min resolved orders per RTO segment", "lookback_days": "optional int"}),
        _spec(finance.orders, "orders", finance.ORDERS_DEF, "count", ORDER_GROUPS, ORDER_FILTERS),
        _spec(finance.aov, "aov", finance.AOV_DEF, "paise", ORDER_GROUPS, ORDER_FILTERS),
        _spec(marketing.blended_cac, "blended_cac", marketing.BLENDED_CAC_DEF, "paise", ("day", "week"), ORDER_FILTERS + marketing.AD_FILTERS),
        _spec(marketing.mer, "mer", marketing.MER_DEF, "ratio", ("day", "week"), _LEDGER_FILTERS + marketing.AD_FILTERS),
        _spec(marketing.platform_roas, "platform_roas", marketing.ROAS_DEF, "ratio", marketing_spend.AD_GROUPS, marketing.AD_FILTERS),
        _spec(marketing_spend.ad_spend, "ad_spend", marketing.AD_SPEND_DEF, "paise", marketing_spend.AD_GROUPS, marketing.AD_FILTERS),
        _spec(marketing_spend.platform_reported_revenue, "platform_reported_revenue", marketing.PLATFORM_REVENUE_DEF, "paise",
              marketing_spend.AD_GROUPS, marketing.AD_FILTERS),
        _spec(marketing_spend.ad_impressions, "ad_impressions", marketing_spend.IMPRESSIONS_DEF, "count", marketing_spend.AD_GROUPS, marketing.AD_FILTERS),
        _spec(marketing_spend.ad_clicks, "ad_clicks", marketing_spend.CLICKS_DEF, "count", marketing_spend.AD_GROUPS, marketing.AD_FILTERS),
        _spec(marketing_spend.platform_reported_conversions, "platform_reported_conversions", marketing_spend.CONVERSIONS_DEF, "count",
              marketing_spend.AD_GROUPS, marketing.AD_FILTERS),
        _spec(marketing_spend.sessions, "sessions", marketing_spend.SESSIONS_DEF, "count", ("day", "week", "channel"), ("channel",)),
        _spec(marketing.contribution_after_marketing, "contribution_after_marketing", marketing.CAM_DEF, "paise", ("day", "week"),
              _LEDGER_FILTERS + marketing.AD_FILTERS),
    )
}


def run_metric(name: str, org_id, date_range, filters=None, group_by=None, *, repo, **extra) -> MetricResult:
    """Dispatch by name; unknown names raise KeyError."""
    return METRICS[name].callable(org_id, date_range, filters, group_by, repo=repo, **extra)
