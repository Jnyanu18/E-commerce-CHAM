"""Claude tool definitions and the server-side executor.

Claude -> tool -> metric layer -> validated result -> Claude. There is NO SQL tool and no free-form data
access. org_id is bound by the executor at construction and is never an accepted input. Every call is
validated (schema, enums, date bounds), executed, audited and returned with a run-unique `call_id`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from metrics import DateRange, METRICS, MetricError, MetricSpec, run_metric

from .audit import AuditLog, ToolCallRecord, utcnow
from .config import Limits
from .fmt import format_value
from .sources import AnomalySource, DataQualitySource, ModelSource, RecommendationSource

IST = timezone(timedelta(hours=5, minutes=30))
METRIC_TOOLS = ("get_metric", "get_metric_breakdown")
OTHER_TOOLS = ("get_anomalies", "get_root_causes", "get_model_results", "get_recommendations",
               "get_experiment_candidates", "get_data_quality")
ALL_TOOLS = METRIC_TOOLS + OTHER_TOOLS
FORBIDDEN_PARAMS = {"org_id", "org", "organization", "organization_id", "tenant", "tenant_id", "sql", "query"}
MODEL_STATUSES = ("OK", "WEAK", "INSUFFICIENT_DATA")


@dataclass
class ToolBackends:
    repo: Any  # metrics.MetricsRepository
    anomalies: AnomalySource | None = None
    models: ModelSource | None = None
    recommendations: RecommendationSource | None = None
    data_quality: DataQualitySource | None = None


class ToolInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ToolOutcome:
    call_id: str
    ok: bool
    content: str  # JSON string returned to the model as the tool_result
    record: ToolCallRecord


# ----------------------------------------------------------------------------- definitions
_DATE = {"type": "string", "format": "date", "description": "IST calendar date, YYYY-MM-DD, inclusive"}


def _filters_schema(metrics: dict[str, MetricSpec]) -> dict:
    per = "; ".join(f"{n}: {', '.join(s.allowed_filters)}" for n, s in sorted(metrics.items()))
    return {
        "type": "object",
        "description": f"Optional equality filters {{name: value or [values]}}. Allowed names per metric - {per}.",
        "additionalProperties": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
    }


def build_tool_definitions(metrics: dict[str, MetricSpec] | None = None) -> list[dict[str, Any]]:
    """Anthropic tool-definition format. The metric enum and group_by enum are generated from the registry."""
    metrics = METRICS if metrics is None else metrics
    names = sorted(metrics)
    groups = sorted({g for s in metrics.values() for g in s.allowed_group_by})
    catalogue = " ".join(f"{n} ({s.unit}): {s.description}" for n, s in sorted(metrics.items()))
    base_props = {"metric": {"type": "string", "enum": names}, "start_date": _DATE, "end_date": _DATE,
                  "filters": _filters_schema(metrics)}
    return [
        {
            "name": "get_metric",
            "description": "Get one canonical business metric for a date range (organisation is fixed server-side). "
                           "Money is integer paise. platform_roas is platform-reported, not causal. " + catalogue,
            "input_schema": {"type": "object", "properties": dict(base_props), "required": ["metric", "start_date", "end_date"],
                             "additionalProperties": False},
        },
        {
            "name": "get_metric_breakdown",
            "description": "Same metric, grouped by one allowed dimension (channel, sku, category, city, pincode, payment_method, "
                           "week, platform; allowed dimensions differ by metric and invalid pairs are rejected).",
            "input_schema": {"type": "object", "properties": {**base_props, "group_by": {"type": "string", "enum": groups}},
                             "required": ["metric", "start_date", "end_date", "group_by"], "additionalProperties": False},
        },
        {
            "name": "get_anomalies",
            "description": "Anomalies the detector flagged for the IST week starting week_start (may be an empty list).",
            "input_schema": {"type": "object", "properties": {"week_start": _DATE}, "required": ["week_start"],
                             "additionalProperties": False},
        },
        {
            "name": "get_root_causes",
            "description": "Driver decomposition (root cause) for one anomaly id returned by get_anomalies.",
            "input_schema": {"type": "object", "properties": {"anomaly_id": {"type": "string", "maxLength": 64}},
                             "required": ["anomaly_id"], "additionalProperties": False},
        },
        {
            "name": "get_model_results",
            "description": "Latest marketing-mix model status and diagnostics. Status is OK, WEAK or INSUFFICIENT_DATA; "
                           "do not make causal claims unless OK.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_recommendations",
            "description": "Stored budget recommendations with expected impact (paise), 80% interval and probability of beating the current split.",
            "input_schema": {"type": "object", "properties": {"week_start": _DATE}, "required": ["week_start"],
                             "additionalProperties": False},
        },
        {
            "name": "get_experiment_candidates",
            "description": "Proposed experiments (hypothesis, design, risk, predicted effect) to run where models are unsure.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_data_quality",
            "description": "System data-quality checks for a date range: duplicates, missing COGS, unreconciled settlements, stale integrations.",
            "input_schema": {"type": "object", "properties": {"start_date": _DATE, "end_date": _DATE},
                             "required": ["start_date", "end_date"], "additionalProperties": False},
        },
    ]


# ----------------------------------------------------------------------------- helpers
def _json_default(o: Any):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def normalise_json(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=_json_default))


def _scrub_org(obj: Any, org_id: str) -> Any:
    """Defence in depth: reject results mentioning another org; drop org_id keys from the model-facing payload."""
    if isinstance(obj, dict):
        if "org_id" in obj and str(obj["org_id"]) != str(org_id):
            raise ToolInputError("cross_org_data", "backend returned data for a different organisation; discarded")
        return {k: _scrub_org(v, org_id) for k, v in obj.items() if k != "org_id"}
    if isinstance(obj, list):
        return [_scrub_org(v, org_id) for v in obj]
    return obj


def result_is_insufficient(result: dict[str, Any] | None) -> bool:
    """True when a stored result says data was missing/insufficient (drives the mandatory disclosure sentence)."""
    if not result:
        return True
    dq = result.get("data_quality")
    if isinstance(dq, dict) and dq.get("status") == "insufficient":
        return True
    if "metric" in result and "value" in result and result["value"] is None:
        return True
    return result.get("status") in ("INSUFFICIENT_DATA", "UNAVAILABLE", "NOT_FOUND", "insufficient")


class ToolExecutor:
    def __init__(self, org_id: str, backends: ToolBackends, *, audit: AuditLog | None = None,
                 metrics: dict[str, MetricSpec] | None = None, limits: Limits | None = None,
                 today: Callable[[], date] | None = None):
        self.org_id = str(org_id)  # bound server-side; never read from model input
        self.backends = backends
        self.audit = audit or AuditLog(org_id=self.org_id)
        self.metrics = METRICS if metrics is None else metrics
        self.limits = limits or Limits()
        self._today = today or (lambda: datetime.now(IST).date())
        self.definitions = build_tool_definitions(self.metrics)
        self._handlers: dict[str, Callable[[dict], tuple[dict, dict]]] = {
            "get_metric": lambda a: self._metric(a, grouped=False),
            "get_metric_breakdown": lambda a: self._metric(a, grouped=True),
            "get_anomalies": self._anomalies,
            "get_root_causes": self._root_causes,
            "get_model_results": self._models,
            "get_recommendations": self._recs,
            "get_experiment_candidates": self._experiments,
            "get_data_quality": self._dq,
        }

    # ---- public
    def execute(self, name: str, args: Any) -> ToolOutcome:
        call_id = self.audit.next_call_id()
        t0 = time.perf_counter()
        ts = utcnow().isoformat()
        safe_args = normalise_json(args) if isinstance(args, dict) else {"_raw": str(args)[:200]}
        try:
            if name not in self._handlers:
                raise ToolInputError("unknown_tool", f"unknown tool {name!r}; available: {', '.join(ALL_TOOLS)}")
            if not isinstance(args, dict):
                raise ToolInputError("invalid_input", "tool input must be a JSON object")
            bad = FORBIDDEN_PARAMS & set(args)
            if bad:
                raise ToolInputError("forbidden_param", f"{sorted(bad)} cannot be supplied: the organisation is fixed server-side "
                                                        "and there is no SQL or free-form data access")
            result, display = self._handlers[name](args)
            result = _scrub_org(normalise_json(result), self.org_id)
            rec = ToolCallRecord(call_id, name, safe_args, True, result, None, ts, (time.perf_counter() - t0) * 1000)
            envelope = {"call_id": call_id, "tool": name, "ok": True, "result": result, "display": display,
                        "provenance": self._provenance(name, result)}
        except ToolInputError as e:
            rec, envelope = self._failed(call_id, name, safe_args, ts, t0, e.code, str(e))
        except MetricError as e:
            rec, envelope = self._failed(call_id, name, safe_args, ts, t0, "invalid_metric_request", str(e))
        except Exception as e:  # backend failure: never leak internals, never crash the brief
            rec, envelope = self._failed(call_id, name, safe_args, ts, t0, "tool_failed", f"{type(e).__name__} while running the tool")
        self.audit.add(rec)
        return ToolOutcome(call_id, rec.ok, json.dumps(envelope, ensure_ascii=False), rec)

    @staticmethod
    def _failed(call_id, name, args, ts, t0, code, message):
        err = {"code": code, "message": message}
        rec = ToolCallRecord(call_id, name, args, False, None, err, ts, (time.perf_counter() - t0) * 1000)
        return rec, {"call_id": call_id, "tool": name, "ok": False, "error": err}

    # ---- validation
    @staticmethod
    def _check_keys(args: dict, allowed: set[str], required: set[str]) -> None:
        unknown = set(args) - allowed
        if unknown:
            raise ToolInputError("unknown_param", f"unknown parameter(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
        missing = required - set(args)
        if missing:
            raise ToolInputError("missing_param", f"missing required parameter(s) {sorted(missing)}")

    def _date(self, args: dict, key: str) -> date:
        v = args.get(key)
        if not isinstance(v, str) or len(v) != 10:
            raise ToolInputError("invalid_date", f"{key} must be a YYYY-MM-DD string")
        try:
            d = date.fromisoformat(v)
        except ValueError:
            raise ToolInputError("invalid_date", f"{key}={v!r} is not a valid YYYY-MM-DD date") from None
        if d < date.fromisoformat(self.limits.min_date):
            raise ToolInputError("date_out_of_bounds", f"{key} is before {self.limits.min_date}")
        if d > self._today():
            raise ToolInputError("date_out_of_bounds", f"{key}={v} is in the future")
        return d

    def _range(self, args: dict) -> DateRange:
        s, e = self._date(args, "start_date"), self._date(args, "end_date")
        if s > e:
            raise ToolInputError("invalid_range", "start_date is after end_date")
        if (e - s).days + 1 > self.limits.max_range_days:
            raise ToolInputError("range_too_large", f"range exceeds {self.limits.max_range_days} days")
        return DateRange(s, e)

    def _filters(self, spec: MetricSpec, raw: Any) -> dict[str, list[str]] | None:
        if raw is None or raw == {}:
            return None
        if not isinstance(raw, dict):
            raise ToolInputError("invalid_filters", "filters must be an object")
        out: dict[str, list[str]] = {}
        for k, v in raw.items():
            if k not in spec.allowed_filters:
                raise ToolInputError("invalid_filter", f"filter {k!r} is not allowed for {spec.name}; allowed: {list(spec.allowed_filters)}")
            vals = [v] if isinstance(v, str) else v
            if not isinstance(vals, list) or not vals or not all(isinstance(x, str) for x in vals):
                raise ToolInputError("invalid_filter", f"filter {k!r} must be a string or non-empty list of strings")
            if len(vals) > self.limits.max_filter_values or any(len(x) > self.limits.max_filter_value_len for x in vals):
                raise ToolInputError("invalid_filter", f"filter {k!r} has too many or too long values")
            out[k] = vals
        return out

    # ---- handlers: each returns (result, display)
    def _metric(self, args: dict, *, grouped: bool):
        allowed = {"metric", "start_date", "end_date", "filters"} | ({"group_by"} if grouped else set())
        self._check_keys(args, allowed, {"metric", "start_date", "end_date"} | ({"group_by"} if grouped else set()))
        name = args["metric"]
        if not isinstance(name, str) or name not in self.metrics:
            raise ToolInputError("unknown_metric", f"unknown metric {name!r}; allowed: {sorted(self.metrics)}")
        spec = self.metrics[name]
        group_by = args.get("group_by") if grouped else None
        if grouped and (not isinstance(group_by, str) or group_by not in spec.allowed_group_by):
            raise ToolInputError("invalid_group_by", f"group_by {group_by!r} is not allowed for {name}; allowed: {list(spec.allowed_group_by)}")
        dr = self._range(args)
        filters = self._filters(spec, args.get("filters"))
        res = run_metric(name, self.org_id, dr, filters, group_by, repo=self.backends.repo).to_dict()
        total = len(res["groups"])
        if total > self.limits.max_groups_returned:
            keep = sorted(res["groups"], key=lambda g: (g["value"] is None, -abs(g["value"] or 0), g["key"]))
            res["groups"] = keep[: self.limits.max_groups_returned]
            res["groups_truncated"] = {"shown": len(res["groups"]), "total": total, "order": "largest absolute value first"}
        display = {"value": format_value(res["value"], res["unit"]),
                   "groups": {g["key"]: format_value(g["value"], res["unit"]) for g in res["groups"]}}
        return res, display

    @staticmethod
    def _unavailable(backend, what: str):
        if backend is None:
            return {"status": "UNAVAILABLE", "reason": f"{what} is not available for this organisation"}
        return None

    def _anomalies(self, args):
        self._check_keys(args, {"week_start"}, {"week_start"})
        ws = self._date(args, "week_start")
        if (miss := self._unavailable(self.backends.anomalies, "anomaly detection")):
            return miss, {}
        items = list(self.backends.anomalies.anomalies(self.org_id, ws))
        return {"status": "OK", "week_start": ws.isoformat(), "count": len(items), "anomalies": items}, {}

    def _root_causes(self, args):
        self._check_keys(args, {"anomaly_id"}, {"anomaly_id"})
        aid = args["anomaly_id"]
        if not isinstance(aid, str) or not aid or len(aid) > 64:
            raise ToolInputError("invalid_anomaly_id", "anomaly_id must be a non-empty string up to 64 characters")
        if (miss := self._unavailable(self.backends.anomalies, "root-cause analysis")):
            return miss, {}
        rc = self.backends.anomalies.root_causes(self.org_id, aid)
        if rc is None:
            return {"status": "NOT_FOUND", "anomaly_id": aid, "reason": "no root-cause analysis stored for this anomaly"}, {}
        return {"status": "OK", "anomaly_id": aid, **rc}, {}

    def _models(self, args):
        self._check_keys(args, set(), set())
        if (miss := self._unavailable(self.backends.models, "model results")):
            return {**miss, "status": "INSUFFICIENT_DATA"}, {}
        res = dict(self.backends.models.model_results(self.org_id))
        if res.get("status") not in MODEL_STATUSES:
            res["status"] = "INSUFFICIENT_DATA"
            res.setdefault("reason", "model status missing or unrecognised")
        return res, {}

    def _recs(self, args):
        self._check_keys(args, {"week_start"}, {"week_start"})
        ws = self._date(args, "week_start")
        if (miss := self._unavailable(self.backends.recommendations, "recommendations")):
            return miss, {}
        recs = [dict(r) for r in self.backends.recommendations.recommendations(self.org_id, ws)][:20]
        for r in recs:
            if r.get("ci_low_paise") is not None and r.get("ci_high_paise") is not None:
                r.setdefault("interval_level", 0.8)  # recommendations.ci_* are 80% intervals (CONTRACTS section 6)
        out = {"status": "OK" if recs else "INSUFFICIENT_DATA", "week_start": ws.isoformat(), "count": len(recs), "recommendations": recs}
        if not recs:
            out["reason"] = "no stored recommendations for this week"
        return out, {}

    def _experiments(self, args):
        self._check_keys(args, set(), set())
        if (miss := self._unavailable(self.backends.recommendations, "experiment candidates")):
            return miss, {}
        ex = [dict(e) for e in self.backends.recommendations.experiment_candidates(self.org_id)][:20]
        out = {"status": "OK" if ex else "INSUFFICIENT_DATA", "count": len(ex), "experiments": ex}
        if not ex:
            out["reason"] = "no experiment candidates stored"
        return out, {}

    def _dq(self, args):
        self._check_keys(args, {"start_date", "end_date"}, {"start_date", "end_date"})
        dr = self._range(args)
        if (miss := self._unavailable(self.backends.data_quality, "data-quality checks")):
            return miss, {}
        res = dict(self.backends.data_quality.data_quality(self.org_id, dr.start, dr.end))
        res.setdefault("status", "ok")
        res["period"] = dr.to_dict()
        return res, {}

    @staticmethod
    def _provenance(name: str, result: dict) -> dict:
        if "metric" in result:
            return {k: result.get(k) for k in ("metric", "value", "unit", "currency", "period", "filters", "group_by", "data_quality")}
        prov: dict[str, Any] = {"tool": name, "status": result.get("status")}
        for k in ("week_start", "period", "reason"):
            if k in result:
                prov[k] = result[k]
        return prov
