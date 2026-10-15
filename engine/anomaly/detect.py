"""Anomaly detection: robust STL residuals scored by median/MAD z (deviation from expected, not size)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import numpy as np

from intelligence.common import insufficient
from intelligence.sources import MetricSource, Series

from .stl import robust_stl

# Metrics where an upward move is bad (everything else: down is bad). Used for `favourable`.
HIGHER_IS_WORSE = {"expected_rto_loss", "rto", "blended_cac", "ad_spend"}
DEFAULT_METRICS = ("net_revenue", "orders", "aov", "contribution_margin", "mer", "blended_cac",
                   "expected_rto_loss", "ad_spend", "conversion")


@dataclass(frozen=True)
class AnomalyConfig:
    period: int = 7  # weekly seasonality for daily data
    threshold: float = 4.0  # |robust z| to report; calibrated on synthetic noise (see tests), configurable
    severity_bands: tuple[float, float] = (5.0, 7.0)  # >= high, >= critical
    min_cycles: int = 2  # <2 full cycles (14 days at period 7) -> INSUFFICIENT_DATA
    max_missing_frac: float = 0.2
    # Floor on the residual scale as a fraction of the median level: avoids hair-trigger z on near-noiseless data.
    min_rel_scale: float = 0.01
    # "auto": log-transform strictly positive series (multiplicative seasonality: weekend lift scales with level).
    # In-sample STL residuals understate true noise (the smoother absorbs part of it; ~15-25% at 60-180 days on
    # synthetic Gaussian noise), so the MAD scale is inflated by this empirical factor.
    scale_inflation: float = 1.2
    log_transform: str = "auto"  # auto | always | never


@dataclass(frozen=True)
class Anomaly:
    metric: str
    date: date
    observed: float
    expected: float
    residual: float
    robust_z: float
    severity: str  # medium | high | critical
    direction: str  # up | down
    favourable: bool

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["date"] = self.date.isoformat()
        return d


def _severity(z: float, cfg: AnomalyConfig) -> str:
    a = abs(z)
    return "critical" if a >= cfg.severity_bands[1] else "high" if a >= cfg.severity_bands[0] else "medium"


def detect_anomalies(series: Series, cfg: AnomalyConfig = AnomalyConfig()) -> dict[str, Any]:
    """Returns {status:'ok', metric, anomalies:[...], diagnostics} or the INSUFFICIENT_DATA shape."""
    if series.grain != "day" and cfg.period == 7:
        return insufficient(f"{series.metric}: weekly-grain series need period=52 and >=2 years; use daily series", "none")
    n = len(series.values)
    min_n = cfg.min_cycles * cfg.period
    if n < min_n:
        return insufficient(f"{series.metric}: {n} points < {min_n} (need {cfg.min_cycles} full cycles of {cfg.period})", "none")
    if len(set(series.dates)) != n:
        return insufficient(f"{series.metric}: duplicate dates in series", "none")
    order = np.argsort([d.toordinal() for d in series.dates])
    dates = [series.dates[i] for i in order]
    if (dates[-1] - dates[0]).days + 1 != n and series.grain == "day":
        return insufficient(f"{series.metric}: dates are not a contiguous daily range (gaps must be explicit None)", "none")
    y = np.array([np.nan if series.values[i] is None else float(series.values[i]) for i in order])
    miss = ~np.isfinite(y)
    if miss.mean() > cfg.max_missing_frac or (~miss).sum() < min_n:
        return insufficient(f"{series.metric}: {miss.mean():.0%} of points undefined (max {cfg.max_missing_frac:.0%})", "none")
    yi = y.copy()
    if miss.any():  # interpolate only to fit; missing days are never flagged
        yi[miss] = np.interp(np.flatnonzero(miss), np.flatnonzero(~miss), y[~miss])
    use_log = cfg.log_transform == "always" or (cfg.log_transform == "auto" and bool((yi > 0).all()))
    if use_log and not (yi > 0).all():
        return insufficient(f"{series.metric}: log transform requested but series has non-positive values", "none")
    work = np.log(yi) if use_log else yi
    trend, seasonal, resid = robust_stl(work, cfg.period)
    r = resid[~miss]
    med = np.median(r)
    mad = cfg.scale_inflation * 1.4826 * np.median(np.abs(r - med))
    floor = cfg.min_rel_scale * (1.0 if use_log else max(float(np.median(np.abs(yi))), 1e-9))
    scale = max(mad, floor)
    z = (resid - med) / scale
    out: list[Anomaly] = []
    for i in range(n):
        if miss[i] or abs(z[i]) < cfg.threshold:
            continue
        exp_i = float(trend[i] + seasonal[i] + med)
        exp_i = float(np.exp(exp_i)) if use_log else exp_i
        out.append(Anomaly(series.metric, dates[i], float(y[i]), exp_i,
                           float(y[i] - exp_i), float(z[i]), _severity(z[i], cfg),
                           "up" if z[i] > 0 else "down",
                           bool((z[i] > 0) != (series.metric in HIGHER_IS_WORSE))))
    out.sort(key=lambda a: -abs(a.robust_z))
    return {"status": "ok", "metric": series.metric, "anomalies": out,
            "diagnostics": {"method": "robust_stl(median cycle-subseries + robust LOESS) + median/MAD z",
                            "n": n, "log_transform": use_log, "period": cfg.period, "mad_scale": float(scale), "threshold": cfg.threshold,
                            "missing_points": int(miss.sum()), "data_issues": list(series.issues)}}


def detect_many(source: MetricSource, org_id: str, start: date, end: date,
                metrics: Iterable[str] = DEFAULT_METRICS, cfg: AnomalyConfig = AnomalyConfig(),
                skip_unavailable: bool = True) -> dict[str, dict[str, Any]]:
    """Run detection per metric. Metrics the source cannot provide (e.g. conversion without traffic data) are
    reported as INSUFFICIENT_DATA with a reason, not silently dropped."""
    res: dict[str, dict[str, Any]] = {}
    for m in metrics:
        try:
            s = source.series(m, org_id, start, end, "day")
        except (KeyError, LookupError) as e:
            if not skip_unavailable:
                raise
            res[m] = insufficient(f"{m}: not available from the metric source ({e})", "none")
            continue
        res[m] = detect_anomalies(s, cfg)
    return res
