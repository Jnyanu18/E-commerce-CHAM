"""Plain totals over ad_spend_daily (and traffic_daily), built on marketing.load_spend (the single spend implementation).

ad_spend, platform_reported_revenue, ad_impressions, ad_clicks, platform_reported_conversions share ONE helper
(`_ad_sum`); sessions uses the same grouping helper for traffic rows.
"""
from __future__ import annotations

from typing import Callable

from .marketing import AD_FILTERS, AD_SPEND_DEF, NO_SPEND, PLATFORM_REVENUE_DEF, _Spend, _prep, load_spend, spend_group_key
from .repository import MetricsRepository
from .result import make_result
from .types import DateRange, Filters, MetricResult

AD_GROUPS = ("day", "week", "platform")
IMPRESSIONS_DEF = "ad_impressions = sum of ad_spend_daily.impressions (meta + google, all campaigns) for IST dates in the period."
CLICKS_DEF = "ad_clicks = sum of ad_spend_daily.clicks (meta + google, all campaigns) for IST dates in the period."
CONVERSIONS_DEF = (
    "platform_reported_conversions = sum of ad_spend_daily.platform_reported_conversions. platform-reported, not causal: "
    "each ad platform's own attribution (can be fractional). Rows with null conversions are excluded and flagged."
)
SESSIONS_DEF = (
    "sessions = sum of traffic_daily.sessions for IST dates in the period (all channels unless filtered). "
    "Null when traffic_daily has no rows for the period: there is no sessions connector yet."
)
NO_TRAFFIC = "no_traffic_data_in_period: traffic_daily has no rows (no sessions connector yet)"


def _ad_sum(
    name: str, definition: str, unit: str, total_of: Callable[[_Spend], object], rows_of: Callable[[_Spend], int] | None,
    missing_label: str, org_id, date_range, filters, group_by, repo: MetricsRepository,
) -> MetricResult:
    """total_of: field to sum; rows_of: rows that report it (None = every row always reports it)."""
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, AD_GROUPS, AD_FILTERS)
    issues: list[str] = []
    spend, groups = load_spend(repo, org_id, dr, nf, group_by, issues)
    reporting = (lambda b: b.rows) if rows_of is None else rows_of
    if spend.rows == 0:
        issues.append(NO_SPEND)
    elif reporting(spend) == 0:
        issues.append(f"{missing_label}_not_reported_in_period")
    elif reporting(spend) < spend.rows:
        issues.append(f"{missing_label}_missing_for_rows: {spend.rows - reporting(spend)} of {spend.rows} (excluded)")
    return make_result(
        metric=name, value=total_of(spend) if reporting(spend) else None, unit=unit, dr=dr, filters=filters, group_by=group_by,
        group_values={k: (total_of(v) if reporting(v) else None) for k, v in groups.items()},
        definition=definition, issues=issues, row_count=reporting(spend),
    )


def ad_spend(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _ad_sum("ad_spend", AD_SPEND_DEF, "paise", lambda b: b.spend, None, "ad_spend", org_id, date_range, filters, group_by, repo)


def platform_reported_revenue(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _ad_sum("platform_reported_revenue", PLATFORM_REVENUE_DEF, "paise", lambda b: b.rev, lambda b: b.rev_rows,
                   "platform_revenue", org_id, date_range, filters, group_by, repo)


def ad_impressions(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _ad_sum("ad_impressions", IMPRESSIONS_DEF, "count", lambda b: b.impressions, None, "impressions", org_id, date_range, filters, group_by, repo)


def ad_clicks(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _ad_sum("ad_clicks", CLICKS_DEF, "count", lambda b: b.clicks, None, "clicks", org_id, date_range, filters, group_by, repo)


def platform_reported_conversions(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    return _ad_sum("platform_reported_conversions", CONVERSIONS_DEF, "count", lambda b: b.conv, lambda b: b.conv_rows,
                   "platform_conversions", org_id, date_range, filters, group_by, repo)


def sessions(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("day", "week", "channel"), ("channel",))
    wanted = nf.get("channel")
    total, groups, dates, rows = 0, {}, set(), 0
    for r in repo.traffic(org_id, dr.start, dr.end):
        if wanted and r.channel not in wanted:
            continue
        rows += 1
        dates.add(r.date)
        total += r.sessions
        if group_by:
            key = spend_group_key(r.date, r.channel, group_by)
            groups[key] = groups.get(key, 0) + r.sessions
    issues: list[str] = []
    if rows == 0:
        issues.append(NO_TRAFFIC)
    elif len(dates) < dr.days:
        issues.append(f"traffic_missing_days: {dr.days - len(dates)} of {dr.days}")
    return make_result(metric="sessions", value=total if rows else None, unit="count", dr=dr, filters=filters, group_by=group_by,
                       group_values=groups, definition=SESSIONS_DEF, issues=issues, row_count=rows)
