"""Root-cause driver decomposition via the log-mean Divisia index (LMDI-I), an EXACT additive decomposition.

For X = f1 * f2 * ... * fk and two periods (0 -> 1):
    X1 - X0 = sum_i L(X1, X0) * ln(f_i1 / f_i0),   L(a, b) = (a - b) / (ln a - ln b)   (L(a, a) = a)
so driver contributions sum to the total change with no residual / interaction term.

This is arithmetic attribution of an observed change, NOT a causal estimate: it says which factor moved, not why
(e.g. a CTR drop could be creative fatigue or an audience change). Evidence strings say so.

Inputs are raw counts / amounts. Revenue-type amounts come from the metric layer (`net_revenue`); counts that have no
canonical metric yet (sessions, orders, impressions, clicks, conversions, spend) come from ledger-backed providers
supplied through the MetricSource (see sources.py). Nothing here defines revenue, profit, CAC, ROAS or margin: ROAS
here is the identity revenue/spend of whatever platform-reported revenue the caller supplies, and is labelled so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from .common import insufficient
from .sources import MetricSource

REVENUE_CHAIN = ("sessions", "cvr", "aov")
ROAS_CHAIN = ("impressions_per_rupee", "ctr", "cvr", "aov")


@dataclass(frozen=True)
class Driver:
    driver: str
    from_value: float
    to_value: float
    pct_change: float  # of the driver itself (e.g. CVR down 12%)
    contribution: float  # in the metric's unit (paise as int-valued for money metrics)
    share_of_change: float | None  # contribution / total change (None if total change is 0)
    rank: int
    evidence: str
    confidence: str  # high | medium | low (qualitative, see `confidence_reason`)
    confidence_reason: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def log_mean(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        raise ValueError("log-mean needs positive values")
    return a if a == b else (a - b) / (math.log(a) - math.log(b))


def lmdi(f0: Mapping[str, float], f1: Mapping[str, float]) -> dict[str, float]:
    """Exact contributions per factor; sum(values) == prod(f1) - prod(f0) up to float rounding."""
    if set(f0) != set(f1):
        raise ValueError("factor sets differ between periods")
    x0, x1 = math.prod(f0.values()), math.prod(f1.values())
    if any(v <= 0 or not math.isfinite(v) for v in (*f0.values(), *f1.values())):
        raise ValueError("all factors must be positive and finite")
    if x0 == x1:
        return {k: 0.0 for k in f0}
    lm = log_mean(x1, x0)
    return {k: lm * math.log(f1[k] / f0[k]) for k in f0}


def _round_to_total(contribs: dict[str, float], total: int) -> dict[str, int]:
    """Largest-remainder rounding so integer-paise contributions sum EXACTLY to the integer total change."""
    floors = {k: math.floor(v) for k, v in contribs.items()}
    rem = total - sum(floors.values())
    order = sorted(contribs, key=lambda k: contribs[k] - floors[k], reverse=True)
    for k in order[: max(rem, 0)]:
        floors[k] += 1
    return floors


def _confidence(share: float | None, counts: Sequence[float], min_count: float, quality_issues: Sequence[str]) -> tuple[str, str]:
    if share is None:
        return "low", "total change is zero; no meaningful ranking"
    reasons = []
    level = 2
    if min(counts) < min_count:
        level -= 1
        reasons.append(f"small sample (min count {min(counts):.0f} < {min_count:.0f}), period-to-period noise may explain the move")
    if abs(share) < 0.25:
        level -= 1
        reasons.append(f"driver explains only {abs(share):.0%} of the change")
    if quality_issues:
        level -= 1
        reasons.append("data-quality issues on inputs: " + "; ".join(quality_issues[:3]))
    if not reasons:
        reasons.append(f"explains {abs(share):.0%} of the change on adequate volume with clean inputs")
    return ("low", "medium", "high")[max(level, 0)], "; ".join(reasons)


def decompose(metric: str, chain: Sequence[str], f0: Mapping[str, float], f1: Mapping[str, float], *, unit: str,
              counts: Sequence[float] = (), min_count: float = 100, quality_issues: Sequence[str] = (),
              display: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Generic LMDI decomposition with ranking. `chain` fixes the factor order/names; values must be positive."""
    for name in chain:
        if name not in f0 or name not in f1:
            return insufficient(f"{metric}: missing factor {name!r}", "descriptive")
        if not (f0[name] > 0 and f1[name] > 0) or not (math.isfinite(f0[name]) and math.isfinite(f1[name])):
            return insufficient(f"{metric}: factor {name!r} is zero/undefined in one period ({f0[name]} -> {f1[name]}); "
                                "multiplicative decomposition is not defined", "descriptive")
    c = lmdi({k: f0[k] for k in chain}, {k: f1[k] for k in chain})
    x0, x1 = math.prod(f0[k] for k in chain), math.prod(f1[k] for k in chain)
    total = x1 - x0
    if unit == "paise":
        total_i = round(x1) - round(x0)
        contrib: dict[str, float] = dict(_round_to_total(c, total_i))
        total = total_i
    else:
        contrib = c
    order = sorted(chain, key=lambda k: -abs(contrib[k]))
    drivers = []
    disp = display or {}
    for rank, k in enumerate(order, 1):
        share = None if total == 0 else contrib[k] / total
        conf, why = _confidence(share, counts or [min_count], min_count, quality_issues)
        pct = f1[k] / f0[k] - 1
        name = disp.get(k, k)
        evidence = (f"{name} moved {f0[k]:.4g} -> {f1[k]:.4g} ({pct:+.1%}); log-mean attribution gives "
                    f"{contrib[k]:+,.4g} {unit} of the {total:+,.4g} total change. Arithmetic attribution, not a causal estimate.")
        drivers.append(Driver(name, f0[k], f1[k], pct, contrib[k], share, rank, evidence, conf, why))
    return {"status": "ok", "metric": metric, "unit": unit, "from_value": x0, "to_value": x1, "total_change": total,
            "sum_of_contributions": sum(contrib.values()), "drivers": drivers, "method": "LMDI-I (log-mean Divisia), exact"}


def _total(source: MetricSource, metric: str, org_id: str, start: date, end: date):
    s = source.series(metric, org_id, start, end, "day")
    vals = [v for v in s.values]
    if not vals or any(v is None for v in vals):
        return None, s
    return float(sum(vals)), s


def explain_revenue_change(source: MetricSource, org_id: str, p0: tuple[date, date], p1: tuple[date, date]) -> dict[str, Any]:
    """net_revenue = sessions x CVR x AOV, CVR = orders/sessions, AOV = net_revenue/orders.
    net_revenue is the metric-layer value; `orders` and `sessions` come from ledger/traffic providers
    (orders must use the same basis as net_revenue: non-cancelled, non-RTO)."""
    tot: dict[str, list[float]] = {"net_revenue": [], "orders": [], "sessions": []}
    issues: list[str] = []
    for (a, b) in (p0, p1):
        for m in tot:
            try:
                v, s = _total(source, m, org_id, a, b)
            except (KeyError, LookupError) as e:
                return insufficient(f"revenue driver decomposition needs {m}: {e}; connect traffic data" if m == "sessions"
                                    else f"revenue driver decomposition needs {m}: {e}")
            if v is None:
                return insufficient(f"{m} has undefined days in {a}..{b}")
            tot[m].append(v)
            issues += s.issues
    f = [{"sessions": tot["sessions"][i], "cvr": tot["orders"][i] / tot["sessions"][i] if tot["sessions"][i] else 0.0,
          "aov": tot["net_revenue"][i] / tot["orders"][i] if tot["orders"][i] else 0.0} for i in (0, 1)]
    out = decompose("net_revenue", REVENUE_CHAIN, f[0], f[1], unit="paise", counts=[tot["orders"][0], tot["orders"][1]],
                    min_count=100, quality_issues=sorted(set(issues)), display={"cvr": "conversion rate (orders/sessions)", "aov": "AOV (net revenue/orders)"})
    if out["status"] == "ok":
        # sessions x CVR x AOV equals orders x AOV equals net_revenue only because CVR/AOV are defined as ratios of the same totals
        out["period0"], out["period1"] = [d.isoformat() for d in p0], [d.isoformat() for d in p1]
    return out


def explain_roas_change(source: MetricSource, org_id: str, p0: tuple[date, date], p1: tuple[date, date], platform_label: str = "platform-reported") -> dict[str, Any]:
    """Platform ROAS ({label}, not causal) = (1000/CPM) x CTR x CVR x AOV. spend/impressions/clicks/conversions/platform revenue
    are summed from ledger ad tables via providers named ad_spend, impressions, clicks, conversions, platform_revenue."""
    names = ("ad_spend", "impressions", "clicks", "conversions", "platform_revenue")
    tot: dict[str, list[float]] = {n: [] for n in names}
    for (a, b) in (p0, p1):
        for m in names:
            try:
                v, _ = _total(source, m, org_id, a, b)
            except (KeyError, LookupError) as e:
                return insufficient(f"ROAS driver decomposition needs {m}: {e}")
            if v is None:
                return insufficient(f"{m} has undefined days in {a}..{b}")
            tot[m].append(v)
    f = []
    for i in (0, 1):
        sp, im, cl, cv, rv = (tot[n][i] for n in names)
        if min(sp, im, cl, cv, rv) <= 0:
            return insufficient(f"ROAS chain has a zero input in period {i} (spend/impr/clicks/conv/revenue = {sp}/{im}/{cl}/{cv}/{rv})")
        f.append({"impressions_per_rupee": im / sp, "ctr": cl / im, "cvr": cv / cl, "aov": rv / cv})
    out = decompose("platform_roas", ROAS_CHAIN, f[0], f[1], unit="ratio", counts=[tot["clicks"][0], tot["clicks"][1]], min_count=500,
                    display={"impressions_per_rupee": "CPM (inverse: impressions per rupee)", "ctr": "CTR", "cvr": "CVR (conversions/clicks)", "aov": "AOV (platform revenue/conversions)"})
    if out["status"] == "ok":
        out["label"] = f"{platform_label}, not causal"
        for d in out["drivers"]:
            if d.driver.startswith("CPM"):  # report CPM itself, not its inverse
                cpm0, cpm1 = 1000 / d.from_value / 100, 1000 / d.to_value / 100  # spend is paise -> rupees
                object.__setattr__(d, "evidence", d.evidence + f" In CPM terms: Rs {cpm0:.2f} -> Rs {cpm1:.2f} per 1000 impressions.")
    return out
