"""MMM v0 orchestration: sufficiency gate -> sampler -> diagnostics gate -> curves + contributions.

`run_mmm` never returns curves or intervals unless BOTH the data gate and the diagnostics gate pass.
The sampler is injectable (`fit=`), defaulting to the lazy PyMC fit; tests inject a fake sampler to exercise the
orchestration, and a real PyMC test is skipped when PyMC is absent.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Sequence

import numpy as np

from intelligence.common import insufficient, is_insufficient

from .fit_pymc import CalibrationPoint, FitConfig
from .gate import GateConfig, MMMData, _full_weeks, check_diagnostics, check_sufficiency, descriptive_fallback
from .posterior import PosteriorDraws

Fit = Callable[..., PosteriorDraws]


def run_mmm(data: MMMData, gate_cfg: GateConfig = GateConfig(), fit_cfg: FitConfig = FitConfig(),
            calibration: Sequence[CalibrationPoint] = (), fit: Fit | None = None,
            exclude_failing_channels: bool = False) -> dict[str, Any]:
    """Returns {status:'ok', channels, curves, contributions, diagnostics, draws, params} or the INSUFFICIENT_DATA shape
    (always carrying a `descriptive` fallback summary and no curves)."""
    gate = check_sufficiency(data, gate_cfg, exclude_failing_channels)
    if is_insufficient(gate):
        gate["descriptive"] = descriptive_fallback(data) if data.weeks else None
        gate["stage"] = "data_gate"
        return gate
    channels: list[str] = gate["channels"]
    if fit is None:
        try:
            import pymc  # noqa: F401
        except ImportError:
            r = insufficient("PyMC is not installed in this environment, so the model cannot be fitted", "descriptive",
                             stage="dependency", descriptive=descriptive_fallback(data))
            r["details"] = gate["details"]
            return r
        from .fit_pymc import fit_pymc as fit  # type: ignore[assignment]
    draws = fit(data, channels, fit_cfg, calibration)
    bad = check_diagnostics(draws.diagnostics, gate_cfg)
    if bad:
        bad["stage"] = "diagnostics_gate"
        bad["descriptive"] = descriptive_fallback(data)
        return bad
    weeks, spend, y, _ = _full_weeks(data)
    curves = [draws.curve(c, 2.0 * float(draws.x_scale[draws.idx(c)])) for c in channels]
    contributions = draws.contributions({c: spend[c] for c in channels})
    return {"status": "ok", "channels": channels, "excluded_channels": gate["excluded_channels"], "curves": curves,
            "contributions": contributions, "diagnostics": dict(draws.diagnostics), "draws": draws,
            "data_start": weeks[0], "data_end": weeks[-1], "outcome_metric": data.outcome_metric,
            "params": {"model": "mmm_v0", "adstock": f"geometric(l_max={draws.l_max})", "saturation": "hill",
                       "interval": "80% central credible", "gate": gate_cfg.__dict__, "fit": fit_cfg.__dict__,
                       "calibration_points": len(calibration)}}
