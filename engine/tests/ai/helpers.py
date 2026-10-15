"""Shared fixtures for AI-agent tests: real metric layer over metrics' hand-built InMemoryRepository + mock sources."""
from __future__ import annotations

from datetime import date

from ai.mocks import StaticAnomalySource, StaticDataQualitySource, StaticModelSource, StaticRecommendationSource
from ai.tools import ToolBackends, ToolExecutor
from tests.metrics.fx import ORG, OTHER_ORG, PERIOD, repo_with_spend

TODAY = date(2026, 10, 1)
WEEK = PERIOD.start  # Monday 2026-09-14
W0, W1 = PERIOD.start.isoformat(), PERIOD.end.isoformat()

REC = {
    "id": "rec-1", "type": "budget_shift",
    "payload": {"from_channel": "meta", "to_channel": "google", "shift_paise": 5_000_000,
                "reason": "Google shows a flatter marginal response than Meta at current spend."},
    "expected_impact_paise": 1_250_000, "ci_low_paise": 400_000, "ci_high_paise": 2_100_000, "prob_beats_current": 0.72,
}
EXP = {
    "id": "exp-1", "hypothesis": "Pausing Meta prospecting in Tier 2 cities will not reduce orders.", "design": "geo_holdout",
    "channel": "meta", "treatment_cities": ["Indore"], "control_cities": ["Bhopal"], "risk_paise": 300_000,
    "predicted_effect": {"point_paise": -50_000, "ci_low_paise": -200_000, "ci_high_paise": 80_000},
}
ANOM = {"id": "an-1", "metric": "contribution_margin", "date": "2026-09-17", "direction": "down", "robust_z": -3.4,
        "week_start": W0}
CAUSE = {"drivers": [{"driver": "rto_rate", "share_of_change": 0.6}, {"driver": "discounts", "share_of_change": 0.4}]}
MODEL_OK = {"status": "OK", "reason": "78 weeks of data", "models": [{"model_type": "mmm_v0", "weeks": 78, "r_hat_max": 1.01}]}
MODEL_INSUFFICIENT = {"status": "INSUFFICIENT_DATA", "reason": "47 weeks < 52", "fallback": "descriptive", "models": []}


def make_backends(*, model=MODEL_INSUFFICIENT, recs=(REC,), exps=(EXP,), anomalies=(ANOM,), dq=None, org=ORG) -> ToolBackends:
    return ToolBackends(
        repo=repo_with_spend(),
        anomalies=StaticAnomalySource({org: list(anomalies)}, {org: {"an-1": CAUSE}}),
        models=StaticModelSource({org: model}),
        recommendations=StaticRecommendationSource({org: list(recs)}, {org: list(exps)}),
        data_quality=StaticDataQualitySource({org: dq or {"status": "ok", "issues": [], "duplicate_orders": 0}}),
    )


def make_executor(backends: ToolBackends | None = None, org: str = ORG) -> ToolExecutor:
    return ToolExecutor(org, backends or make_backends(), today=lambda: TODAY)
