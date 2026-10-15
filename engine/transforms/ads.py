"""Ad connector raw rows -> ``ad_spend_daily`` rows (pure).

Reads exactly what connectors persist (connectors/meta.py, connectors/google_ads.py):

META (``meta_insights``; raw external_id ``<level>:<account_id>:<object_id>:<date_start>``): the verbatim insights row
  ``campaign_id, campaign_name, account_currency, date_start, date_stop, spend, impressions, clicks, actions[],
  action_values[]``. ``spend`` is a decimal string in ACCOUNT currency. Only ``campaign`` level rows are ingested:
  adset/ad/account rows would double count the same spend under the table key (org, platform, campaign_id, date)
  and are skipped with an issue. ``time_increment=1`` means date_start == date_stop; otherwise the row is skipped.
  Conversions = ``actions`` entry ``action_type == 'purchase'`` (ASSUMPTION: purchase action type name unverified);
  revenue = ``action_values`` ``purchase`` value. Absent -> NULL, never 0. These are platform-reported, not causal.

GOOGLE (``google_ads_row``; external_id ``<customer>:<campaign>:<date>``): verbatim search row
  ``campaign{id,name}, segments{date}, metrics{impressions,clicks,costMicros}`` with int64 values as strings.
  ``costMicros`` is converted with exact Decimal arithmetic (micros / 1_000_000 rupees -> paise, half-up).
  ASSUMPTIONS (unverified, see connectors/NOTES.md): costMicros is millionths of the account currency, and the account
  currency is INR (the connector does not fetch ``customer.currency_code``): every row carries
  ``currency_unverified`` and ``cost_micros_unit_assumed``. Conversions (``metrics.conversions`` /
  ``conversionsValue``) are only read if an operator added them via ``extra_metrics``; otherwise NULL.

Non-INR currency (Meta ``account_currency``) is REJECTED with an issue, never converted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.money import rupees_to_paise

from .common import TransformError

META_SOURCE = "meta_insights"
GOOGLE_SOURCE = "google_ads_row"


@dataclass
class AdBundle:
    org_id: str
    rows: list[dict] = field(default_factory=list)   # ad_spend_daily rows (without org_id)
    raw_ref: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def _dec(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
        raise TransformError(f"{name}: missing/invalid {value!r}")
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation as e:
        raise TransformError(f"{name}: invalid number {value!r}") from e
    if not d.is_finite():
        raise TransformError(f"{name}: not finite {value!r}")
    return d


def _count(value: Any, name: str) -> int:
    if value in (None, ""):
        return 0
    d = _dec(value, name)
    if d != d.to_integral_value() or d < 0:
        raise TransformError(f"{name}: expected non-negative integer, got {value!r}")
    return int(d)


def _day(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise TransformError(f"{name}: invalid date {value!r}") from e


def _purchase(items: Any, key: str = "value") -> Decimal | None:
    """Sum of ``purchase`` entries in a Meta actions/action_values list; None if there is none."""
    if not isinstance(items, list):
        return None
    vals = [_dec(i.get(key), "action value") for i in items if isinstance(i, dict) and i.get("action_type") == "purchase"]
    return sum(vals, Decimal(0)) if vals else None


def transform_meta_insights(org_id: str, payload: dict[str, Any], *, raw_external_id: str | None = None) -> AdBundle:
    if not isinstance(payload, dict):
        raise TransformError("meta insights payload is not an object")
    b = AdBundle(org_id, raw_ref={"source": META_SOURCE, "external_id": raw_external_id or ""})
    level = (raw_external_id or "campaign:").split(":", 1)[0]
    if level != "campaign" or not payload.get("campaign_id"):
        b.issues.append(f"meta_row_skipped: level {level!r} / no campaign_id (only campaign-level rows are ingested to avoid double counting)")
        return b
    cur = payload.get("account_currency")
    if cur != "INR":
        b.issues.append(f"non_inr_currency: meta account_currency {cur!r}; row rejected, not converted")
        return b
    ds, de = payload.get("date_start"), payload.get("date_stop") or payload.get("date_start")
    if ds != de:
        b.issues.append(f"meta_row_skipped: date_start {ds} != date_stop {de} (expected daily rows)")
        return b
    conv, rev = _purchase(payload.get("actions")), _purchase(payload.get("action_values"))
    b.rows.append({
        "platform": "meta", "campaign_id": str(payload["campaign_id"]), "campaign_name": payload.get("campaign_name"),
        "date": _day(ds, "date_start"), "spend_paise": rupees_to_paise(_dec(payload.get("spend"), "spend")),
        "impressions": _count(payload.get("impressions"), "impressions"), "clicks": _count(payload.get("clicks"), "clicks"),
        "platform_reported_conversions": conv,
        "platform_reported_revenue_paise": rupees_to_paise(rev) if rev is not None else None,
    })
    if rev is None:
        b.issues.append("platform_revenue_unavailable: no purchase action_values (stored NULL)")
    return b


def transform_google_ads_row(org_id: str, payload: dict[str, Any], *, raw_external_id: str | None = None) -> AdBundle:
    if not isinstance(payload, dict):
        raise TransformError("google ads payload is not an object")
    camp, seg, met = payload.get("campaign") or {}, payload.get("segments") or {}, payload.get("metrics") or {}
    if not camp.get("id") or not seg.get("date"):
        raise TransformError("google ads row missing campaign.id or segments.date")
    b = AdBundle(org_id, raw_ref={"source": GOOGLE_SOURCE, "external_id": raw_external_id or ""})
    micros = _dec(met.get("costMicros"), "metrics.costMicros")
    b.issues += ["currency_unverified: customer currency not fetched; assumed INR",
                 "cost_micros_unit_assumed: costMicros taken as millionths of the account currency"]
    conv = _dec(met["conversions"], "metrics.conversions") if met.get("conversions") not in (None, "") else None
    rev = _dec(met["conversionsValue"], "metrics.conversionsValue") if met.get("conversionsValue") not in (None, "") else None
    if rev is None:
        b.issues.append("platform_revenue_unavailable: conversionsValue not fetched (stored NULL)")
    b.rows.append({
        "platform": "google", "campaign_id": str(camp["id"]), "campaign_name": camp.get("name"),
        "date": _day(seg["date"], "segments.date"), "spend_paise": rupees_to_paise(micros / Decimal(1_000_000)),
        "impressions": _count(met.get("impressions"), "impressions"), "clicks": _count(met.get("clicks"), "clicks"),
        "platform_reported_conversions": conv,
        "platform_reported_revenue_paise": rupees_to_paise(rev) if rev is not None else None,
    })
    return b


def find_ad_spend_gaps(dates: set[date]) -> list[date]:
    """Days between the first and last day with data that have no row at all (reported only, never filled)."""
    if not dates:
        return []
    lo, hi = min(dates), max(dates)
    return [date.fromordinal(o) for o in range(lo.toordinal(), hi.toordinal() + 1) if date.fromordinal(o) not in dates]
