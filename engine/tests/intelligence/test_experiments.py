from datetime import date, timedelta

import numpy as np
import pytest

from intelligence.experiments import (CitySales, DesignConfig, design_geo_holdout, measure_did, persist_experiment,
                                      record_measurement)
from intelligence.persistence import InMemoryIntelRepo

W0 = date(2026, 1, 5)  # Monday


def cities(n_weeks=20, seed=2):
    rng = np.random.default_rng(seed)
    weeks = [W0 + timedelta(weeks=i) for i in range(n_weeks)]
    out = {}
    for i, name in enumerate(["Mumbai", "Delhi", "Bengaluru", "Chennai", "Kolkata", "Hyderabad", "Pune", "Jaipur", "Lucknow", "Indore"]):
        pattern = np.cumsum(rng.normal(0, 0.12, n_weeks)) + 0.3 * np.sin(np.arange(n_weeks) * (0.5 + 0.13 * i) + i)
        out[name] = 4_000_000 * (1 + 0.1 * i) * np.exp(pattern)
    out["Surat"] = out["Pune"] * 0.9 * np.exp(rng.normal(0, 0.015, n_weeks))  # planted look-alike of Pune
    return CitySales(weeks, {k: [int(x) for x in v] for k, v in out.items()})


def test_matching_picks_planted_lookalike():
    r = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"])
    assert r["status"] == "ok" and r["treatment_cities"] == ["Pune"] and r["control_cities"] == ["Surat"]
    pair = r["matching"]["pairs"][0]
    assert pair["correlation"] > 0.9


def test_auto_selection_pairs_the_twins_and_respects_share_cap():
    r = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), DesignConfig(max_treatment_share=0.15))
    assert r["status"] == "ok"
    assert {"Pune", "Surat"} & set(r["treatment_cities"]) and ({"Pune", "Surat"} <= set(r["treatment_cities"] + r["control_cities"]))
    assert r["risk"]["heldout_share_of_total_sales"] <= 0.15 + 1e-9
    assert not set(r["treatment_cities"]) & set(r["control_cities"])


def test_design_contents_and_money_types():
    r = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"], contribution_margin_ratio=0.35,
                           prior_ci_width_paise=500_000_000)
    assert isinstance(r["risk_paise"], int) and r["risk"]["revenue_at_risk_paise"] > r["risk"]["contribution_at_risk_paise"] > 0
    assert r["risk"]["worst_case_revenue_at_risk_paise"] > r["risk"]["revenue_at_risk_paise"]
    assert 0 <= r["uncertainty"]["expected_uncertainty_reduction"] <= 1
    assert r["uncertainty"]["mde_80pct_power_pct_of_heldout_sales"] > 0
    assert "Ads Manager" in r["ads_manager_instructions"] and "Pune" in r["ads_manager_instructions"]
    assert r["predicted_effect"]["point"] is None  # no model prediction => none invented
    assert r["end_date"] == r["start_date"] + timedelta(days=27) and r["assumptions"] and r["measurement_plan"]


def test_no_prior_means_no_fabricated_reduction():
    r = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"])
    assert r["uncertainty"]["expected_uncertainty_reduction"] is None


def test_insufficient_cases():
    cs = cities()
    assert design_geo_holdout(cs, "meta", W0 + timedelta(days=1))["status"] == "INSUFFICIENT_DATA"  # not Monday
    short = CitySales(cs.weeks[:8], {k: v[:8] for k, v in cs.sales.items()})
    assert design_geo_holdout(short, "meta", W0 + timedelta(weeks=24))["status"] == "INSUFFICIENT_DATA"
    assert design_geo_holdout(CitySales([], {}), "meta", W0)["status"] == "INSUFFICIENT_DATA"
    one = CitySales(cs.weeks, {"Pune": cs.sales["Pune"]})
    assert design_geo_holdout(one, "meta", W0 + timedelta(weeks=24))["status"] == "INSUFFICIENT_DATA"
    rng = np.random.default_rng(9)
    noise = CitySales(cs.weeks, {f"c{i}": [int(x) for x in 1_000_000 * np.exp(rng.normal(0, 0.3, 20))] for i in range(3)})
    r = design_geo_holdout(noise, "meta", W0 + timedelta(weeks=24), DesignConfig(min_match_corr=0.95))
    assert r["status"] == "INSUFFICIENT_DATA" and "look-alike" in r["reason"]


def test_dirty_cities_dropped_not_used():
    cs = cities()
    cs.sales["Surat"] = [None] * 10 + cs.sales["Surat"][10:]
    r = design_geo_holdout(cs, "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"])
    assert "Surat" in r["matching"]["dropped_cities"] and r["control_cities"] != ["Surat"]


def _did_data(lift=-0.15, n_pre=16, n_post=4, seed=5, sd=0.03):
    rng = np.random.default_rng(seed)
    ctrl = 5e6 * np.exp(np.cumsum(rng.normal(0, 0.05, n_pre + n_post)))
    ratio = 0.9 * np.exp(rng.normal(0, sd, n_pre + n_post))
    ratio[n_pre:] *= np.exp(np.log1p(lift))
    t = ctrl * ratio
    return t[:n_pre], t[n_pre:], ctrl[:n_pre], ctrl[n_pre:]


def test_did_recovers_planted_lift_within_interval():
    r = measure_did(*_did_data(-0.15))
    assert r["status"] == "ok"
    assert abs(r["effect_pct"] - (-0.15)) < 0.05 and r["excludes_zero"] is True
    assert r["ci_low_paise"] < r["effect_paise"] < r["ci_high_paise"] < 0
    assert r["counterfactual_paise"] > r["observed_paise"]


def test_did_interval_coverage_under_noise_is_near_nominal():
    hits = 0
    for s in range(200):
        r = measure_did(*_did_data(-0.10, seed=s, sd=0.04))
        hits += r["effect_pct_ci_low"] <= -0.10 <= r["effect_pct_ci_high"]
    assert 0.70 <= hits / 200 <= 0.92  # nominal 80%


def test_did_null_effect_rarely_excludes_zero():
    ex = sum(measure_did(*_did_data(0.0, seed=s))["excludes_zero"] for s in range(200))
    assert ex / 200 < 0.32  # nominal 20% for an 80% interval


def test_did_insufficient_and_invalid():
    tp, tq, cp, cq = _did_data()
    assert measure_did(tp[:4], tq, cp[:4], cq)["status"] == "INSUFFICIENT_DATA"
    assert measure_did(tp, [], cp, [])["status"] == "INSUFFICIENT_DATA"
    assert measure_did(tp, tq[:2], cp, cq)["status"] == "INSUFFICIENT_DATA"
    bad = tp.copy(); bad[3] = 0
    assert measure_did(bad, tq, cp, cq)["status"] == "INSUFFICIENT_DATA"
    nan = tp.copy(); nan[2] = np.nan
    assert measure_did(nan, tq, cp, cq)["status"] == "INSUFFICIENT_DATA"


def test_persist_and_record_in_memory():
    repo = InMemoryIntelRepo()
    d = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"])
    eid = persist_experiment(repo, "org1", d)
    assert repo.experiments[eid]["status"] == "proposed" and repo.experiments[eid]["treatment_cities"] == ["Pune"]
    record_measurement(repo, "org1", eid, measure_did(*_did_data()))
    assert repo.experiments[eid]["status"] == "completed" and repo.experiments[eid]["measured_effect"]["point"] < 0
    with pytest.raises(ValueError):
        record_measurement(repo, "org1", eid, {"status": "INSUFFICIENT_DATA"})
    with pytest.raises(ValueError):
        persist_experiment(repo, "org1", {"status": "INSUFFICIENT_DATA"})
