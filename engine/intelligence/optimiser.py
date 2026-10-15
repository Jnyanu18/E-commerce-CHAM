"""Budget optimiser over validated MMM response curves.

Runs ONLY on a `run_mmm` result with status 'ok' (data gate + diagnostics gate passed). Anything else, or a result
whose posterior does not clearly favour the shift, yields an experiment recommendation instead of a precise number.

Method: maximise the posterior-mean total weekly response subject to total budget and per-channel bounds, by greedy
marginal allocation in fixed rupee steps over the mean curves (exact for concave curves, a good heuristic for S-shaped
Hill curves). The proposed split is then scored against EVERY posterior draw to get the expected incremental outcome,
the 80% interval and P(proposed beats current). Impact is in the MMM outcome metric (default net_revenue), not profit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from models.posterior import HI_Q, LO_Q, PosteriorDraws

from .common import insufficient, is_insufficient


@dataclass(frozen=True)
class Constraints:
    """total_budget_paise: weekly total to allocate (default: sum of current). max_increase_pct / max_decrease_pct are
    per-channel caps relative to current spend (e.g. 0.3 = at most +30%). min/max_spend_paise are absolute overrides."""
    total_budget_paise: int | None = None
    max_increase_pct: float = 0.30
    max_decrease_pct: float = 0.30
    min_spend_paise: Mapping[str, int] | None = None
    max_spend_paise: Mapping[str, int] | None = None
    horizon_weeks: int = 4
    recommend_min_prob: float = 0.75  # below this the model is "unsure" -> suggest an experiment instead
    step_paise: int | None = None


def _bounds(cur: dict[str, int], c: Constraints) -> tuple[dict[str, int], dict[str, int]]:
    lo, hi = {}, {}
    for ch, v in cur.items():
        lo[ch] = int(np.floor(v * (1 - c.max_decrease_pct)))
        hi[ch] = int(np.ceil(v * (1 + c.max_increase_pct)))
        if c.min_spend_paise and ch in c.min_spend_paise:
            lo[ch] = max(lo[ch], int(c.min_spend_paise[ch]))
        if c.max_spend_paise and ch in c.max_spend_paise:
            hi[ch] = min(hi[ch], int(c.max_spend_paise[ch]))
        if lo[ch] > hi[ch]:
            raise ValueError(f"{ch}: min {lo[ch]} > max {hi[ch]}")
    return lo, hi


def _total_response(draws: PosteriorDraws, alloc: Mapping[str, float]) -> np.ndarray:
    """(D,) summed weekly response per draw."""
    return sum(draws.response(ch, [v])[:, 0] for ch, v in alloc.items())


def allocate(draws: PosteriorDraws, current: dict[str, int], c: Constraints) -> dict[str, int]:
    total = int(c.total_budget_paise if c.total_budget_paise is not None else sum(current.values()))
    lo, hi = _bounds(current, c)
    if sum(lo.values()) > total or sum(hi.values()) < total:
        raise ValueError(f"infeasible: budget {total} outside [{sum(lo.values())}, {sum(hi.values())}] implied by the constraints")
    alloc = dict(lo)
    remaining = total - sum(lo.values())
    step = c.step_paise or max(1, total // 200)
    mean_resp = {ch: (lambda v, ch=ch: float(draws.response(ch, [v])[:, 0].mean())) for ch in current}
    while remaining > 0:
        best, best_gain, best_inc = None, -np.inf, 0
        for ch in alloc:
            inc = min(step, remaining, hi[ch] - alloc[ch])
            if inc <= 0:
                continue
            gain = (mean_resp[ch](alloc[ch] + inc) - mean_resp[ch](alloc[ch])) / inc
            if gain > best_gain:
                best, best_gain, best_inc = ch, gain, inc
        if best is None:  # pragma: no cover - guarded by feasibility check
            raise ValueError("could not place remaining budget within the caps")
        alloc[best] += best_inc
        remaining -= best_inc
    assert sum(alloc.values()) == total and all(lo[k] <= v <= hi[k] for k, v in alloc.items())
    return alloc


def _experiment_rec(reason: str, channel: str | None, extra: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "type": "run_experiment", "payload": {"reason": reason, "suggested_experiment": {
        "design": "geo_holdout", "channel": channel,
        "why": "Response curves are not credible enough for a precise budget move; a geo-holdout would measure the channel's incremental effect directly."}, **extra},
        "expected_impact_paise": None, "ci_low_paise": None, "ci_high_paise": None, "prob_beats_current": None}


def optimise(mmm_result: Mapping[str, Any], current_weekly_spend_paise: dict[str, int], constraints: Constraints = Constraints(),
             experiment_channel_hint: str | None = None) -> dict[str, Any]:
    if is_insufficient(mmm_result):
        hint = experiment_channel_hint or (max(current_weekly_spend_paise, key=current_weekly_spend_paise.get) if current_weekly_spend_paise else None)
        return _experiment_rec(f"MMM not usable: {mmm_result['reason']}", hint,
                               {"mmm_status": "INSUFFICIENT_DATA", "fallback": mmm_result.get("fallback", "descriptive")})
    if mmm_result.get("status") != "ok" or "draws" not in mmm_result:
        return insufficient("optimiser requires a passed MMM result with posterior draws")
    draws: PosteriorDraws = mmm_result["draws"]
    unknown = set(current_weekly_spend_paise) - set(draws.channels)
    if unknown:
        return insufficient(f"no validated response curve for channels {sorted(unknown)}; cannot optimise them")
    cur = {k: int(v) for k, v in current_weekly_spend_paise.items()}
    if any(v < 0 for v in cur.values()) or not cur:
        return insufficient("current spend must be a non-empty set of non-negative paise amounts")
    try:
        prop = allocate(draws, cur, constraints)
    except ValueError as e:
        return insufficient(str(e), "none")
    diff = _total_response(draws, prop) - _total_response(draws, cur)  # per draw, weekly paise
    h = constraints.horizon_weeks
    exp, lo, hi = diff.mean() * h, np.percentile(diff, LO_Q) * h, np.percentile(diff, HI_Q) * h
    prob = float((diff > 0).mean())
    moves = {ch: prop[ch] - cur[ch] for ch in cur}
    top = max(moves, key=lambda k: abs(moves[k]))
    payload = {"current_weekly_spend_paise": cur, "proposed_weekly_spend_paise": prop, "moves_paise": moves,
               "horizon_weeks": h, "outcome_metric": mmm_result.get("outcome_metric", "net_revenue"),
               "constraints": {k: v for k, v in constraints.__dict__.items()},
               "interval": "80% central, from posterior draws", "model_diagnostics": mmm_result["diagnostics"]}
    if all(m == 0 for m in moves.values()):
        payload["reasoning"] = "The current split is already at the optimum within your constraints; no change recommended."
        return {"status": "ok", "type": "budget_shift", "payload": payload, "expected_impact_paise": 0, "ci_low_paise": 0,
                "ci_high_paise": 0, "prob_beats_current": 0.5}
    if prob < constraints.recommend_min_prob:
        r = _experiment_rec(f"The model is unsure: the proposed shift beats the current split in only {prob:.0%} of posterior draws "
                            f"(need {constraints.recommend_min_prob:.0%}).", top,
                            {"uncertain_estimate": {"expected_impact_paise": int(round(exp)), "ci_low_paise": int(round(lo)),
                                                    "ci_high_paise": int(round(hi)), "prob_beats_current": prob, "note": "not a recommendation"},
                             "current_weekly_spend_paise": cur})
        return r
    up = [k for k, v in moves.items() if v > 0]
    down = [k for k, v in moves.items() if v < 0]
    payload["reasoning"] = (f"Move weekly spend from {', '.join(down) or 'no channel'} to {', '.join(up) or 'no channel'} "
                            f"(largest move: {top} {moves[top] / 100:+,.0f} Rs/week). The model expects about Rs {exp / 100:,.0f} "
                            f"extra {payload['outcome_metric']} over {h} weeks (80% range Rs {lo / 100:,.0f} to Rs {hi / 100:,.0f}); the new split beats "
                            f"the current one in {prob:.0%} of posterior draws. Marginal returns diminish, so gains shrink as a channel is scaled. "
                            f"This is a model estimate calibrated to {mmm_result['diagnostics'].get('calibration_points', 0)} experiment(s), not a guarantee.")
    return {"status": "ok", "type": "budget_shift", "payload": payload, "expected_impact_paise": int(round(exp)),
            "ci_low_paise": int(round(lo)), "ci_high_paise": int(round(hi)), "prob_beats_current": prob}


def persist_recommendation(repo: Any, org_id: str, rec: Mapping[str, Any], model_run_id: str | None = None,
                           experiment_id: str | None = None) -> str:
    if rec.get("status") != "ok":
        raise ValueError("only status=ok recommendations are persisted; INSUFFICIENT_DATA is reported, not stored as advice")
    return repo.save_recommendation(org_id, {"type": rec["type"], "payload": rec["payload"], "model_run_id": model_run_id,
                                              "experiment_id": experiment_id, "expected_impact_paise": rec["expected_impact_paise"],
                                              "ci_low_paise": rec["ci_low_paise"], "ci_high_paise": rec["ci_high_paise"],
                                              "prob_beats_current": rec["prob_beats_current"]})
