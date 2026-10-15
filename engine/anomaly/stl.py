"""Robust seasonal-trend decomposition (STL-style), numpy only.

statsmodels is NOT installed in the build interpreter, so this is our own implementation of the STL idea, not
`statsmodels.tsa.seasonal.STL`: iterate (1) seasonal = per-cycle-position MEDIAN of the detrended series (robust
cycle-subseries smoother, centred to zero mean), (2) trend = robust LOESS (tricube kernel, local linear, bisquare
robustness weights) of the deseasonalised series. residual = y - trend - seasonal. If statsmodels is later
available this function can be swapped without touching callers.
"""
from __future__ import annotations

import numpy as np


def _loess(y: np.ndarray, window: int, weights: np.ndarray | None = None) -> np.ndarray:
    n = len(y)
    window = min(window, n)
    x = np.arange(n, dtype=float)
    out = np.empty(n)
    w_rob = np.ones(n) if weights is None else weights
    for i in range(n):
        lo = max(0, min(i - window // 2, n - window))
        idx = np.arange(lo, lo + window)
        d = np.abs(x[idx] - i)
        h = max(d.max(), 1.0) + 1e-9
        w = (1 - (d / h) ** 3) ** 3 * w_rob[idx]
        if w.sum() <= 1e-12:
            w = np.ones(len(idx))
        sw = w.sum()
        xm = (w * x[idx]).sum() / sw
        ym = (w * y[idx]).sum() / sw
        sxx = (w * (x[idx] - xm) ** 2).sum()
        b = 0.0 if sxx < 1e-12 else (w * (x[idx] - xm) * (y[idx] - ym)).sum() / sxx
        out[i] = ym + b * (i - xm)
    return out


def robust_stl(y: np.ndarray, period: int = 7, trend_window: int | None = None, outer: int = 3, inner: int = 2):
    """Return (trend, seasonal, resid). `y` must be finite (caller interpolates gaps)."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 2 * period:
        raise ValueError("need at least two full seasonal cycles")
    tw = trend_window or max(2 * period + 1, (n // 4) | 1)
    rob = np.ones(n)
    trend = np.full(n, np.median(y))
    seasonal = np.zeros(n)
    for _ in range(outer):
        for _ in range(inner):
            detr = y - trend
            sub = np.array([np.median(detr[k::period]) for k in range(period)])
            sub -= sub.mean()
            seasonal = np.array([sub[i % period] for i in range(n)])
            trend = _loess(y - seasonal, tw, rob)
        resid = y - trend - seasonal
        s = 6.0 * np.median(np.abs(resid - np.median(resid)))
        if s <= 1e-12:
            break
        u = np.clip(np.abs(resid) / s, 0, 1)
        rob = (1 - u**2) ** 2
    return trend, seasonal, y - trend - seasonal
