"""Worker job handlers for the intelligence layer, signature ``(job: dict, conn) -> None`` (same style as ledger.jobs).

Registration (done by main, not here):
    from intelligence.jobs import handle_detect_anomalies, handle_mmm_run
    handlers = {"detect_anomalies": handle_detect_anomalies, "mmm_run": handle_mmm_run}

``detect_anomalies`` payload: ``{"week_start": "YYYY-MM-DD"?}`` (Monday; default = last complete IST week).
``mmm_run`` payload: ``{"end": "YYYY-MM-DD"?, "weeks": 104?}`` (``end`` = a Sunday; default = last complete IST week).
Both read ONLY through the metric layer (`MetricLayerSource` over `PostgresRepository`). The `run_*` functions return a
summary dict for tests/logging; the handlers discard it and raise on batch-level failure so worker.queue retries.

MMM sampler cost on the handler path (`HANDLER_FIT`): 2 chains x 500 draws after 500 tune, target_accept 0.95. Two chains
are the minimum for R-hat; the gate still demands R-hat <= 1.05, bulk ESS >= 200 and 80% PPC coverage in [0.6, 0.95] so a
cheaper run simply fails closed (INSUFFICIENT_DATA) instead of reporting a weak model. The library default (4 x 1000 after
1000 tune) is for offline use. On a box without a C++ compiler pytensor runs in pure Python and sampling takes minutes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from metrics.repository import PostgresRepository

from anomaly import AnomalyConfig, detect_many
from models import run_mmm
from models.fit_pymc import FitConfig
from models.gate import GateConfig, load_mmm_data

from .common import is_insufficient
from .experiments import DesignConfig, design_geo_holdout, load_city_sales, persist_experiment
from .optimiser import Constraints, optimise, persist_recommendation
from .persistence import PsycopgIntelRepo, jsonable, persist_mmm_result
from .rootcause import explain_revenue_change, explain_roas_change
from .sources import MetricLayerSource

log = logging.getLogger("intelligence.jobs")
IST = timezone(timedelta(hours=5, minutes=30))

ANOMALY_METRICS = ("net_revenue", "orders", "aov", "contribution_margin", "mer", "blended_cac", "expected_rto_loss", "ad_spend")
ANOMALY_WINDOW_DAYS = 120
REVENUE_CHAIN_METRICS = {"net_revenue", "orders", "aov"}
ROAS_CHAIN_METRICS = {"ad_spend", "mer"}
HANDLER_FIT = FitConfig(draws=500, tune=500, chains=2)


def _org(job: dict) -> str:
    org = job.get("org_id")
    if not org:
        raise ValueError("intelligence job requires org_id")
    return str(org)


def _payload(job: dict) -> dict:
    p = job.get("payload") or {}
    return json.loads(p) if isinstance(p, str) else dict(p)


def last_complete_week_start(today: date | None = None) -> date:
    today = today or datetime.now(IST).date()
    return today - timedelta(days=today.weekday()) - timedelta(days=7)


def anomaly_id(org_id: str, metric: str, day: date | str) -> str:
    """Stable across reruns: depends only on org, metric and date."""
    d = day.isoformat() if isinstance(day, date) else str(day)
    return "anm_" + hashlib.sha256(f"{org_id}|{metric}|{d}".encode()).hexdigest()[:16]


def _insufficient_rc(rc: dict) -> dict:
    return {"status": "INSUFFICIENT_DATA", "reason": rc.get("reason"), "fallback": rc.get("fallback", "descriptive")}


def _rc_json(rc: dict) -> dict:
    if is_insufficient(rc):
        return _insufficient_rc(rc)
    return jsonable({"status": "ok", "method": rc["method"], "metric": rc["metric"], "label": rc.get("label"),
                     "total_change": rc["total_change"], "from_value": rc["from_value"], "to_value": rc["to_value"],
                     "period0": rc.get("period0"), "period1": rc.get("period1"),
                     "drivers": [d.to_dict() for d in rc["drivers"]]})


def run_detect_anomalies(job: dict, conn: Any) -> dict:
    org = _org(job)
    p = _payload(job)
    ws = date.fromisoformat(p["week_start"]) if p.get("week_start") else last_complete_week_start()
    if ws.weekday() != 0:
        raise ValueError(f"week_start {ws} is not a Monday")
    we = ws + timedelta(days=6)
    start = we - timedelta(days=ANOMALY_WINDOW_DAYS - 1)
    src = MetricLayerSource(PostgresRepository(conn))
    t0 = time.time()
    first = conn.execute("select min(ordered_date_ist) from orders where org_id = %s", (org,)).fetchone()[0]
    if first is not None:  # never score the pre-history: days before the first order are not zero-revenue days
        start = max(start, first)
    results = detect_many(src, org, start, we, ANOMALY_METRICS, AnomalyConfig())
    prev = (ws - timedelta(days=7), ws - timedelta(days=1))
    cur = (ws, we)
    rc_cache: dict[str, dict] = {}

    def rc_for(kind: str) -> dict:
        if kind not in rc_cache:
            fn = explain_revenue_change if kind == "revenue" else explain_roas_change
            rc_cache[kind] = _rc_json(fn(src, org, prev, cur))
        return rc_cache[kind]

    records, skipped = [], {}
    for metric, res in results.items():
        if is_insufficient(res):
            skipped[metric] = res["reason"]
            continue
        for a in res["anomalies"]:
            if not (ws <= a.date <= we):
                continue
            rec = {"id": anomaly_id(org, metric, a.date), "metric": metric, "date": a.date.isoformat(), "direction": a.direction,
                   "robust_z": round(a.robust_z, 3), "severity": a.severity, "week_start": ws.isoformat(),
                   "observed": a.observed, "expected": a.expected, "residual": a.residual, "favourable": a.favourable}
            if metric in REVENUE_CHAIN_METRICS:
                rec["root_cause"] = rc_for("revenue")
            elif metric in ROAS_CHAIN_METRICS:
                rec["root_cause"] = rc_for("roas")
            records.append(rec)
    records.sort(key=lambda r: (-abs(r["robust_z"]), r["id"]))
    with conn.transaction():
        # Touches ONLY week_start / anomalies: brief_markdown, tool_calls, emailed_at, generated_at are never overwritten.
        conn.execute("insert into insights (org_id, week_start, anomalies) values (%s, %s, %s) "
                     "on conflict (org_id, week_start) do update set anomalies = excluded.anomalies",
                     (org, ws, Jsonb(jsonable(records))))
    log.info("detect_anomalies org=%s week=%s anomalies=%d skipped_metrics=%d in %.1fs", org, ws, len(records), len(skipped), time.time() - t0)
    return {"week_start": ws.isoformat(), "anomalies": records, "skipped_metrics": skipped}


def handle_detect_anomalies(job: dict, conn: Any) -> None:
    run_detect_anomalies(job, conn)


def _platforms(src: MetricLayerSource, org: str, start: date, end: date) -> list[str]:
    from metrics import METRICS
    res = METRICS["ad_spend"].callable(org, (start, end), None, "platform", repo=src.repo)
    return sorted(g.key for g in res.groups if g.value)


def _fill_after_first_spend(values: list) -> list:
    """Weeks after a platform's first spend row with no row = zero spend (it stopped/paused); weeks before it stay missing."""
    out, seen = [], False
    for v in values:
        seen = seen or v is not None
        out.append(0.0 if (v is None and seen) else v)
    return out


def run_mmm_job(job: dict, conn: Any, *, fit: Any = None, fit_cfg: FitConfig = HANDLER_FIT,
                gate_cfg: GateConfig = GateConfig(), today: date | None = None) -> dict:
    org = _org(job)
    p = _payload(job)
    end = date.fromisoformat(p["end"]) if p.get("end") else last_complete_week_start(today) + timedelta(days=6)
    if end.weekday() != 6:
        raise ValueError(f"end {end} is not a Sunday")
    weeks = int(p.get("weeks", 104))
    start = end - timedelta(days=7 * weeks - 1)
    repo = PostgresRepository(conn)
    src = MetricLayerSource(repo)
    irepo = PsycopgIntelRepo(conn)
    t0 = time.time()
    log.info("mmm_run org=%s loading weekly data %s..%s", org, start, end)
    channels = _platforms(src, org, start, end)
    summary: dict[str, Any] = {"channels": channels, "recommendations": [], "experiment_id": None}
    if channels:
        data = load_mmm_data(src, org, start, end, channels)
        data.spend_paise = {c: _fill_after_first_spend(v) for c, v in data.spend_paise.items()}
        # drop leading weeks before any spend/order history so the gate judges the real history length
        first = min((next((i for i, v in enumerate(vals) if v is not None), len(vals)) for vals in data.spend_paise.values()), default=0)
        if first:
            data.weeks = data.weeks[first:]
            data.outcome_paise = data.outcome_paise[first:]
            data.spend_paise = {c: v[first:] for c, v in data.spend_paise.items()}
        log.info("mmm_run org=%s %d weeks x %d channels loaded in %.1fs; running gate", org, len(data.weeks), len(channels), time.time() - t0)
        # sampling starts only if the data gate passes; run_mmm logs below via the wrapped fit
        def logged_fit(*a, **k):
            t = time.time()
            log.info("mmm_run org=%s sampling: chains=%d draws=%d tune=%d (slow without a C++ compiler)", org, fit_cfg.chains, fit_cfg.draws, fit_cfg.tune)
            if fit is not None:
                out = fit(*a, **k)
            else:
                from models.fit_pymc import fit_pymc
                out = fit_pymc(*a, **k)
            log.info("mmm_run org=%s sampling done in %.1fs; diagnostics=%s", org, time.time() - t, out.diagnostics)
            return out
        result = run_mmm(data, gate_cfg, fit_cfg, fit=logged_fit)
    else:
        from intelligence.common import insufficient
        data = None
        result = insufficient("no ad spend rows in the window; nothing to model", "descriptive")
    run_id = persist_mmm_result(irepo, org, result)
    summary.update(model_run_id=run_id, mmm_status=result["status"], mmm_reason=result.get("reason"))
    log.info("mmm_run org=%s model_run=%s status=%s %s", org, run_id, result["status"], result.get("reason", ""))

    exp_channel: str | None = None
    reason = result.get("reason")
    if result["status"] == "ok":
        cur = {}
        for c in channels:
            s = src.series(f"ad_spend:{c}", org, end - timedelta(days=27), end, "week")
            vals = [v or 0.0 for v in s.values]
            cur[c] = int(sum(vals) / max(len(vals), 1))
        rec = optimise(result, cur, Constraints())
        if is_insufficient(rec):
            reason = rec["reason"]
            summary["recommendations"].append({"status": "INSUFFICIENT_DATA", "reason": rec["reason"]})
        else:
            if rec["type"] == "run_experiment":
                exp_channel = rec["payload"]["suggested_experiment"]["channel"]
                reason = rec["payload"]["reason"]
            else:
                rid = persist_recommendation(irepo, org, rec, run_id)
                summary["recommendations"].append({"id": rid, "type": rec["type"]})
    if result["status"] != "ok" or exp_channel:
        if exp_channel is None:
            tot = {c: sum(v or 0 for v in data.spend_paise[c]) for c in channels} if data else {}
            exp_channel = max(tot, key=tot.get) if tot else "meta"
        cs = load_city_sales(repo, org, end - timedelta(days=7 * 26 - 1), end)
        today_ = today or datetime.now(IST).date()
        launch = today_ - timedelta(days=today_.weekday()) + timedelta(days=14)
        design = design_geo_holdout(cs, exp_channel, launch, DesignConfig())
        if is_insufficient(design):
            summary["experiment_design"] = {"status": "INSUFFICIENT_DATA", "reason": design["reason"]}
            log.warning("mmm_run org=%s no experiment design: %s", org, design["reason"])
        else:
            eid = persist_experiment(irepo, org, design)
            payload = {"reason": reason, "design": {k: design[k] for k in ("channel", "treatment_cities", "control_cities", "test_weeks",
                                                                            "risk", "uncertainty", "hypothesis", "measurement_plan",
                                                                            "ads_manager_instructions", "assumptions", "warnings")},
                       "start_date": design["start_date"], "end_date": design["end_date"], "mmm_status": result["status"]}
            rid = persist_recommendation(irepo, org, {"status": "ok", "type": "run_experiment", "payload": payload,
                                                      "expected_impact_paise": None, "ci_low_paise": None, "ci_high_paise": None,
                                                      "prob_beats_current": None}, run_id if result["status"] == "ok" else run_id, eid)
            summary["experiment_id"] = eid
            summary["recommendations"].append({"id": rid, "type": "run_experiment"})
    log.info("mmm_run org=%s finished in %.1fs", org, time.time() - t0)
    return summary


def handle_mmm_run(job: dict, conn: Any) -> None:
    with conn.transaction():
        run_mmm_job(job, conn)
