"""Plain ad-spend / platform-reported-revenue totals, built on marketing.load_spend (the single spend implementation)."""
from __future__ import annotations

from .marketing import AD_FILTERS, AD_SPEND_DEF, NO_SPEND, PLATFORM_REVENUE_DEF, _prep, load_spend
from .repository import MetricsRepository
from .result import make_result
from .types import DateRange, Filters, MetricResult


def ad_spend(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("week", "platform"), AD_FILTERS)
    issues: list[str] = []
    spend, groups = load_spend(repo, org_id, dr, nf, group_by, issues)
    if spend.rows == 0:
        issues.append(NO_SPEND)
    return make_result(
        metric="ad_spend", value=spend.spend if spend.rows else None, unit="paise", dr=dr, filters=filters, group_by=group_by,
        group_values={k: v.spend for k, v in groups.items()}, definition=AD_SPEND_DEF, issues=issues, row_count=spend.rows,
    )


def platform_reported_revenue(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("week", "platform"), AD_FILTERS)
    issues: list[str] = []
    spend, groups = load_spend(repo, org_id, dr, nf, group_by, issues)
    if spend.rows == 0:
        issues.append(NO_SPEND)
    elif spend.rev_rows == 0:
        issues.append("platform_revenue_not_reported_in_period")
    elif spend.rev_rows < spend.rows:
        issues.append(f"platform_revenue_missing_for_rows: {spend.rows - spend.rev_rows} of {spend.rows} (excluded)")
    return make_result(
        metric="platform_reported_revenue", value=spend.rev if spend.rev_rows else None, unit="paise", dr=dr, filters=filters,
        group_by=group_by, group_values={k: (v.rev if v.rev_rows else None) for k, v in groups.items()},
        definition=PLATFORM_REVENUE_DEF, issues=issues, row_count=spend.rev_rows,
    )
