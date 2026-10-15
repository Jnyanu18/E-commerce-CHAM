"""Ad-spend based metrics: blended_cac, mer, platform_roas, contribution_after_marketing.

Ad spend is stored per platform/campaign/day and cannot be attributed to an order channel, SKU or
city, so these metrics only support group_by in (None, 'week') (platform_roas also 'platform').
"""
from __future__ import annotations

from collections import defaultdict

from .core import ORDER_FILTERS, aggregate_ledger, load_orders, order_field, validate_request
from .finance import ALLOWED_FILTERS as LEDGER_FILTERS, contribution_of, net_of
from .periods import week_start
from .repository import MetricsRepository
from .result import NO_ORDERS, div_paise, make_result, ratio
from .types import DateRange, Filters, MetricError, MetricResult, normalise_filters

AD_FILTERS = ("platform",)
NO_SPEND = "ad_spend_missing_no_rows_in_period"

BLENDED_CAC_DEF = (
    "blended_cac = total ad spend (meta + google, all campaigns) / new customers, in paise per customer. A new customer is a "
    "customer_key whose first non-cancelled order in the whole ledger (earliest ordered_at, any channel) falls inside the "
    "period. Orders without customer_key are not counted as new customers. Filters restrict which first orders count; "
    "ad spend is only filterable by platform."
)
MER_DEF = "mer (marketing efficiency ratio) = net_revenue / total ad spend (meta + google). Orders are the source of truth; not attribution."
ROAS_DEF = (
    "platform_roas = platform-reported conversion revenue / ad spend, over ad rows that report revenue. "
    "platform-reported, not causal: it reflects each ad platform's own attribution and must not be used as incremental return."
)
CAM_DEF = "contribution_after_marketing = contribution_margin - total ad spend (meta + google) for the same period."


class _Spend:
    def __init__(self) -> None:
        self.spend = 0
        self.rev = 0
        self.rev_spend = 0  # spend of rows that report revenue
        self.rows = 0


def _spend_week_or_platform(row, group_by):
    return week_start(row.date).isoformat() if group_by == "week" else row.platform


def load_spend(repo: MetricsRepository, org_id: str, dr: DateRange, nf, group_by, issues: list[str]) -> tuple[_Spend, dict[str, _Spend]]:
    platforms = nf.get("platform")
    seen: set = set()
    total, groups = _Spend(), {}
    dates = set()
    dup = 0
    for r in repo.ad_spend(org_id, dr.start, dr.end):
        if platforms and r.platform not in platforms:
            continue
        k = (r.platform, r.campaign_id, r.date)
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        dates.add(r.date)
        buckets = [total]
        if group_by:
            buckets.append(groups.setdefault(_spend_week_or_platform(r, group_by), _Spend()))
        for b in buckets:
            b.spend += r.spend_paise
            b.rows += 1
            if r.platform_reported_revenue_paise is not None:
                b.rev += r.platform_reported_revenue_paise
                b.rev_spend += r.spend_paise
    if dup:
        issues.append(f"duplicate_ad_spend_rows_ignored: {dup}")
    if total.rows:
        missing = dr.days - len(dates)
        if missing:
            issues.append(f"ad_spend_missing_days: {missing} of {dr.days}")
    return total, groups


def _prep(group_by, filters, allowed_groups, allowed_filters):
    nf = normalise_filters(filters)
    validate_request(group_by, nf, allowed_groups, allowed_filters)
    return nf


def _ledger_filters(nf):
    return {k: v for k, v in nf.items() if k != "platform"}


def _note_unattributable(nf, issues):
    if any(k in nf for k in ORDER_FILTERS) or "sku" in nf or "category" in nf:
        issues.append("ad_spend_not_filtered_by_order_filters: spend covers all channels/segments")


def mer(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("week",), LEDGER_FILTERS + AD_FILTERS)
    agg = aggregate_ledger(repo, org_id, dr, _ledger_filters(nf), group_by, {"gross", "discounts", "taxes", "refunds"})
    issues = list(agg.issues)
    spend, sgroups = load_spend(repo, org_id, dr, nf, group_by, issues)
    _note_unattributable(nf, issues)
    if agg.row_count == 0:
        issues.append(NO_ORDERS)
    if spend.rows == 0:
        issues.append(NO_SPEND)
    value = ratio(net_of(agg.totals), spend.spend) if agg.row_count else None
    keys = set(agg.groups) | set(sgroups)
    gv = {k: ratio(net_of(agg.groups.get(k, {})), sgroups[k].spend) if k in sgroups else None for k in keys}
    return make_result(metric="mer", value=value, unit="ratio", dr=dr, filters=filters, group_by=group_by, group_values=gv,
                       definition=MER_DEF, issues=issues, row_count=agg.row_count + spend.rows)


def platform_roas(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("week", "platform"), AD_FILTERS)
    issues: list[str] = []
    spend, groups = load_spend(repo, org_id, dr, nf, group_by, issues)
    if spend.rows == 0:
        issues.append(NO_SPEND)
    elif spend.rev_spend < spend.spend:
        issues.append(f"platform_revenue_missing_for_spend_paise: {spend.spend - spend.rev_spend} (excluded from ratio)")
    return make_result(
        metric="platform_roas", value=ratio(spend.rev, spend.rev_spend), unit="ratio", dr=dr, filters=filters,
        group_by=group_by, group_values={k: ratio(v.rev, v.rev_spend) for k, v in groups.items()},
        definition=ROAS_DEF, issues=issues, row_count=spend.rows,
    )


def blended_cac(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("week",), ORDER_FILTERS + AD_FILTERS)
    of = _ledger_filters(nf)
    issues: list[str] = []
    period = load_orders(repo, org_id, dr, {}, issues)  # unfiltered: first order must be judged against all orders
    keyed = [o for o in period if o.customer_key]
    if len(keyed) < len(period):
        issues.append(f"orders_without_customer_key_not_counted: {len(period) - len(keyed)}")
    first: dict = {}
    for o in repo.orders_for_customers(org_id, {o.customer_key for o in keyed}, dr.end):
        if o.status == "cancelled":
            continue
        cur = first.get(o.customer_key)
        if cur is None or (o.ordered_at, o.id) < (cur.ordered_at, cur.id):
            first[o.customer_key] = o
    new_total = 0
    new_groups: dict[str, int] = defaultdict(int)
    for o in first.values():
        if not (dr.start <= o.ordered_date_ist <= dr.end):
            continue
        if any(order_field(o, k) not in of[k] for k in ORDER_FILTERS if k in of):
            continue
        new_total += 1
        if group_by:
            new_groups[order_field(o, group_by)] += 1
    spend, sgroups = load_spend(repo, org_id, dr, nf, group_by, issues)
    _note_unattributable(nf, issues)
    if spend.rows == 0:
        issues.append(NO_SPEND)
    if new_total == 0:
        issues.append("no_new_customers_in_period")
    value = div_paise(spend.spend, new_total) if spend.rows else None
    keys = set(new_groups) | set(sgroups)
    gv = {k: div_paise(sgroups[k].spend, new_groups.get(k, 0)) if k in sgroups else None for k in keys}
    return make_result(metric="blended_cac", value=value, unit="paise", dr=dr, filters=filters, group_by=group_by,
                       group_values=gv, definition=BLENDED_CAC_DEF, issues=issues, row_count=new_total + spend.rows)


def contribution_after_marketing(org_id: str, date_range, filters: Filters | None = None, group_by: str | None = None, *, repo: MetricsRepository) -> MetricResult:
    dr = DateRange.of(date_range)
    nf = _prep(group_by, filters, ("week",), LEDGER_FILTERS + AD_FILTERS)
    comps = {"gross", "discounts", "taxes", "refunds", "cogs", "fees", "shipping", "returns"}
    agg = aggregate_ledger(repo, org_id, dr, _ledger_filters(nf), group_by, comps)
    issues = list(agg.issues)
    spend, sgroups = load_spend(repo, org_id, dr, nf, group_by, issues)
    _note_unattributable(nf, issues)
    if agg.row_count == 0:
        issues.append(NO_ORDERS)
    if spend.rows == 0:
        issues.append(NO_SPEND)
    ok = agg.row_count > 0 and spend.rows > 0
    value = contribution_of(agg.totals) - spend.spend if ok else None
    gv = {}
    for k in set(agg.groups) | set(sgroups):
        gv[k] = contribution_of(agg.groups[k]) - sgroups[k].spend if k in agg.groups and k in sgroups else None
    return make_result(metric="contribution_after_marketing", value=value, unit="paise", dr=dr, filters=filters,
                       group_by=group_by, group_values=gv, definition=CAM_DEF, issues=issues, row_count=agg.row_count + spend.rows)
