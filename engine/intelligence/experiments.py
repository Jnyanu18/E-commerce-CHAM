"""Geo-holdout experiment designer + difference-in-differences measurement.

Design: ads for one channel are PAUSED ("held out") in matched `treatment_cities` for `test_weeks`, while matched
`control_cities` continue as usual. Effect = how much total sales in the held-out cities fall relative to their matched
controls. All sales come from the metric layer (weekly net_revenue by city); nothing here defines revenue.

Assumptions stated in every design (see `assumptions`): weekly noise of log(treatment/control) is i.i.d. normal around a
constant level in the pre-period (parallel trends in logs); no spillover between cities; the pause does not change
organic behaviour elsewhere. The 80% interval matches the contract; MDE uses 80% power at 5% two-sided significance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np

from .common import insufficient

_N = NormalDist()
Z80 = _N.inv_cdf(0.90)


@dataclass(frozen=True)
class DesignConfig:
    min_pre_weeks: int = 12
    test_weeks: int = 4
    min_match_corr: float = 0.5  # detrended-log correlation required for a city to be matchable
    level_penalty: float = 0.5  # score = corr - level_penalty * |ln(level ratio)|
    max_treatment_share: float = 0.15  # held-out cities' max share of total sales
    max_treatment_cities: int = 3
    max_missing_frac: float = 0.10
    max_zero_week_frac: float = 0.20
    assumed_channel_share: float = 0.30  # ASSUMPTION: share of held-out cities' sales the channel drives (for expected risk)
    worst_case_channel_share: float = 0.60  # ASSUMPTION: upper bound for the risk statement


@dataclass
class CitySales:
    """Weekly (Monday-start IST) sales per city in integer paise; None = missing."""
    weeks: list[date]
    sales: dict[str, list[int | float | None]]
    metric: str = "net_revenue"


def load_city_sales(repo: Any, org_id: str, start: date, end: date, metric: str = "net_revenue") -> CitySales:
    """Weekly city series from the canonical metric (group_by=city), one metric call per Monday-start week."""
    from metrics import METRICS
    from metrics.periods import week_start

    spec = METRICS[metric]
    weeks, per_week = [], []
    w = week_start(start)
    while w <= end:
        res = spec.callable(org_id, (w, w + timedelta(days=6)), None, "city", repo=repo)
        weeks.append(w)
        per_week.append({g.key: (None if g.value is None else float(g.value)) for g in res.groups})
        w += timedelta(days=7)
    cities = sorted({c for d in per_week for c in d})
    return CitySales(weeks, {c: [d.get(c, 0.0) for d in per_week] for c in cities}, metric)


def _t_quantile(p: float, df: int) -> float:
    """Student-t quantile via the Cornish-Fisher expansion (no scipy); accurate to ~1% for df >= 5."""
    z = _N.inv_cdf(p)
    return z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)


def _detrended_log(x: np.ndarray) -> np.ndarray:
    y = np.log1p(x)
    t = np.arange(len(y))
    return y - np.polyval(np.polyfit(t, y, 1), t)


def _clean(cs: CitySales, cfg: DesignConfig) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    ok, dropped = {}, {}
    for c, v in cs.sales.items():
        a = np.array([np.nan if x is None else float(x) for x in v])
        if np.isnan(a).mean() > cfg.max_missing_frac:
            dropped[c] = "too many missing weeks"
        elif (np.nan_to_num(a) <= 0).mean() > cfg.max_zero_week_frac:
            dropped[c] = "too many zero-sales weeks"
        else:
            a = np.where(np.isnan(a), np.nanmean(a), a)
            ok[c] = a
    return ok, dropped


def match_score(t: np.ndarray, c: np.ndarray, cfg: DesignConfig) -> tuple[float, float, float]:
    corr = float(np.corrcoef(_detrended_log(t), _detrended_log(c))[0, 1])
    ratio = float(np.log(t.mean() / c.mean()))
    return corr - cfg.level_penalty * abs(ratio), corr, ratio


def design_geo_holdout(cs: CitySales, channel: str, start_date: date, cfg: DesignConfig = DesignConfig(),
                       treatment_candidates: Sequence[str] | None = None, contribution_margin_ratio: float | None = None,
                       prior_ci_width_paise: float | None = None, predicted_effect_paise: float | None = None) -> dict[str, Any]:
    """Return a full design dict, or INSUFFICIENT_DATA. `contribution_margin_ratio` (metric layer, optional) converts
    revenue at risk into contribution at risk; `prior_ci_width_paise` (e.g. the MMM 80% interval width for this channel's
    weekly contribution x test weeks) lets us state the uncertainty reduction; `predicted_effect_paise` is a model
    prediction if one exists - never invented."""
    n = len(cs.weeks)
    if start_date.weekday() != 0:
        return insufficient("start_date must be a Monday (weekly design)", "none")
    if n < cfg.min_pre_weeks:
        return insufficient(f"{n} pre-period weeks < {cfg.min_pre_weeks} needed to match cities and estimate noise", "none")
    if len(set(cs.weeks)) != n:
        return insufficient("duplicate weeks in city sales", "none")
    if any(len(v) != n for v in cs.sales.values()):
        return insufficient("city series lengths differ from weeks", "none")
    series, dropped = _clean(cs, cfg)
    if len(series) < 2:
        return insufficient(f"fewer than 2 usable cities (dropped: {dropped})", "none")
    total = sum(float(a.sum()) for a in series.values())
    cands = [c for c in (treatment_candidates or series) if c in series]
    # best control per candidate
    best: dict[str, tuple[float, str, float, float]] = {}
    for t in cands:
        scored = [(match_score(series[t], series[c], cfg), c) for c in series if c != t]
        scored = [(s, c) for (s, c) in scored if s[1] >= cfg.min_match_corr]
        if scored:
            (sc, corr, ratio), c = max(scored, key=lambda x: x[0][0])
            best[t] = (sc, c, corr, ratio)
    if not best:
        return insufficient(f"no city has a look-alike with detrended correlation >= {cfg.min_match_corr}", "none")
    order = sorted(best, key=lambda t: -best[t][0])
    treat: list[str] = []
    control: dict[str, str] = {}
    share = 0.0
    for t in order:
        if len(treat) >= cfg.max_treatment_cities:
            break
        if t in control.values():
            continue
        # re-pick the control among cities not already used on either side
        used = set(treat) | set(control.values())
        pool = [(match_score(series[t], series[c], cfg), c) for c in series if c != t and c not in used]
        pool = [(s, c) for (s, c) in pool if s[1] >= cfg.min_match_corr]
        if not pool:
            continue
        (sc, corr, ratio), c = max(pool, key=lambda x: x[0][0])
        city_share = float(series[t].sum()) / total
        if share + city_share > cfg.max_treatment_share and treat:
            continue
        if city_share > cfg.max_treatment_share:
            continue
        treat.append(t)
        control[t] = c
        share += city_share
    if not treat:
        return insufficient(f"every matchable city exceeds {cfg.max_treatment_share:.0%} of sales; hold-out too costly", "none")
    T = sum(series[t] for t in treat)
    C = sum(series[control[t]] for t in treat)
    d = np.log(T) - np.log(C)  # pre-period log ratio
    sigma = float(d.std(ddof=1))
    slope_t = _slope_t(d)
    W, P = cfg.test_weeks, n
    se_log = sigma * math.sqrt(1 / W + 1 / P)
    mde_log = (_N.inv_cdf(0.975) + _N.inv_cdf(0.80)) * se_log
    weekly_T = float(T[-min(8, n):].mean())  # recent level of held-out cities (paise/week)
    mde_pct = 1 - math.exp(-mde_log)
    ci80_half_paise = Z80 * se_log * weekly_T * W
    m = contribution_margin_ratio
    risk_rev = int(round(W * weekly_T * cfg.assumed_channel_share))
    worst_rev = int(round(W * weekly_T * cfg.worst_case_channel_share))
    unc: dict[str, Any] = {"measured_effect_80pct_half_width_paise": int(round(ci80_half_paise)),
                           "mde_80pct_power_pct_of_heldout_sales": mde_pct}
    if prior_ci_width_paise and prior_ci_width_paise > 0:
        unc["prior_80pct_width_paise"] = int(round(prior_ci_width_paise))
        unc["expected_uncertainty_reduction"] = max(0.0, 1 - 2 * ci80_half_paise / prior_ci_width_paise)
    else:
        unc["expected_uncertainty_reduction"] = None
        unc["note"] = "no prior interval supplied (no valid MMM); only the experiment's own precision and MDE are stated"
    end_date = start_date + timedelta(days=7 * W - 1)
    assumptions = [
        "weekly log(treatment/control) pre-period noise is i.i.d. normal and constant (parallel trends in logs)",
        "no spillover between held-out and control cities (shared audiences, delivery, marketplaces)",
        f"channel drives {cfg.assumed_channel_share:.0%} of held-out sales (risk estimate; worst case {cfg.worst_case_channel_share:.0%})",
        "MDE: 80% power, 5% two-sided; intervals are 80%",
        f"matching uses {n} pre-period weeks of {cs.metric}",
    ]
    warnings = []
    if abs(slope_t) > 2:
        warnings.append(f"pre-period log ratio trends (t={slope_t:.1f}); parallel-trends assumption is doubtful")
    if sigma == 0:
        warnings.append("zero pre-period noise is implausible; check data")
    hyp = (f"Pausing {channel} ads in {', '.join(treat)} for {W} weeks reduces total {cs.metric} there by at least "
           f"{mde_pct:.1%} relative to matched control cities ({', '.join(control[t] for t in treat)}); smaller effects "
           f"cannot be detected with this design.")
    plan = [f"Pre-period: {n} weeks of weekly {cs.metric} by city (already available). Freeze the city lists before launch.",
            f"Run {start_date.isoformat()} to {end_date.isoformat()} ({W} weeks); do not change other channels' budgets in these cities.",
            "Measure with difference-in-differences on log(treatment total / control total): effect = mean(post) - mean(pre) of that ratio, "
            "80% interval from the pre-period noise (intelligence.experiments.measure_did).",
            "Do not stop early on a peeked result; read out only at the end date. Exclude festival/sale weeks that hit only one group."]
    ads = {"meta": "In Meta Ads Manager, edit each campaign/ad set running this channel > Audience > Locations > Exclude the held-out cities "
                   f"({', '.join(treat)}); keep control cities included and unchanged.",
           "google_ads": "In Google Ads, open each campaign > Settings > Locations > Excluded > add the held-out cities "
                         f"({', '.join(treat)}); leave control cities targeted."}
    ads_text = ads.get(channel, "In the channel's ad platform, exclude the held-out cities from every campaign's location targeting; leave control cities unchanged.")
    ads_text += " Platform UI labels change; verify against the current Ads Manager before launch. Re-include the cities on the end date."
    risk: dict[str, Any] = {"revenue_at_risk_paise": risk_rev, "worst_case_revenue_at_risk_paise": worst_rev,
                            "heldout_weekly_sales_paise": int(round(weekly_T)), "heldout_share_of_total_sales": share}
    if m is not None:
        risk["contribution_at_risk_paise"] = int(round(risk_rev * m))
        risk["worst_case_contribution_at_risk_paise"] = int(round(worst_rev * m))
        risk["contribution_margin_ratio_source"] = "metric layer (caller supplied)"
    pe = {"point": None if predicted_effect_paise is None else int(round(predicted_effect_paise)), "ci_low": None, "ci_high": None,
          "note": "No model prediction available; see uncertainty.mde for what the design can detect." if predicted_effect_paise is None else "model prediction"}
    return {"status": "ok", "design": "geo_holdout", "channel": channel, "treatment_cities": treat,
            "control_cities": [control[t] for t in treat],
            "matching": {"method": "detrended-log weekly sales correlation minus level-ratio penalty; one distinct control per treatment city",
                         "rationale": "look-alike cities share seasonality/promos so a post-period gap is attributable to the pause",
                         "pairs": [{"treatment": t, "control": control[t], "correlation": best_pair(series, t, control[t], cfg)[1],
                                    "log_level_ratio": best_pair(series, t, control[t], cfg)[2]} for t in treat],
                         "dropped_cities": dropped},
            "start_date": start_date, "end_date": end_date, "test_weeks": W, "risk": risk, "risk_paise": risk.get("contribution_at_risk_paise", risk_rev),
            "uncertainty": unc, "pre_period_noise_log_sd": sigma, "hypothesis": hyp, "predicted_effect": pe, "measurement_plan": plan,
            "ads_manager_instructions": ads_text, "assumptions": assumptions, "warnings": warnings}


def best_pair(series: Mapping[str, np.ndarray], t: str, c: str, cfg: DesignConfig):
    return match_score(series[t], series[c], cfg)


def _slope_t(d: np.ndarray) -> float:
    n = len(d)
    t = np.arange(n)
    b, a = np.polyfit(t, d, 1)
    res = d - (a + b * t)
    s2 = (res**2).sum() / (n - 2)
    sxx = ((t - t.mean()) ** 2).sum()
    return float(b / math.sqrt(s2 / sxx)) if s2 > 0 else 0.0


def measure_did(treat_pre: Sequence[float], treat_post: Sequence[float], control_pre: Sequence[float],
                control_post: Sequence[float], min_pre: int = 8) -> dict[str, Any]:
    """Difference-in-differences on weekly aggregates (paise) in logs: effect = mean(d_post) - mean(d_pre), d = log(T/C).
    80% interval from the PRE-period noise of d (Student-t, df = n_pre - 1); post-period noise is assumed equal to it.
    Returns INSUFFICIENT_DATA when the pre-period is too short/noisy to estimate or data are invalid."""
    tp, tq, cp, cq = (np.asarray(x, dtype=float) for x in (treat_pre, treat_post, control_pre, control_post))
    if len(tp) != len(cp) or len(tq) != len(cq):
        return insufficient("treatment and control series must have equal length in each period", "none")
    if len(tp) < min_pre or len(tq) < 1:
        return insufficient(f"{len(tp)} pre weeks < {min_pre} or no post weeks; cannot estimate pre-period noise", "none")
    for a in (tp, tq, cp, cq):
        if not np.isfinite(a).all() or (a <= 0).any():
            return insufficient("series contain missing or non-positive weeks; log-ratio DiD undefined", "none")
    dpre, dpost = np.log(tp / cp), np.log(tq / cq)
    s = float(dpre.std(ddof=1))
    eff = float(dpost.mean() - dpre.mean())
    se = s * math.sqrt(1 / len(dpost) + 1 / len(dpre))
    q = _t_quantile(0.90, len(dpre) - 1)
    lo, hi = eff - q * se, eff + q * se
    cf = cq * math.exp(dpre.mean())  # counterfactual treatment weekly sales
    cf_sum = float(cf.sum())
    obs_sum = float(tq.sum())
    warnings = []
    if abs(_slope_t(dpre)) > 2:
        warnings.append("pre-period log ratio trends; parallel-trends assumption is doubtful, treat the interval as optimistic")
    return {"status": "ok", "method": "difference-in-differences on log(treatment/control), 80% interval from pre-period noise",
            "effect_pct": math.exp(eff) - 1, "effect_pct_ci_low": math.exp(lo) - 1, "effect_pct_ci_high": math.exp(hi) - 1,
            "effect_paise": int(round(cf_sum * (math.exp(eff) - 1))),
            "ci_low_paise": int(round(cf_sum * (math.exp(lo) - 1))), "ci_high_paise": int(round(cf_sum * (math.exp(hi) - 1))),
            "counterfactual_paise": int(round(cf_sum)), "observed_paise": int(round(obs_sum)),
            "excludes_zero": bool(lo > 0 or hi < 0), "pre_noise_log_sd": s, "n_pre": len(dpre), "n_post": len(dpost), "warnings": warnings}


def persist_experiment(repo: Any, org_id: str, design: Mapping[str, Any]) -> str:
    if design.get("status") != "ok":
        raise ValueError("only a valid design is persisted")
    return repo.save_experiment(org_id, {"hypothesis": design["hypothesis"], "design": "geo_holdout", "channel": design["channel"],
                                         "treatment_cities": design["treatment_cities"], "control_cities": design["control_cities"],
                                         "start_date": design["start_date"], "end_date": design["end_date"],
                                         "risk_paise": design["risk_paise"],
                                         "predicted_effect": {**design["predicted_effect"], "uncertainty": design["uncertainty"],
                                                              "measurement_plan": design["measurement_plan"],
                                                              "ads_manager_instructions": design["ads_manager_instructions"],
                                                              "assumptions": design["assumptions"]}})


def record_measurement(repo: Any, org_id: str, experiment_id: str, did: Mapping[str, Any]) -> None:
    if did.get("status") != "ok":
        raise ValueError("cannot record an INSUFFICIENT_DATA measurement as a completed result")
    repo.update_experiment_measured(org_id, experiment_id, {"point": did["effect_paise"], "ci_low": did["ci_low_paise"],
                                                            "ci_high": did["ci_high_paise"], "detail": dict(did)}, "completed")
