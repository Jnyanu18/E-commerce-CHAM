"""Posterior draws -> response curves, contributions, credible intervals. Pure numpy; sampler-agnostic.

Draws are the ONLY source of intervals. Nothing here fabricates uncertainty: with no draws there is no curve.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .curves import geometric_adstock, hill

LO_Q, HI_Q = 10.0, 90.0  # 80% central credible interval


@dataclass
class PosteriorDraws:
    """D posterior draws for C channels. Response (paise/week) of channel c at weekly spend s (paise):
    y_scale * beta[d, c] * hill(s / x_scale[c], K[d, c], S[d, c])  (steady state; adstock is normalised)."""
    channels: list[str]
    beta: np.ndarray  # (D, C) asymptotic contribution in outcome-scale units
    alpha: np.ndarray  # (D, C) adstock decay
    k: np.ndarray  # (D, C) half-saturation, in x_scale units
    s: np.ndarray  # (D, C) Hill slope
    x_scale: np.ndarray  # (C,) paise (max weekly spend)
    y_scale: float  # paise (mean weekly outcome)
    l_max: int = 8
    baseline: np.ndarray | None = None  # (D, T) non-media component per week, outcome-scale units
    sigma: np.ndarray | None = None  # (D,)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def n_draws(self) -> int:
        return self.beta.shape[0]

    def idx(self, channel: str) -> int:
        return self.channels.index(channel)

    def response(self, channel: str, weekly_spend_paise: np.ndarray | float) -> np.ndarray:
        """(D, ...) weekly outcome paise attributable to the channel at the given steady weekly spend(s)."""
        c = self.idx(channel)
        sp = np.atleast_1d(np.asarray(weekly_spend_paise, dtype=float))
        z = sp[None, :] / self.x_scale[c]
        return self.y_scale * self.beta[:, c : c + 1] * hill(z, self.k[:, c : c + 1], self.s[:, c : c + 1])

    def curve(self, channel: str, max_spend_paise: float, points: int = 25) -> dict[str, Any]:
        grid = np.unique(np.round(np.linspace(0, max_spend_paise, points)).astype(np.int64))
        r = self.response(channel, grid)
        return {"channel": channel, "spend_grid_paise": grid.tolist(),
                "expected_revenue_paise": np.round(r.mean(axis=0)).astype(np.int64).tolist(),
                "lower_paise": np.round(np.percentile(r, LO_Q, axis=0)).astype(np.int64).tolist(),
                "upper_paise": np.round(np.percentile(r, HI_Q, axis=0)).astype(np.int64).tolist()}

    def contributions(self, spend: dict[str, np.ndarray]) -> dict[str, dict[str, Any]]:
        """Total media-attributed outcome over the observed weeks (paise): mean and 80% interval per channel."""
        out = {}
        for c, name in enumerate(self.channels):
            tot = np.zeros(self.n_draws)
            x = np.nan_to_num(np.asarray(spend[name], dtype=float))
            for d in range(self.n_draws):
                a = geometric_adstock(x, float(self.alpha[d, c]), self.l_max)
                tot[d] = (self.y_scale * self.beta[d, c] * hill(a / self.x_scale[c], self.k[d, c], self.s[d, c])).sum()
            out[name] = {"mean_paise": int(round(tot.mean())), "lo_paise": int(round(np.percentile(tot, LO_Q))),
                         "hi_paise": int(round(np.percentile(tot, HI_Q)))}
        return out
