"""Persistence for model_runs, channel_response_curves, experiments, recommendations (migration 0003).

Abstract repo + in-memory (tests) + psycopg (real DB). Money is integer paise; callers pass plain dicts.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any, Mapping, Protocol, Sequence

import numpy as np


def jsonable(o: Any) -> Any:
    """Recursively convert numpy / dates / dataclasses to JSON-safe values (NaN/inf -> None)."""
    if o is None or isinstance(o, (str, bool)):
        return o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, int):
        return o
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if is_dataclass(o) and not isinstance(o, type):
        return jsonable(asdict(o))
    if isinstance(o, Mapping):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    return str(o)


class IntelligenceRepo(Protocol):
    def save_model_run(self, org_id: str, model_type: str, data_start: date | None, data_end: date | None,
                       params: dict, diagnostics: dict, sufficient_data: bool) -> str: ...
    def save_curves(self, org_id: str, model_run_id: str, curves: Sequence[Mapping[str, Any]]) -> None: ...
    def save_experiment(self, org_id: str, exp: Mapping[str, Any]) -> str: ...
    def update_experiment_measured(self, org_id: str, experiment_id: str, measured_effect: dict, status: str = "completed") -> None: ...
    def save_recommendation(self, org_id: str, rec: Mapping[str, Any]) -> str: ...


def _check_curve(c: Mapping[str, Any]) -> None:
    n = len(c["spend_grid_paise"])
    for k in ("expected_revenue_paise", "lower_paise", "upper_paise"):
        if len(c[k]) != n:
            raise ValueError(f"curve {c['channel']}: {k} length differs from spend grid")
    if not all(isinstance(v, int) and not isinstance(v, bool) for k in ("spend_grid_paise", "expected_revenue_paise", "lower_paise", "upper_paise") for v in c[k]):
        raise ValueError("curve values must be integer paise")


def persist_mmm_result(repo: IntelligenceRepo, org_id: str, result: Mapping[str, Any]) -> str:
    """Write a model_runs row for ANY run_mmm outcome. Curves are written only when the run passed the gates.

    diagnostics shape (read by app.ai_backends.DbModelSource): top-level `verdict` ("OK" | "WEAK" | "INSUFFICIENT_DATA"),
    `reason`, and r_hat_max / ess_bulk_min / divergences / ppc_coverage_80; gate detail lives under `gate`.
    """
    if result.get("status") == "ok":
        d = result["diagnostics"]
        diag = {**d, "divergences": d.get("n_divergences", 0), "verdict": "OK",
                "reason": "all diagnostics passed (R-hat, ESS, divergences, posterior-predictive coverage)",
                "contributions": result["contributions"]}
        rid = repo.save_model_run(org_id, "mmm_v0", result["data_start"], result["data_end"], jsonable(result["params"]),
                                  jsonable(diag), True)
        repo.save_curves(org_id, rid, result["curves"])
        return rid
    gate = {k: v for k, v in result.items() if k in ("stage", "details", "diagnostics", "failing_channels", "descriptive", "fallback")}
    diag = {"verdict": "INSUFFICIENT_DATA", "reason": result.get("reason", "data insufficient"), "fallback": result.get("fallback", "descriptive"),
            "gate": gate}
    for k in ("r_hat_max", "ess_bulk_min", "ppc_coverage_80"):
        if k in (result.get("diagnostics") or {}):
            diag[k] = result["diagnostics"][k]
    if "n_divergences" in (result.get("diagnostics") or {}):
        diag["divergences"] = result["diagnostics"]["n_divergences"]
    return repo.save_model_run(org_id, "mmm_v0", None, None, {"model": "mmm_v0"}, jsonable(diag), False)


class InMemoryIntelRepo:
    def __init__(self) -> None:
        self.model_runs: dict[str, dict] = {}
        self.curves: list[dict] = []
        self.experiments: dict[str, dict] = {}
        self.recommendations: dict[str, dict] = {}

    def save_model_run(self, org_id, model_type, data_start, data_end, params, diagnostics, sufficient_data):
        rid = str(uuid.uuid4())
        self.model_runs[rid] = dict(id=rid, org_id=org_id, model_type=model_type, data_start=data_start, data_end=data_end,
                                    params=params, diagnostics=diagnostics, sufficient_data=sufficient_data)
        return rid

    def save_curves(self, org_id, model_run_id, curves):
        if model_run_id not in self.model_runs or not self.model_runs[model_run_id]["sufficient_data"]:
            raise ValueError("curves may only be attached to a model run with sufficient_data = true")
        for c in curves:
            _check_curve(c)
            self.curves.append(dict(org_id=org_id, model_run_id=model_run_id, channel=c["channel"],
                                    spend_grid_paise=list(c["spend_grid_paise"]), expected_revenue_paise=list(c["expected_revenue_paise"]),
                                    lower_paise=list(c["lower_paise"]), upper_paise=list(c["upper_paise"])))

    def save_experiment(self, org_id, exp):
        eid = str(uuid.uuid4())
        self.experiments[eid] = dict(id=eid, org_id=org_id, status="proposed", **exp)
        return eid

    def update_experiment_measured(self, org_id, experiment_id, measured_effect, status="completed"):
        e = self.experiments[experiment_id]
        if e["org_id"] != org_id:
            raise KeyError(experiment_id)
        e["measured_effect"], e["status"] = measured_effect, status

    def save_recommendation(self, org_id, rec):
        rid = str(uuid.uuid4())
        self.recommendations[rid] = dict(id=rid, org_id=org_id, status="proposed", **rec)
        return rid


class PsycopgIntelRepo:
    """Plain INSERTs; the caller owns the connection / transaction. `org_id` must exist (FK)."""

    def __init__(self, conn: Any):
        self.conn = conn

    def _one(self, sql: str, params: tuple) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def save_model_run(self, org_id, model_type, data_start, data_end, params, diagnostics, sufficient_data):
        from psycopg.types.json import Jsonb
        return str(self._one(
            "insert into model_runs (org_id, model_type, data_start, data_end, params, diagnostics, sufficient_data) "
            "values (%s,%s,%s,%s,%s,%s,%s) returning id",
            (org_id, model_type, data_start, data_end, Jsonb(jsonable(params)), Jsonb(jsonable(diagnostics)), sufficient_data)))

    def save_curves(self, org_id, model_run_id, curves):
        with self.conn.cursor() as cur:
            ok = cur.execute("select sufficient_data from model_runs where id=%s and org_id=%s", (model_run_id, org_id)).fetchone()
            if not ok or not ok[0]:
                raise ValueError("curves may only be attached to a model run with sufficient_data = true")
            for c in curves:
                _check_curve(c)
                cur.execute(
                    "insert into channel_response_curves (org_id, model_run_id, channel, spend_grid_paise, expected_revenue_paise, lower_paise, upper_paise) "
                    "values (%s,%s,%s,%s,%s,%s,%s)",
                    (org_id, model_run_id, c["channel"], list(c["spend_grid_paise"]), list(c["expected_revenue_paise"]),
                     list(c["lower_paise"]), list(c["upper_paise"])))

    def save_experiment(self, org_id, exp):
        from psycopg.types.json import Jsonb
        return str(self._one(
            "insert into experiments (org_id, hypothesis, design, channel, treatment_cities, control_cities, start_date, end_date, risk_paise, predicted_effect) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (org_id, exp["hypothesis"], exp.get("design", "geo_holdout"), exp.get("channel"), list(exp.get("treatment_cities", [])),
             list(exp.get("control_cities", [])), exp.get("start_date"), exp.get("end_date"), exp.get("risk_paise"),
             Jsonb(jsonable(exp.get("predicted_effect"))) if exp.get("predicted_effect") is not None else None)))

    def update_experiment_measured(self, org_id, experiment_id, measured_effect, status="completed"):
        from psycopg.types.json import Jsonb
        with self.conn.cursor() as cur:
            cur.execute("update experiments set measured_effect=%s, status=%s where id=%s and org_id=%s",
                        (Jsonb(jsonable(measured_effect)), status, experiment_id, org_id))
            if cur.rowcount != 1:
                raise KeyError(experiment_id)

    def save_recommendation(self, org_id, rec):
        from psycopg.types.json import Jsonb
        return str(self._one(
            "insert into recommendations (org_id, model_run_id, experiment_id, type, payload, expected_impact_paise, ci_low_paise, ci_high_paise, prob_beats_current) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (org_id, rec.get("model_run_id"), rec.get("experiment_id"), rec["type"], Jsonb(jsonable(rec["payload"])),
             rec.get("expected_impact_paise"), rec.get("ci_low_paise"), rec.get("ci_high_paise"), rec.get("prob_beats_current"))))
