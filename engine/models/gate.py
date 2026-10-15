"""MMM sufficiency gate. Weak data yields {status: INSUFFICIENT_DATA, reason, fallback: 'descriptive'} - no curves,
no intervals, ever. Thresholds are explicit and configurable; the defaults below are judgement calls (documented in
DECISIONS.md), not statistical guarantees.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

import numpy as np

from intelligence.common import insufficient


@dataclass(frozen=True)
class GateConfig:
    min_weeks: int = 52  # CLAUDE.md: MMM needs >= 52 weeks
    min_spend_cv: float = 0.15  # per channel: std/mean of weekly spend (variation needed to identify a response)
    max_zero_spend_week_frac: float = 0.50  # per channel: weeks with zero spend
    min_nonzero_weeks: int = 26  # per channel
    max_missing_frac: float = 0.05  # missing outcome/spend weeks (gaps or None)
    max_channel_corr: float = 0.90  # |corr| between two channels' weekly spend: above this they cannot be separated
    min_weeks_per_channel: int = 10
    min_outcome_cv: float = 0.02
    # post-fit diagnostics
    max_r_hat: float = 1.05
    min_ess_bulk: float = 200.0
    max_divergence_frac: float = 0.01
    ppc_coverage_range: tuple[float, float] = (0.6, 0.95)  # share of observed weeks inside the 80% posterior predictive interval


@dataclass
class MMMData:
    """Weekly (Monday-start IST) outcome and per-channel spend, integer paise. None = missing."""
    weeks: list[date]
    outcome_paise: list[int | float | None]
    spend_paise: dict[str, list[int | float | None]]
    outcome_metric: str = "net_revenue"
    calls: list[dict[str, Any]] = field(default_factory=list)  # metric-layer provenance


def load_mmm_data(source: Any, org_id: str, start: date, end: date, channels: Sequence[str],
                  outcome_metric: str = "net_revenue", spend_key: str = "ad_spend:{channel}") -> MMMData:
    """Weekly outcome from the metric layer (group_by=week); weekly ad spend per platform/channel from a ledger-backed
    provider named e.g. `ad_spend:meta` (no canonical per-platform spend metric exists yet; see PROGRESS)."""
    o = source.series(outcome_metric, org_id, start, end, "week")
    weeks = list(o.dates)
    spend: dict[str, list] = {}
    calls = list(o.calls)
    for ch in channels:
        s = source.series(spend_key.format(channel=ch), org_id, start, end, "week")
        m = dict(zip(s.dates, s.values))
        spend[ch] = [m.get(w) for w in weeks]
        calls += s.calls
    return MMMData(weeks, list(o.values), spend, outcome_metric, calls)


def _check_weeks(weeks: Sequence[date]) -> str | None:
    if len(set(weeks)) != len(weeks):
        return "duplicate weeks"
    ws = sorted(weeks)
    if any(w.weekday() != 0 for w in ws):
        return "week keys must be Monday-start"
    return None


def _full_weeks(data: MMMData) -> tuple[list[date], dict[str, np.ndarray], np.ndarray, int]:
    """Reindex onto a contiguous weekly calendar; absent weeks become NaN. Returns (weeks, spend, outcome, n_absent)."""
    ws = sorted(data.weeks)
    full = [ws[0] + timedelta(weeks=i) for i in range(((ws[-1] - ws[0]).days // 7) + 1)]
    idx = {w: i for i, w in enumerate(data.weeks)}

    def col(vals):
        return np.array([np.nan if (w not in idx or vals[idx[w]] is None) else float(vals[idx[w]]) for w in full])

    return full, {c: col(v) for c, v in data.spend_paise.items()}, col(data.outcome_paise), len(full) - len(ws)


def check_sufficiency(data: MMMData, cfg: GateConfig = GateConfig(), exclude_failing_channels: bool = False) -> dict[str, Any]:
    """Return {status:'ok', channels:[...], weeks, details} or the INSUFFICIENT_DATA dict (with per-check details)."""
    if not data.weeks or not data.spend_paise:
        return insufficient("no data: empty weekly series or no channels")
    for vals in (data.outcome_paise, *data.spend_paise.values()):
        if len(vals) != len(data.weeks):
            return insufficient("series lengths differ from the weeks index")
    bad = _check_weeks(data.weeks)
    if bad:
        return insufficient(bad)
    weeks, spend, y, n_absent = _full_weeks(data)
    n = len(weeks)
    details: dict[str, Any] = {"n_weeks": n}
    if n < cfg.min_weeks:
        return insufficient(f"{n} weeks < {cfg.min_weeks}", details=details)
    miss_frac = float(np.isnan(y).mean())
    for c, s in spend.items():
        miss_frac = max(miss_frac, float(np.isnan(s).mean()))
    details["max_missing_frac"] = miss_frac
    if miss_frac > cfg.max_missing_frac:
        return insufficient(f"{miss_frac:.0%} of weekly points missing > {cfg.max_missing_frac:.0%}", details=details)
    ycv = float(np.nanstd(y) / abs(np.nanmean(y))) if np.nanmean(y) else 0.0
    details["outcome_cv"] = ycv
    if not ycv >= cfg.min_outcome_cv:
        return insufficient(f"outcome is flat (CV {ycv:.3f} < {cfg.min_outcome_cv})", details=details)
    failing: dict[str, str] = {}
    per: dict[str, Any] = {}
    for c, s in spend.items():
        ok = s[~np.isnan(s)]
        nz = int((ok > 0).sum())
        zero_frac = float((ok <= 0).mean()) if len(ok) else 1.0
        cv = float(ok.std() / ok.mean()) if len(ok) and ok.mean() > 0 else 0.0
        per[c] = {"spend_cv": cv, "zero_spend_week_frac": zero_frac, "nonzero_weeks": nz}
        if zero_frac > cfg.max_zero_spend_week_frac:
            failing[c] = f"{zero_frac:.0%} zero-spend weeks > {cfg.max_zero_spend_week_frac:.0%}"
        elif nz < cfg.min_nonzero_weeks:
            failing[c] = f"{nz} weeks with spend < {cfg.min_nonzero_weeks}"
        elif cv < cfg.min_spend_cv:
            failing[c] = f"weekly spend CV {cv:.2f} < {cfg.min_spend_cv} (too little variation to identify a response)"
    details["channels"] = per
    keep = [c for c in spend if c not in failing]
    if failing and not (exclude_failing_channels and keep):
        why = "; ".join(f"{c}: {r}" for c, r in failing.items())
        return insufficient(why, details=details, failing_channels=failing)
    if n < cfg.min_weeks_per_channel * len(keep):
        return insufficient(f"{n} weeks < {cfg.min_weeks_per_channel} per channel x {len(keep)} channels", details=details)
    if len(keep) > 1:
        mat = np.vstack([np.where(np.isnan(spend[c]), np.nanmean(spend[c]), spend[c]) for c in keep])
        cm = np.corrcoef(mat)
        for i in range(len(keep)):
            for j in range(i + 1, len(keep)):
                if abs(cm[i, j]) > cfg.max_channel_corr:
                    return insufficient(f"channels {keep[i]} and {keep[j]} spend are collinear (|r|={abs(cm[i, j]):.2f} > "
                                        f"{cfg.max_channel_corr}); their effects cannot be separated", details=details)
    return {"status": "ok", "channels": keep, "excluded_channels": failing, "weeks": weeks, "details": details}


def check_diagnostics(diag: Mapping[str, Any], cfg: GateConfig = GateConfig()) -> dict[str, Any] | None:
    """None if the fit is acceptable, else an INSUFFICIENT_DATA dict carrying the diagnostics."""
    problems = []
    if not diag.get("r_hat_max", np.inf) <= cfg.max_r_hat:
        problems.append(f"R-hat max {diag.get('r_hat_max')} > {cfg.max_r_hat}")
    if not diag.get("ess_bulk_min", 0) >= cfg.min_ess_bulk:
        problems.append(f"bulk ESS min {diag.get('ess_bulk_min')} < {cfg.min_ess_bulk}")
    if not diag.get("divergence_frac", 1.0) <= cfg.max_divergence_frac:
        problems.append(f"divergences {diag.get('divergence_frac')} of draws > {cfg.max_divergence_frac}")
    lo, hi = cfg.ppc_coverage_range
    cov = diag.get("ppc_coverage_80")
    if cov is None or not (lo <= cov <= hi):
        problems.append(f"posterior-predictive 80% coverage {cov} outside [{lo}, {hi}]")
    if not problems:
        return None
    return insufficient("failed diagnostics: " + "; ".join(problems), diagnostics=dict(diag))


def descriptive_fallback(data: MMMData) -> dict[str, Any]:
    """Spend vs outcome summary. Association only - labelled non-causal, no curves or intervals."""
    weeks, spend, y, _ = _full_weeks(data) if data.weeks else ([], {}, np.array([]), 0)
    out: dict[str, Any] = {"kind": "descriptive", "causal": False,
                           "note": "Association between spend and outcome only; not a causal estimate and no response curve.",
                           "outcome_metric": data.outcome_metric, "n_weeks": len(weeks), "channels": {}}
    if not weeks:
        return out
    tot = sum(float(np.nansum(s)) for s in spend.values())
    half = len(weeks) // 2
    for c, s in spend.items():
        both = ~np.isnan(s) & ~np.isnan(y)
        r = None
        if both.sum() >= 8 and np.std(s[both]) > 0 and np.std(y[both]) > 0:
            r = float(np.corrcoef(s[both], y[both])[0, 1])
        out["channels"][c] = {
            "total_spend_paise": int(np.nansum(s)),
            "share_of_spend": float(np.nansum(s) / tot) if tot else None,
            "mean_weekly_spend_paise": int(np.nanmean(s)) if (~np.isnan(s)).any() else None,
            "weekly_correlation_with_outcome": r,
            "spend_change_recent_half_pct": _pct(np.nanmean(s[:half]), np.nanmean(s[half:])) if half >= 4 else None,
        }
    out["total_outcome_paise"] = int(np.nansum(y))
    out["outcome_change_recent_half_pct"] = _pct(np.nanmean(y[:half]), np.nanmean(y[half:])) if half >= 4 else None
    return out


def _pct(a: float, b: float) -> float | None:
    return None if not a or np.isnan(a) or np.isnan(b) else float(b / a - 1)
