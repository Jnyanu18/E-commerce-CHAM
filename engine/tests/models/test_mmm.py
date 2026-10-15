import numpy as np
import pytest

from models import GateConfig, PosteriorDraws, check_diagnostics, check_sufficiency, descriptive_fallback, run_mmm
from models.curves import geometric_adstock, hill
from models.fit_pymc import CalibrationPoint, FitConfig, calibration_lift, ppc_coverage_80
from models.gate import MMMData
from tests.models.synth import BASE, TRUE, make


def test_gate_30_weeks_insufficient_with_descriptive_fallback():
    d = make(30)
    r = run_mmm(d)
    assert r["status"] == "INSUFFICIENT_DATA" and r["fallback"] == "descriptive"
    assert "30 weeks < 52" in r["reason"]
    assert "curves" not in r and "contributions" not in r
    assert r["descriptive"]["causal"] is False and r["descriptive"]["channels"]["meta"]["total_spend_paise"] > 0


def test_gate_flat_spend_insufficient():
    r = check_sufficiency(make(78, flat=True))
    assert r["status"] == "INSUFFICIENT_DATA" and "too little variation" in r["reason"]
    assert set(r["failing_channels"]) == {"meta", "google"}


def test_gate_passes_on_good_data():
    r = check_sufficiency(make(78))
    assert r["status"] == "ok" and r["channels"] == ["meta", "google"]


def test_gate_boundary_52_weeks_ok_51_not():
    assert check_sufficiency(make(52))["status"] == "ok"
    assert check_sufficiency(make(51))["status"] == "INSUFFICIENT_DATA"


def test_gate_zero_spend_weeks_and_missing_and_collinear_and_empty():
    d = make(78)
    d.spend_paise["meta"] = [0 if i % 3 else v for i, v in enumerate(d.spend_paise["meta"])]  # 67% zero
    assert "zero-spend" in check_sufficiency(d)["reason"]
    d = make(78)
    d.spend_paise["google"] = [None if i < 10 else v for i, v in enumerate(d.spend_paise["google"])]
    assert "missing" in check_sufficiency(d)["reason"]
    d = make(78)
    d.spend_paise["google"] = [int(v * 0.5) for v in d.spend_paise["meta"]]
    assert "collinear" in check_sufficiency(d)["reason"]
    assert check_sufficiency(MMMData([], [], {}))["status"] == "INSUFFICIENT_DATA"


def test_gate_duplicate_weeks_and_gap_weeks():
    d = make(60)
    d.weeks[5] = d.weeks[4]
    assert check_sufficiency(d)["reason"] == "duplicate weeks"
    d = make(60)
    keep = [i for i in range(60) if i not in (20, 21, 22, 23)]  # 4 absent weeks of 60 = 6.7% > 5%
    d2 = MMMData([d.weeks[i] for i in keep], [d.outcome_paise[i] for i in keep], {c: [v[i] for i in keep] for c, v in d.spend_paise.items()})
    assert "missing" in check_sufficiency(d2)["reason"]


def test_exclude_failing_channels_option():
    d = make(78)
    d.spend_paise["google"] = [1_000_000] * 78
    assert check_sufficiency(d)["status"] == "INSUFFICIENT_DATA"
    r = check_sufficiency(d, exclude_failing_channels=True)
    assert r["status"] == "ok" and r["channels"] == ["meta"] and "google" in r["excluded_channels"]


def _planted_draws(n=400, seed=0, spread=0.05):
    rng = np.random.default_rng(seed)
    ch = ["meta", "google"]
    col = lambda key: np.column_stack([TRUE[c][key] * np.abs(1 + spread * rng.standard_normal(n)) + 1e-3 for c in ch])  # noqa: E731
    return PosteriorDraws(ch, col("beta"), np.clip(col("alpha"), 0.01, 0.99), col("k"), col("s"),
                          np.array([TRUE[c]["xmax"] for c in ch]), BASE, 8,
                          diagnostics={"r_hat_max": 1.01, "ess_bulk_min": 900.0, "divergence_frac": 0.0, "ppc_coverage_80": 0.8})


def test_curves_intervals_monotone_and_contain_truth():
    dr = _planted_draws()
    c = dr.curve("meta", 8_000_000)
    p = TRUE["meta"]
    truth = BASE * p["beta"] * hill(np.array(c["spend_grid_paise"]) / p["xmax"], p["k"], p["s"])
    lo, hi = np.array(c["lower_paise"]), np.array(c["upper_paise"])
    assert (lo <= np.array(c["expected_revenue_paise"])).all() and (np.array(c["expected_revenue_paise"]) <= hi).all()
    assert (np.diff(c["expected_revenue_paise"]) >= 0).all()
    inside = ((truth >= lo - 1) & (truth <= hi + 1)).mean()
    assert inside > 0.8
    assert all(isinstance(v, int) for v in c["spend_grid_paise"] + c["lower_paise"])


def test_run_mmm_with_injected_sampler_returns_curves_and_contributions():
    d = make(78)
    r = run_mmm(d, fit=lambda data, ch, cfg, cal: _planted_draws())
    assert r["status"] == "ok" and {c["channel"] for c in r["curves"]} == {"meta", "google"}
    assert r["contributions"]["meta"]["lo_paise"] <= r["contributions"]["meta"]["mean_paise"] <= r["contributions"]["meta"]["hi_paise"]
    # planted truth: meta contribution over observed weeks
    p = TRUE["meta"]
    x = np.array(d.spend_paise["meta"], float)
    tru = (BASE * p["beta"] * hill(geometric_adstock(x, p["alpha"], 8) / p["xmax"], p["k"], p["s"])).sum()
    assert r["contributions"]["meta"]["lo_paise"] * 0.9 <= tru <= r["contributions"]["meta"]["hi_paise"] * 1.1


@pytest.mark.parametrize("bad", [{"r_hat_max": 1.2}, {"ess_bulk_min": 50.0}, {"divergence_frac": 0.2}, {"ppc_coverage_80": 0.3}, {"ppc_coverage_80": None}])
def test_failed_diagnostics_return_insufficient_no_curves(bad):
    def fit(data, ch, cfg, cal):
        dr = _planted_draws()
        dr.diagnostics.update(bad)
        return dr
    r = run_mmm(make(78), fit=fit)
    assert r["status"] == "INSUFFICIENT_DATA" and r["stage"] == "diagnostics_gate" and "curves" not in r
    assert r["fallback"] == "descriptive" and r["descriptive"]["channels"]


def test_check_diagnostics_missing_keys_fail_closed():
    assert check_diagnostics({})["status"] == "INSUFFICIENT_DATA"
    assert check_diagnostics(_planted_draws().diagnostics) is None


def test_no_pymc_means_insufficient_dependency_not_fake_curves():
    pytest.importorskip("numpy")
    try:
        import pymc  # noqa: F401
        pytest.skip("PyMC present; dependency-absent path not applicable")
    except ImportError:
        r = run_mmm(make(78))
        assert r["status"] == "INSUFFICIENT_DATA" and r["stage"] == "dependency" and "curves" not in r


def test_calibration_point_requires_se_and_lift_matches_curve():
    with pytest.raises(ValueError):
        CalibrationPoint("meta", 1, 2, 100, 0)
    dr = _planted_draws()
    p = TRUE["meta"]
    cal = CalibrationPoint("meta", 0.2 * p["xmax"], 0.8 * p["xmax"], 0, 1)
    lift = calibration_lift(dr, cal)
    direct = dr.response("meta", [0.8 * p["xmax"]])[:, 0] - dr.response("meta", [0.2 * p["xmax"]])[:, 0]
    assert np.allclose(lift, direct)


def test_ppc_coverage_calibrated_for_correct_model():
    rng = np.random.default_rng(1)
    mu = np.tile(np.linspace(1, 2, 200), (300, 1))
    sigma = np.full(300, 0.1)
    y = mu[0] + 0.1 * rng.standard_normal(200)
    assert 0.7 < ppc_coverage_80(y, mu, sigma, rng) < 0.9


def test_descriptive_fallback_empty_and_basic():
    assert descriptive_fallback(MMMData([], [], {}))["channels"] == {}
    assert descriptive_fallback(make(20))["note"].startswith("Association")


def test_pymc_recovers_planted_response():
    pytest.importorskip("pymc")
    pytest.importorskip("arviz")
    d = make(78)
    r = run_mmm(d, fit_cfg=FitConfig(draws=300, tune=300, chains=2), gate_cfg=GateConfig(min_ess_bulk=50, max_r_hat=1.1, max_divergence_frac=0.05))
    assert r["status"] in ("ok", "INSUFFICIENT_DATA")  # a weak fit must be refused, a good one must bracket the truth
    if r["status"] == "ok":
        p = TRUE["meta"]
        x = np.array(d.spend_paise["meta"], float)
        tru = (BASE * p["beta"] * hill(geometric_adstock(x, p["alpha"], 8) / p["xmax"], p["k"], p["s"])).sum()
        c = r["contributions"]["meta"]
        assert 0.5 * tru < c["mean_paise"] < 1.6 * tru
