"""Response-curve building blocks (numpy). Shared by the PyMC model, the posterior post-processing and the optimiser.

Functional forms
  adstock: geometric, normalised so a constant spend s has steady-state adstocked value s:
           a_t = sum_{l=0}^{L-1} w_l x_{t-l},  w_l = alpha^l / sum_k alpha^k
  saturation: Hill  h(z) = z^S / (z^S + K^S)   with z = adstocked spend / x_scale (x_scale = max weekly spend)
  weekly response (outcome paise) of channel c = y_scale * beta_c * h(z)
"""
from __future__ import annotations

import numpy as np


def adstock_weights(alpha: np.ndarray | float, l_max: int) -> np.ndarray:
    """Weights shape (..., l_max) for alpha of any shape."""
    a = np.asarray(alpha, dtype=float)[..., None]
    w = a ** np.arange(l_max)
    return w / w.sum(axis=-1, keepdims=True)


def geometric_adstock(x: np.ndarray, alpha: float, l_max: int = 8) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    w = adstock_weights(alpha, l_max)
    out = np.zeros_like(x)
    for l in range(l_max):
        out[l:] += w[l] * x[: len(x) - l] if l else w[0] * x
    return out


def hill(z: np.ndarray, k: np.ndarray | float, s: np.ndarray | float) -> np.ndarray:
    z = np.maximum(np.asarray(z, dtype=float), 0.0)
    zs = z**s
    return zs / (zs + np.asarray(k, dtype=float) ** s)


def lift_between(beta: np.ndarray, k: np.ndarray, s: np.ndarray, x_scale: float, y_scale: float,
                 spend_lo: float, spend_hi: float) -> np.ndarray:
    """Model-implied incremental weekly outcome (paise) of raising steady weekly spend lo -> hi.
    Used by the experiment-calibration hook (lift test = observed incremental outcome at two spend levels)."""
    return y_scale * beta * (hill(spend_hi / x_scale, k, s) - hill(spend_lo / x_scale, k, s))
