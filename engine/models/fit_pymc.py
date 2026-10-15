"""Bayesian MMM v0 sampler (PyMC). `pymc` is imported lazily; the build interpreter did NOT have it installed, so
`fit_pymc` has never been executed in this branch (the test that exercises it is skipped via importorskip).

Deviation from CLAUDE.md ("PyMC-Marketing"): the model is written with plain PyMC primitives and the SAME functional
forms PyMC-Marketing uses (geometric adstock, Hill/logistic-style saturation, lift-test calibration). Reason: the
PyMC-Marketing API could not be checked against current docs from the offline build environment, and the estimator
boundary (`PosteriorDraws`) makes the sampler swappable. See DECISIONS.md.

Model (all in standardised units: y/y_scale, spend/x_scale[c]):
  y_t ~ Normal(baseline_t + sum_c beta_c * hill(adstock_c,t; K_c, S_c), sigma)
  alpha_c ~ Beta(2, 2)            geometric decay, weights normalised (steady state = spend)
  beta_c  ~ HalfNormal(1)         asymptotic channel contribution, non-negative (ads do not reduce sales)
  K_c ~ Beta(2, 3), S_c ~ Gamma(3, 2)   half-saturation (fraction of max spend), slope; weakly informative for small data
  baseline = intercept + trend + Fourier(52.18 weeks, 2 pairs); intercept ~ N(1, 0.5), others ~ N(0, 0.3 / 0.2)
  sigma ~ HalfNormal(0.3)
Experiment calibration hook: each CalibrationPoint adds a Normal likelihood on the model-implied incremental weekly
outcome between two steady weekly spend levels, observed from a geo/lift experiment (point estimate and standard error).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .curves import lift_between
from .gate import MMMData, _full_weeks
from .posterior import PosteriorDraws


@dataclass(frozen=True)
class FitConfig:
    draws: int = 1000
    tune: int = 1000
    chains: int = 4
    target_accept: float = 0.95
    l_max: int = 8
    fourier_pairs: int = 2
    seed: int = 20260101


@dataclass(frozen=True)
class CalibrationPoint:
    """Experiment result used to calibrate the model: raising steady weekly spend on `channel` from spend_lo_paise to
    spend_hi_paise produced an incremental weekly outcome of lift_paise_per_week (standard error lift_se_paise)."""
    channel: str
    spend_lo_paise: float
    spend_hi_paise: float
    lift_paise_per_week: float
    lift_se_paise: float

    def __post_init__(self) -> None:
        if not self.lift_se_paise > 0:
            raise ValueError("calibration needs a positive standard error; a lift without uncertainty is not usable")


def ppc_coverage_80(y_scaled: np.ndarray, mu: np.ndarray, sigma: np.ndarray, rng: np.random.Generator) -> float:
    """Share of observed weeks inside the 80% posterior-predictive interval. mu: (D, T), sigma: (D,)."""
    y_rep = mu + sigma[:, None] * rng.standard_normal(mu.shape)
    lo, hi = np.percentile(y_rep, [10, 90], axis=0)
    return float(np.mean((y_scaled >= lo) & (y_scaled <= hi)))


def fourier_terms(n: int, pairs: int) -> np.ndarray:
    t = np.arange(n)
    cols = []
    for k in range(1, pairs + 1):
        cols += [np.sin(2 * np.pi * k * t / 52.18), np.cos(2 * np.pi * k * t / 52.18)]
    return np.column_stack(cols) if cols else np.zeros((n, 0))


def fit_pymc(data: MMMData, channels: Sequence[str], cfg: FitConfig = FitConfig(),
             calibration: Sequence[CalibrationPoint] = ()) -> PosteriorDraws:
    import pymc as pm  # lazy
    import pytensor.tensor as pt
    import arviz as az

    weeks, spend, y, _ = _full_weeks(data)
    ok = ~np.isnan(y) & ~np.any([np.isnan(spend[c]) for c in channels], axis=0)
    T = len(weeks)
    y_scale = float(np.nanmean(y))
    ys = np.where(ok, y / y_scale, 0.0)
    x_scale = np.array([float(np.nanmax(spend[c])) for c in channels])
    xs = {c: np.nan_to_num(spend[c]) / x_scale[i] for i, c in enumerate(channels)}
    lags = {c: np.column_stack([np.concatenate([np.zeros(l), xs[c][: T - l]]) for l in range(cfg.l_max)]) for c in channels}
    fourier = fourier_terms(T, cfg.fourier_pairs)
    trend = np.linspace(-1, 1, T)
    for cal in calibration:
        if cal.channel not in channels:
            raise ValueError(f"calibration for unknown channel {cal.channel}")

    with pm.Model():
        alpha = pm.Beta("alpha", 2, 2, shape=len(channels))
        beta = pm.HalfNormal("beta", 1.0, shape=len(channels))
        k = pm.Beta("k", 2, 3, shape=len(channels))
        s = pm.Gamma("s", 3, 2, shape=len(channels))
        intercept = pm.Normal("intercept", 1.0, 0.5)
        b_trend = pm.Normal("b_trend", 0.0, 0.3)
        b_four = pm.Normal("b_four", 0.0, 0.2, shape=fourier.shape[1])
        sigma = pm.HalfNormal("sigma", 0.3)
        baseline = intercept + b_trend * trend + (pt.dot(fourier, b_four) if fourier.shape[1] else 0.0)
        mu = baseline
        w_pows = pt.arange(cfg.l_max)
        for i, c in enumerate(channels):
            w = alpha[i] ** w_pows
            w = w / w.sum()
            a = pt.dot(lags[c], w)
            zs = (a + 1e-6) ** s[i]  # epsilon: d/da a**s is undefined at 0 for s<1
            mu = mu + beta[i] * zs / (zs + k[i] ** s[i])
        pm.Deterministic("mu", mu)
        pm.Deterministic("baseline", baseline)
        pm.Normal("y", mu[np.flatnonzero(ok)], sigma, observed=ys[ok])
        for j, cal in enumerate(calibration):
            i = list(channels).index(cal.channel)
            lo, hi = cal.spend_lo_paise / x_scale[i], cal.spend_hi_paise / x_scale[i]
            h = lambda z: z ** s[i] / (z ** s[i] + k[i] ** s[i])  # noqa: E731
            pm.Normal(f"cal_{j}", beta[i] * (h(hi) - h(lo)), cal.lift_se_paise / y_scale, observed=cal.lift_paise_per_week / y_scale)
        idata = pm.sample(draws=cfg.draws, tune=cfg.tune, chains=cfg.chains, cores=1, target_accept=cfg.target_accept,
                          random_seed=cfg.seed, progressbar=False)

    post = idata.posterior
    flat = lambda name: post[name].stack(sample=("chain", "draw")).transpose("sample", ...).values  # noqa: E731
    rh = az.rhat(post)
    ess = az.ess(post, method="bulk")
    names = ["alpha", "beta", "k", "s", "intercept", "b_trend", "sigma"]
    r_hat_max = float(max(np.nanmax(rh[n].values) for n in names))
    ess_min = float(min(np.nanmin(ess[n].values) for n in names))
    div = idata.sample_stats["diverging"].values
    mu_d, sig_d = flat("mu"), flat("sigma")
    rng = np.random.default_rng(cfg.seed)
    diagnostics = {"r_hat_max": r_hat_max, "ess_bulk_min": ess_min, "divergence_frac": float(div.mean()),
                   "n_divergences": int(div.sum()), "ppc_coverage_80": ppc_coverage_80(ys[ok], mu_d[:, ok], sig_d, rng),
                   "draws": cfg.draws, "tune": cfg.tune, "chains": cfg.chains, "calibration_points": len(calibration),
                   "engine": "pymc (plain PyMC, not PyMC-Marketing)"}
    return PosteriorDraws(list(channels), flat("beta"), flat("alpha"), flat("k"), flat("s"), x_scale, y_scale, cfg.l_max,
                          baseline=flat("baseline"), sigma=sig_d, diagnostics=diagnostics)


def calibration_lift(draws: PosteriorDraws, cal: CalibrationPoint) -> np.ndarray:
    """Model-implied weekly lift (paise) per draw for a calibration point (used to check calibration, in tests)."""
    c = draws.idx(cal.channel)
    return lift_between(draws.beta[:, c], draws.k[:, c], draws.s[:, c], draws.x_scale[c], draws.y_scale,
                        cal.spend_lo_paise, cal.spend_hi_paise)
