import json

import pytest

from ai.tools import ALL_TOOLS, build_tool_definitions
from metrics import METRICS
from tests.ai.helpers import ORG, OTHER_ORG, TODAY, W0, W1, make_backends, make_executor
from tests.metrics.fx import ORG as _O  # noqa: F401


def call(ex, name, args):
    out = ex.execute(name, args)
    return out, json.loads(out.content)


def test_tool_schemas_valid_and_complete():
    defs = build_tool_definitions()
    assert [d["name"] for d in defs] == list(ALL_TOOLS)
    assert len(defs) == 8
    for d in defs:
        assert set(d) == {"name", "description", "input_schema"}
        s = d["input_schema"]
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert set(s.get("required", [])) <= set(s["properties"])
        assert "org_id" not in s["properties"]  # the model can never choose the tenant
        json.dumps(d)
    names = {d["name"] for d in defs}
    assert not any("sql" in n or "query" in n for n in names)


def test_metric_enum_equals_registry():
    d = {x["name"]: x for x in build_tool_definitions()}
    assert d["get_metric"]["input_schema"]["properties"]["metric"]["enum"] == sorted(METRICS)
    assert d["get_metric_breakdown"]["input_schema"]["properties"]["metric"]["enum"] == sorted(METRICS)
    groups = {g for s in METRICS.values() for g in s.allowed_group_by}
    assert set(d["get_metric_breakdown"]["input_schema"]["properties"]["group_by"]["enum"]) == groups


def test_get_metric_returns_result_provenance_and_call_id():
    ex = make_executor()
    out, env = call(ex, "get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1})
    assert out.ok and env["call_id"] == out.call_id == "c1"
    r = env["result"]
    assert r["metric"] == "net_revenue" and r["unit"] == "paise" and r["currency"] == "INR"
    assert r["period"] == {"start": W0, "end": W1}
    prov = env["provenance"]
    assert {"metric", "value", "unit", "currency", "period", "filters", "group_by", "data_quality"} <= set(prov)
    assert env["display"]["value"].startswith("₹")
    # value equals the metric layer called directly
    from metrics import DateRange, net_revenue
    from datetime import date
    direct = net_revenue(ORG, DateRange(date.fromisoformat(W0), date.fromisoformat(W1)), None, None, repo=ex.backends.repo)
    assert r["value"] == direct.value


def test_breakdown_and_filters():
    ex = make_executor()
    _, env = call(ex, "get_metric_breakdown", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "group_by": "channel"})
    assert env["ok"] and {g["key"] for g in env["result"]["groups"]} == {"amazon", "shopify"}
    _, env = call(ex, "get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "filters": {"channel": "shopify"}})
    assert env["ok"] and env["result"]["filters"] == {"channel": ["shopify"]}


@pytest.mark.parametrize("name,args,code", [
    ("run_sql", {"sql": "select 1"}, "unknown_tool"),
    ("get_metric", {"metric": "made_up", "start_date": W0, "end_date": W1}, "unknown_metric"),
    ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "bogus": 1}, "unknown_param"),
    ("get_metric", {"metric": "net_revenue", "start_date": W0}, "missing_param"),
    ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "group_by": "channel"}, "unknown_param"),
    ("get_metric_breakdown", {"metric": "mer", "start_date": W0, "end_date": W1, "group_by": "city"}, "invalid_group_by"),
    ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "filters": {"nope": "x"}}, "invalid_filter"),
    ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "filters": {"channel": [1]}}, "invalid_filter"),
    ("get_metric", {"metric": "net_revenue", "start_date": "14/09/2026", "end_date": W1}, "invalid_date"),
    ("get_metric", {"metric": "net_revenue", "start_date": W1, "end_date": W0}, "invalid_range"),
    ("get_metric", {"metric": "net_revenue", "start_date": "2024-01-01", "end_date": W1}, "range_too_large"),
    ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": "2026-12-31"}, "date_out_of_bounds"),
    ("get_metric", {"metric": "net_revenue", "start_date": "1999-01-01", "end_date": W1}, "date_out_of_bounds"),
    ("get_metric", ["not", "a", "dict"], "invalid_input"),
    ("get_root_causes", {"anomaly_id": ""}, "invalid_anomaly_id"),
    ("get_anomalies", {}, "missing_param"),
])
def test_param_validation_rejected_and_audited(name, args, code):
    ex = make_executor()
    out, env = call(ex, name, args)
    assert not out.ok and env["ok"] is False and env["error"]["code"] == code
    assert ex.audit.records[-1].ok is False and ex.audit.get(out.call_id) is None  # failed calls are not citable


@pytest.mark.parametrize("key", ["org_id", "organization_id", "tenant", "sql"])
def test_org_isolation_model_cannot_choose_tenant(key):
    ex = make_executor()
    out, env = call(ex, "get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, key: OTHER_ORG})
    assert not out.ok and env["error"]["code"] == "forbidden_param"
    assert OTHER_ORG in json.dumps(ex.audit.records[-1].args)  # attempt is on the audit trail


def test_executor_binds_org_other_org_has_no_data():
    other = make_executor(org=OTHER_ORG)
    _, env = call(other, "get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1})
    assert env["result"]["value"] == 0 or env["result"]["value"] is None  # repo holds no rows for org-2
    mine = make_executor()
    _, env2 = call(mine, "get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1})
    assert env2["result"]["value"] not in (0, None)


def test_non_metric_tools_bind_org_and_return_data():
    ex = make_executor()
    _, a = call(ex, "get_anomalies", {"week_start": W0})
    assert a["result"]["count"] == 1
    _, rc = call(ex, "get_root_causes", {"anomaly_id": "an-1"})
    assert rc["result"]["drivers"][0]["driver"] == "rto_rate"
    _, rc2 = call(ex, "get_root_causes", {"anomaly_id": "zzz"})
    assert rc2["result"]["status"] == "NOT_FOUND"
    _, m = call(ex, "get_model_results", {})
    assert m["result"]["status"] == "INSUFFICIENT_DATA"
    _, r = call(ex, "get_recommendations", {"week_start": W0})
    assert r["result"]["recommendations"][0]["interval_level"] == 0.8
    _, e = call(ex, "get_experiment_candidates", {})
    assert e["result"]["count"] == 1
    _, d = call(ex, "get_data_quality", {"start_date": W0, "end_date": W1})
    assert d["result"]["status"] == "ok"
    other = make_executor(org=OTHER_ORG)
    assert call(other, "get_anomalies", {"week_start": W0})[1]["result"]["count"] == 0
    assert call(other, "get_model_results", {})[1]["result"]["status"] == "INSUFFICIENT_DATA"


def test_backend_leaking_other_org_is_discarded():
    from ai.mocks import StaticAnomalySource
    b = make_backends()
    b.anomalies = StaticAnomalySource({ORG: [{"id": "x", "org_id": OTHER_ORG}]})
    ex = make_executor(b)
    out, env = call(ex, "get_anomalies", {"week_start": W0})
    assert not out.ok and env["error"]["code"] == "cross_org_data" and OTHER_ORG not in out.content


def test_missing_backends_report_unavailable_not_invented():
    b = make_backends()
    b.models = b.anomalies = b.recommendations = b.data_quality = None
    ex = make_executor(b)
    assert call(ex, "get_anomalies", {"week_start": W0})[1]["result"]["status"] == "UNAVAILABLE"
    assert call(ex, "get_model_results", {})[1]["result"]["status"] == "INSUFFICIENT_DATA"
    assert call(ex, "get_recommendations", {"week_start": W0})[1]["result"]["status"] == "UNAVAILABLE"


def test_empty_data_metric_is_insufficient_not_zero_invented():
    ex = make_executor()
    _, env = call(ex, "get_metric", {"metric": "mer", "start_date": "2026-01-01", "end_date": "2026-01-07"})
    assert env["result"]["value"] is None and env["result"]["data_quality"]["status"] == "insufficient"


def test_group_truncation_is_explicit():
    from ai.config import Limits
    from ai.tools import ToolExecutor
    ex = ToolExecutor(ORG, make_backends(), limits=Limits(max_groups_returned=1), today=lambda: TODAY)
    _, env = call(ex, "get_metric_breakdown", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "group_by": "channel"})
    assert len(env["result"]["groups"]) == 1 and env["result"]["groups_truncated"]["total"] == 2


def test_audit_log_records_every_call_in_order():
    ex = make_executor()
    call(ex, "get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1})
    call(ex, "nope", {})
    recs = ex.audit.tool_calls_json({"k": 1})
    assert [c["call_id"] for c in recs["calls"]] == ["c1", "c2"]
    assert recs["calls"][0]["result"]["metric"] == "net_revenue" and recs["calls"][1]["error"]["code"] == "unknown_tool"
    assert all(c["timestamp"].endswith("+00:00") for c in recs["calls"])
    json.dumps(recs)
