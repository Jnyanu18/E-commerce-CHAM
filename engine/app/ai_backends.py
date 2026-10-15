"""Real (Postgres-backed) implementations of the `ai.sources` protocols. Main-owned wiring.

Reads only what the intelligence jobs and system checks have stored; computes nothing itself. Statuses follow
CONTRACTS section 6: a model result is OK / WEAK / INSUFFICIENT_DATA and missing diagnostics fail closed (WEAK).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ai.tools import ToolBackends
from metrics.repository import PostgresRepository

from .dataquality import run_checks


def _row_dicts(cur) -> list[dict[str, Any]]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _jsonable(v: Any) -> Any:
    if isinstance(v, (date,)):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "as_tuple"):  # Decimal
        return float(v)
    return v


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: _jsonable(v) for k, v in d.items()}


class DbAnomalySource:
    """Anomalies (with their root causes) are stored by the anomaly job in insights.anomalies for the week."""

    def __init__(self, conn: Any):
        self.conn = conn

    def _week(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        row = self.conn.execute("select anomalies from insights where org_id = %s and week_start = %s", (org_id, week_start)).fetchone()
        return list(row[0]) if row and row[0] else []

    def anomalies(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        return [{k: v for k, v in a.items() if k != "root_cause"} for a in self._week(org_id, week_start)]

    def root_causes(self, org_id: str, anomaly_id: str) -> dict[str, Any] | None:
        rows = self.conn.execute("select anomalies from insights where org_id = %s order by week_start desc limit 12", (org_id,)).fetchall()
        for (anoms,) in rows:
            for a in anoms or []:
                if a.get("id") == anomaly_id:
                    return a.get("root_cause")
        return None


class DbModelSource:
    def __init__(self, conn: Any):
        self.conn = conn

    def model_results(self, org_id: str) -> dict[str, Any]:
        cur = self.conn.execute(
            "select id::text, model_type, data_start, data_end, sufficient_data, diagnostics, created_at "
            "from model_runs where org_id = %s order by created_at desc limit 5", (org_id,))
        runs = _row_dicts(cur)
        if not runs:
            return {"status": "INSUFFICIENT_DATA", "reason": "no model has been run for this brand yet", "models": []}
        models = []
        for r in runs:
            diag = r["diagnostics"] or {}
            if not r["sufficient_data"]:
                status, reason = "INSUFFICIENT_DATA", diag.get("reason") or "data insufficient for a causal model"
            else:
                status = {"OK": "OK", "WEAK": "WEAK"}.get(str(diag.get("verdict", "")).upper(), "WEAK")
                reason = diag.get("reason") or ("diagnostics passed" if status == "OK" else "diagnostics missing or marginal")
            models.append({"model_run_id": r["id"], "model_type": r["model_type"], "status": status, "reason": reason,
                           "data_start": _jsonable(r["data_start"]), "data_end": _jsonable(r["data_end"]),
                           "created_at": _jsonable(r["created_at"]),
                           "diagnostics": {k: diag[k] for k in ("r_hat_max", "ess_bulk_min", "divergences", "ppc_coverage_80", "verdict") if k in diag}})
        latest = models[0]
        return {"status": latest["status"], "reason": latest["reason"], "models": models}


class DbRecommendationSource:
    def __init__(self, conn: Any):
        self.conn = conn

    def recommendations(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "select id::text, type, payload, expected_impact_paise, ci_low_paise, ci_high_paise, prob_beats_current, status, created_at "
            "from recommendations where org_id = %s and created_at < %s::date + 8 and status in ('proposed','accepted') "
            "order by created_at desc limit 20", (org_id, week_start))
        out = []
        for r in _row_dicts(cur):
            r = _clean(r)
            r["interval_level"] = 0.8 if r.get("ci_low_paise") is not None else None  # CLAUDE.md: 80% interval
            out.append(r)
        return out

    def experiment_candidates(self, org_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "select id::text, hypothesis, design, channel, treatment_cities, control_cities, risk_paise, predicted_effect, status "
            "from experiments where org_id = %s and status = 'proposed' order by created_at desc limit 10", (org_id,))
        return [_clean(r) for r in _row_dicts(cur)]


class DbDataQualitySource:
    def __init__(self, conn: Any):
        self.conn = conn

    def data_quality(self, org_id: str, start: date, end: date) -> dict[str, Any]:
        rep = run_checks(self.conn, org_id)
        return {"status": rep["status"], "generated_at": rep["generated_at"],
                "issues": [{"code": c["code"], "severity": c["severity"], "count": c["count"], "message": c["message"]}
                           for c in rep["checks"] if c["severity"] != "ok"]}


def build_backends(conn: Any) -> ToolBackends:
    return ToolBackends(repo=PostgresRepository(conn), anomalies=DbAnomalySource(conn), models=DbModelSource(conn),
                        recommendations=DbRecommendationSource(conn), data_quality=DbDataQualitySource(conn))


def previous_week_start(today: date) -> date:
    """Monday of the last complete IST week."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7)
