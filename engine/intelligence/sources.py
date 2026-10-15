"""Metric-source interface for the intelligence layer.

Intelligence never computes revenue / profit / CAC / ROAS / margin itself. Series come from
  * `MetricLayerSource`: calls `metrics.METRICS[name].callable(...)` (the canonical implementations), or
  * `StaticSource`: an injected provider (tests, or ledger-level counts the metric layer does not expose yet).

Every series is ONE metric call with group_by=day|week (orders, aov, sessions, ad_spend, ad_impressions, ad_clicks,
platform_reported_* now exist in the metric layer). `extra` remains only as a hook for inputs that are genuinely absent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Protocol, Sequence


@dataclass
class Series:
    metric: str
    unit: str  # paise | ratio | count
    dates: list[date]
    values: list[float | None]  # None = undefined/missing (never silently zero)
    grain: str = "day"  # day | week
    calls: list[dict[str, Any]] = field(default_factory=list)  # provenance of the metric calls used
    issues: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.dates) != len(self.values):
            raise ValueError("dates and values must have the same length")


class MetricSource(Protocol):
    def series(self, metric: str, org_id: str, start: date, end: date, grain: str = "day") -> Series: ...


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v) if isinstance(v, Decimal) else float(v)


# Names used by intelligence -> canonical metric-layer names.
ALIASES = {"impressions": "ad_impressions", "clicks": "ad_clicks", "conversions": "platform_reported_conversions",
           "platform_revenue": "platform_reported_revenue"}
# Metrics for which a day/week with no orders genuinely means zero. Everything else (ratios, ad and traffic data)
# stays None when the metric layer has no row: a missing day is never silently zero.
ZERO_WHEN_NO_ORDERS = {"net_revenue", "contribution_margin", "expected_rto_loss", "orders"}


class MetricLayerSource:
    """Builds series from the canonical metric layer with ONE call per series (group_by=day|week).
    `extra` is a hook for genuinely absent inputs only (metric name -> callable(org_id, start, end, grain) -> Series).
    Special keys: `ad_spend:<platform>` (ad_spend filtered to one platform) and `conversion` (platform_reported_conversions
    / sessions, each from the metric layer; None where either is undefined)."""

    def __init__(self, repo: Any, extra: dict[str, Callable[..., Series]] | None = None):
        self.repo = repo
        self.extra = extra or {}

    def _one(self, name: str, org_id: str, start: date, end: date, grain: str, filters: dict | None = None) -> Series:
        from metrics import METRICS  # lazy: keeps this module importable without the metric layer

        canon = ALIASES.get(name, name)
        if canon not in METRICS:
            raise KeyError(f"unknown metric {name!r}; not in metrics.METRICS and not an injected extra")
        spec = METRICS[canon]
        if grain not in spec.allowed_group_by:
            raise ValueError(f"{canon} does not support group_by={grain}")
        res = spec.callable(org_id, (start, end), filters, grain, repo=self.repo)
        by = {g.key: _num(g.value) for g in res.groups}
        if grain == "day":
            dates = daterange(start, end)
        else:
            from metrics.periods import week_start
            dates = [week_start(start) + timedelta(weeks=i) for i in range((week_start(end) - week_start(start)).days // 7 + 1)]
        fill = 0.0 if canon in ZERO_WHEN_NO_ORDERS and res.data_quality.status != "insufficient" else None
        vals = [by.get(d.isoformat(), fill) for d in dates]
        return Series(name, res.unit, dates, vals, grain, [res.to_dict()], list(res.data_quality.issues))

    def series(self, metric: str, org_id: str, start: date, end: date, grain: str = "day") -> Series:
        if metric in self.extra:
            return self.extra[metric](org_id, start, end, grain)
        if grain not in ("day", "week"):
            raise ValueError(f"unsupported grain {grain!r}; use 'day' or 'week'")
        if metric.startswith("ad_spend:"):
            s = self._one("ad_spend", org_id, start, end, grain, {"platform": [metric.split(":", 1)[1]]})
            s.metric = metric
            return s
        if metric == "conversion":
            c = self._one("platform_reported_conversions", org_id, start, end, grain)
            t = self._one("sessions", org_id, start, end, grain)
            vals = [None if (a is None or b is None or b <= 0) else a / b for a, b in zip(c.values, t.values)]
            return Series("conversion", "ratio", c.dates, vals, grain, c.calls + t.calls, sorted(set(c.issues + t.issues)))
        return self._one(metric, org_id, start, end, grain)


class StaticSource:
    """Injected series (tests, or pre-fetched ledger counts)."""

    def __init__(self, series: dict[str, Series]):
        self._s = series

    def series(self, metric: str, org_id: str, start: date, end: date, grain: str = "day") -> Series:
        s = self._s[metric]
        keep = [i for i, d in enumerate(s.dates) if start <= d <= end]
        return Series(s.metric, s.unit, [s.dates[i] for i in keep], [s.values[i] for i in keep], s.grain, s.calls, s.issues)


def series_from_values(metric: str, start: date, values: Sequence[float | None], unit: str = "paise") -> Series:
    return Series(metric, unit, daterange(start, start + timedelta(days=len(values) - 1)), list(values))
