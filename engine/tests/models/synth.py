"""Synthetic weekly MMM data with a planted response."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from models.curves import geometric_adstock, hill
from models.gate import MMMData

START = date(2024, 1, 1)  # a Monday
TRUE = {"meta": dict(alpha=0.4, k=0.5, s=1.5, beta=0.6, xmax=4_000_000.0),
        "google": dict(alpha=0.2, k=0.4, s=1.2, beta=0.3, xmax=2_000_000.0)}
BASE = 10_000_000.0  # paise per week
NOISE = 0.03


def make(n_weeks=78, seed=3, flat=False, channels=("meta", "google")) -> MMMData:
    rng = np.random.default_rng(seed)
    weeks = [START + timedelta(weeks=i) for i in range(n_weeks)]
    spend, y = {}, BASE * (1 + 0.1 * np.sin(2 * np.pi * np.arange(n_weeks) / 52.18))
    for c in channels:
        p = TRUE[c]
        x = np.full(n_weeks, 0.5 * p["xmax"]) if flat else p["xmax"] * np.clip(0.5 + 0.35 * rng.standard_normal(n_weeks), 0.05, 1.0)
        spend[c] = [int(v) for v in x]
        a = geometric_adstock(x, p["alpha"], 8)
        y = y + BASE * p["beta"] * hill(a / p["xmax"], p["k"], p["s"])
    y = y * (1 + NOISE * rng.standard_normal(n_weeks))
    return MMMData(weeks, [int(v) for v in y], spend)
