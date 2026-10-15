import numpy as np
import pytest

from intelligence.optimiser import Constraints, optimise, persist_recommendation
from intelligence.persistence import InMemoryIntelRepo, persist_mmm_result
from models import run_mmm
from tests.models.synth import TRUE, make
from tests.models.test_mmm import _planted_draws

CUR = {"meta": 1_000_000, "google": 1_800_000}


def _ok(spread=0.05, seed=0):
    return run_mmm(make(78), fit=lambda d, c, cfg, cal: _planted_draws(seed=seed, spread=spread))


def test_shifts_toward_higher_marginal_return_and_respects_constraints():
    r = optimise(_ok(), CUR, Constraints(max_increase_pct=0.3, max_decrease_pct=0.3))
    assert r["type"] == "budget_shift"
    p = r["payload"]["proposed_weekly_spend_paise"]
    assert sum(p.values()) == sum(CUR.values())  # budget conserved exactly
    for ch in CUR:
        assert CUR[ch] * 0.7 - 1 <= p[ch] <= CUR[ch] * 1.3 + 1
    assert p["meta"] > CUR["meta"] and p["google"] < CUR["google"]
    assert r["expected_impact_paise"] > 0 and r["ci_low_paise"] <= r["expected_impact_paise"] <= r["ci_high_paise"]
    assert 0.75 <= r["prob_beats_current"] <= 1
    assert "80%" in r["payload"]["reasoning"] and "not a guarantee" in r["payload"]["reasoning"]
    assert all(isinstance(r[k], int) for k in ("expected_impact_paise", "ci_low_paise", "ci_high_paise"))


def test_tight_caps_bind():
    r = optimise(_ok(), CUR, Constraints(max_increase_pct=0.05, max_decrease_pct=0.05))
    p = r["payload"]["proposed_weekly_spend_paise"]
    assert p["meta"] <= int(np.ceil(CUR["meta"] * 1.05)) and p["google"] >= int(np.floor(CUR["google"] * 0.95))


def test_absolute_min_max_and_total_budget_override():
    r = optimise(_ok(), CUR, Constraints(total_budget_paise=3_000_000, max_increase_pct=1.0, max_decrease_pct=1.0,
                                         min_spend_paise={"google": 1_700_000}, max_spend_paise={"meta": 1_300_000}, recommend_min_prob=0.0))
    p = r["payload"]["proposed_weekly_spend_paise"]
    assert sum(p.values()) == 3_000_000 and p["google"] >= 1_700_000 and p["meta"] <= 1_300_000


def test_infeasible_constraints_are_reported_not_forced():
    r = optimise(_ok(), CUR, Constraints(total_budget_paise=10_000_000, max_increase_pct=0.1))
    assert r["status"] == "INSUFFICIENT_DATA" and "infeasible" in r["reason"]


def test_unsure_model_emits_experiment_not_precise_number():
    r = optimise(_ok(spread=1.5, seed=3), CUR, Constraints(recommend_min_prob=0.99))
    assert r["type"] == "run_experiment" and r["expected_impact_paise"] is None and r["prob_beats_current"] is None
    assert r["payload"]["suggested_experiment"]["design"] == "geo_holdout"


def test_insufficient_mmm_yields_experiment_recommendation():
    bad = run_mmm(make(30))
    r = optimise(bad, CUR)
    assert r["type"] == "run_experiment" and "30 weeks" in r["payload"]["reason"] and r["expected_impact_paise"] is None


def test_unknown_channel_and_bad_input():
    assert optimise(_ok(), {"tiktok": 100})["status"] == "INSUFFICIENT_DATA"
    assert optimise(_ok(), {})["status"] == "INSUFFICIENT_DATA"
    assert optimise({"status": "ok"}, CUR)["status"] == "INSUFFICIENT_DATA"


def test_already_optimal_no_change():
    ok = _ok()
    r = optimise(ok, CUR, Constraints(max_increase_pct=0, max_decrease_pct=0))
    assert r["expected_impact_paise"] == 0 and set(r["payload"]["moves_paise"].values()) == {0}


def test_persist_in_memory_curves_only_for_sufficient_runs():
    repo = InMemoryIntelRepo()
    rid = persist_mmm_result(repo, "org1", _ok())
    assert repo.model_runs[rid]["sufficient_data"] and len(repo.curves) == 2
    bad = persist_mmm_result(repo, "org1", run_mmm(make(30)))
    assert not repo.model_runs[bad]["sufficient_data"] and len(repo.curves) == 2
    assert repo.model_runs[bad]["diagnostics"]["verdict"] == "INSUFFICIENT_DATA" and "30 weeks" in repo.model_runs[bad]["diagnostics"]["reason"]
    with pytest.raises(ValueError):
        repo.save_curves("org1", bad, [])  # even empty attach to insufficient run is refused? (no curves allowed)
    rec = optimise(_ok(), CUR)
    persist_recommendation(repo, "org1", rec, rid)
    with pytest.raises(ValueError):
        persist_recommendation(repo, "org1", {"status": "INSUFFICIENT_DATA"})
