"""Metric-source interface for the intelligence layer.

Intelligence never computes revenue / profit / CAC / ROAS / margin itself. Series come from
  * `MetricLayerSource`: calls `metrics.METRICS[name].callable(...)` (the canonical implementations), or
  * `StaticSource`: an injected provider (tests, or ledger-level counts the metric layer does not expose yet).

Metric-layer coverage gaps (documented in PROGRESS.md > Integration Needed): there is no `group_by=day`, so daily
series are built by one metric call per day; order count, AOV, ad spend, sessions, impressions, clicks and
conversions have no canonical metric yet and must be supplied through `extra` providers that read ledger tables
(`orders`, `ad_spend_daily`, `traffic_daily`). Those providers return raw counts / paise only - ratios (AOV, CVR)
are derived in `rootcause` from those counts exactly as the decomposition identity requires.
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


class MetricLayerSource:
    """Builds series from the canonical metric layer. `extra` maps metric name -> callable(org_id, start, end, grain)
    returning a Series for inputs that are not (yet) canonical metrics."""

    def __init__(self, repo: Any, extra: dict[str, Callable[..., Series]] | None = None):
        self.repo = repo
        self.extra = extra or {}

    def series(self, metric: str, org_id: str, start: date, end: date, grain: str = "day") -> Series:
        if metric in self.extra:
            return self.extra[metric](org_id, start, end, grain)
        from metrics import METRICS  # lazy: keeps this module importable without the metric layer

        if metric not in METRICS:
            raise KeyError(f"unknown metric {metric!r}; not in metrics.METRICS and not an injected extra")
        spec = METRICS[metric]
        calls: list[dict[str, Any]] = []
        issues: list[str] = []
        if grain == "week":
            if "week" not in spec.allowed_group_by:
                raise ValueError(f"{metric} does not support group_by=week")
            res = spec.callable(org_id, (start, end), None, "week", repo=self.repo)
            by_week = {g.key: _num(g.value) for g in res.groups}
            weeks = sorted(by_week)
            dates = [date.fromisoformat(w) for w in weeks]
            vals = [by_week[w] for w in weeks]
            calls.append(res.to_dict())
            issues += res.data_quality.issues
            return Series(metric, res.unit, dates, vals, "week", calls, issues)
        dates = daterange(start, end)
        vals: list[float | None] = []
        unit = spec.unit
        for d in dates:
            res = spec.callable(org_id, (d, d), None, None, repo=self.repo)
            unit = res.unit
            vals.append(_num(res.value))
            if res.data_quality.status != "ok":
                issues += [f"{d}: {i}" for i in res.data_quality.issues]
        calls.append({"metric": metric, "per_day_calls": len(dates), "period": {"start": start.isoformat(), "end": end.isoformat()}})
        return Series(metric, unit, dates, vals, "day", calls, sorted(set(issues)))


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
