"""End-to-end brief generation: scripted provider + REAL metric layer over the metrics agent's hand-built fixtures."""
import json
from datetime import date

import pytest

from ai.brief import build_fallback_summary, default_plan, generate_brief
from ai.config import FALLBACK_MARKER, NO_DATA_SENTENCE, PROMPT_VERSION, BriefConfig, Limits
from ai.fmt import format_inr
from ai.provider import ProviderError, ScriptedProvider, text_response, tool_response, tool_results_by_call_id
from ai.recommendations import format_recommendation
from ai.store import InMemoryInsightStore
from ai.tools import ToolExecutor
from ai.validator import validate_brief
from tests.ai.helpers import EXP, MODEL_INSUFFICIENT, MODEL_OK, ORG, OTHER_ORG, REC, TODAY, W0, W1, WEEK, make_backends

TOOLS_STEP = tool_response(
    ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1}),
    ("get_metric", {"metric": "contribution_margin", "start_date": W0, "end_date": W1}),
    ("get_metric_breakdown", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "group_by": "channel"}),
    ("get_model_results", {}),
    ("get_recommendations", {"week_start": W0}),
    ("get_experiment_candidates", {}),
    ("get_anomalies", {"week_start": W0}),
)


def by_tool(msgs):
    res = {k: v for k, v in tool_results_by_call_id(msgs).items() if v["ok"]}
    return {e["tool"] + (":" + str(e["result"].get("group_by", "")) if e["tool"].startswith("get_metric") and e["result"].get("group_by") else ""): e
            for e in res.values()}


def good_brief(msgs, model_line=None):
    t = by_tool(msgs)
    rev, cm, ch = t["get_metric"], None, t["get_metric_breakdown:channel"]
    recs, exps, model, an = t["get_recommendations"], t["get_experiment_candidates"], t["get_model_results"], t["get_anomalies"]
    # get_metric appears twice; pick by metric name
    allres = {k: v for k, v in tool_results_by_call_id(msgs).items() if v["ok"]}
    rev = next(e for e in allres.values() if e["tool"] == "get_metric" and e["result"]["metric"] == "net_revenue")
    cm = next(e for e in allres.values() if e["tool"] == "get_metric" and e["result"]["metric"] == "contribution_margin")
    groups = ", ".join(f"{g['key']} {ch['display']['groups'][g['key']]}" for g in ch["result"]["groups"])
    return f"""## What happened
Net revenue for {W0} to {W1} was {rev['display']['value']} [{rev['call_id']}], and contribution margin was {cm['display']['value']} [{cm['call_id']}].
By channel: {groups} [{ch['call_id']}].
## Why
The detector flagged contribution_margin down on 2026-09-17 with a robust z of -3.4 [{an['call_id']}].
The marketing model status is {model['result']['status']} [{model['call_id']}] ({model['result']['reason']}), so no causal claim is made about any channel.
{NO_DATA_SENTENCE}
## Top 3 actions
{format_recommendation(recs['result']['recommendations'][0], recs['call_id'], 1)}
## Experiments
- Hypothesis: {exps['result']['experiments'][0]['hypothesis'].rstrip('.')} [{exps['call_id']}]; risk {format_inr(exps['result']['experiments'][0]['risk_paise'])} [{exps['call_id']}].
"""


def run(provider, backends=None, store=None, org=ORG, **cfg):
    return generate_brief(org, WEEK, provider=provider, backends=backends or make_backends(), store=store,
                          config=BriefConfig(**cfg) if cfg else None, today=lambda: TODAY)


def test_end_to_end_happy_path_with_real_metric_layer():
    store = InMemoryInsightStore()
    p = ScriptedProvider([TOOLS_STEP, lambda msgs: text_response(good_brief(msgs))])
    r = run(p, store=store)
    assert r.mode == "narrative" and r.validation.ok, r.validation.feedback()
    assert r.attempts == 1 and r.fallback_reason is None
    # numbers in the brief are the metric layer's numbers
    from metrics import DateRange, net_revenue
    direct = net_revenue(ORG, DateRange(date.fromisoformat(W0), date.fromisoformat(W1)), None, None, repo=make_backends().repo)
    assert format_inr(direct.value) in r.markdown
    # model saw tool definitions and the system prompt; and never raw data outside tool results
    req = p.requests[0]
    assert len(req["tools"]) == 8 and "ONLY" not in req["messages"][0]["content"]
    # persisted
    saved = store.get(ORG, WEEK)
    assert saved.brief_markdown == r.markdown
    meta = saved.tool_calls["meta"]
    assert meta["prompt_version"] == PROMPT_VERSION and meta["model_id"] == "scripted-mock" and meta["mode"] == "narrative"
    assert [a["id"] for a in saved.anomalies] == ["an-1"]


def test_audit_trail_complete_and_matches_calls_made():
    r = run(ScriptedProvider([TOOLS_STEP, lambda msgs: text_response(good_brief(msgs))]))
    calls = r.insight.tool_calls["calls"]
    assert [c["call_id"] for c in calls] == [f"c{i}" for i in range(1, 8)]
    assert [c["tool"] for c in calls] == ["get_metric", "get_metric", "get_metric_breakdown", "get_model_results",
                                          "get_recommendations", "get_experiment_candidates", "get_anomalies"]
    for c in calls:
        assert c["ok"] and c["result"] and c["timestamp"] and set(c) >= {"args", "result", "call_id", "timestamp"}
    # every citation in the brief resolves to a stored result
    import re
    cited = set(re.findall(r"\[(c\d+)\]", r.markdown))
    assert cited and cited <= {c["call_id"] for c in calls}
    blob = json.dumps(r.insight.tool_calls)
    assert "sk-ant" not in blob and "ANTHROPIC" not in blob


def test_hallucinated_number_is_rejected_then_repaired():
    def bad(msgs):
        t = by_tool(msgs)
        rev = next(e for e in tool_results_by_call_id(msgs).values() if e["tool"] == "get_metric" and e["ok"])
        return text_response(good_brief(msgs).replace(rev["display"]["value"], "₹48.75 lakh", 1))

    p = ScriptedProvider([TOOLS_STEP, bad, lambda msgs: text_response(good_brief(msgs))])
    r = run(p)
    assert r.mode == "narrative" and r.attempts == 2
    feedback = p.requests[2]["messages"][-1]["content"]
    assert "number_not_in_cited_result" in feedback and "48.75 lakh" in feedback and "attempt 1 of 2" in feedback
    assert "48.75" not in r.markdown


def test_fabricated_confidence_interval_rejected_and_falls_back_after_bounded_retries():
    def invented(msgs):
        return text_response(good_brief(msgs).replace("## Experiments", "We are 90% confident the shift adds ₹5.00 lakh to ₹8.00 lakh (90% interval) [c5].\n## Experiments"))

    p = ScriptedProvider([TOOLS_STEP, invented, invented, invented])
    r = run(p)
    assert r.mode == "fallback" and r.attempts == 3 and "validation failed" in r.fallback_reason
    assert "interval_not_in_result" in r.fallback_reason or "number_not_in_cited_result" in r.fallback_reason
    assert FALLBACK_MARKER in r.markdown and "90%" not in r.markdown and "₹5.00 lakh" not in r.markdown
    assert r.validation.ok, r.validation.feedback()  # the fallback itself is fully traceable
    assert r.insight.tool_calls["meta"]["mode"] == "fallback"
    assert len(p.requests) == 4  # 1 tool turn + 3 attempts: bounded


def test_fallback_summary_content_is_deterministic_and_cites():
    mk = lambda: run(ScriptedProvider([TOOLS_STEP, text_response("garbage with 12345 numbers")] + [text_response("garbage 99")] * 2))
    a, b = mk(), mk()
    assert a.mode == "fallback"
    strip = lambda s: s  # ids are call_ids c1.. so output is reproducible
    assert strip(a.markdown) == strip(b.markdown)
    assert "Net revenue".lower() in a.markdown.lower() or "net_revenue" in a.markdown
    assert "INSUFFICIENT_DATA" in a.markdown and "Causal claims are not supported" in a.markdown
    assert format_inr(1_250_000) in a.markdown and "[c5]" in a.markdown


def test_fallback_runs_default_plan_when_model_never_called_tools():
    r = run(ScriptedProvider([text_response("## What happened\nAll good.\n## Why\n-\n## Top 3 actions\n-\n## Experiments\n-\n")] * 3))
    assert r.mode == "fallback"
    tools = [c["tool"] for c in r.insight.tool_calls["calls"]]
    assert tools == [n for n, _ in default_plan(WEEK)]
    assert r.validation.ok, r.validation.feedback()


def test_no_tool_calls_means_narrative_rejected():
    ok_looking = "## What happened\nQuiet.\n## Why\n-\n## Top 3 actions\n-\n## Experiments\n-\n"
    r = run(ScriptedProvider([text_response(ok_looking)] * 3))
    assert r.mode == "fallback" and "no_tool_calls" in r.fallback_reason


def test_provider_error_falls_back():
    class Boom:
        model_id = "boom"
        def complete(self, **kw):
            raise ProviderError("network down")
    r = run(Boom())
    assert r.mode == "fallback" and "network down" in r.fallback_reason and r.validation.ok


def test_model_cannot_pick_another_org_end_to_end():
    attack = tool_response(("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "org_id": OTHER_ORG}),
                           ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1}))
    p = ScriptedProvider([attack, TOOLS_STEP, lambda msgs: text_response(good_brief(msgs))])
    r = run(p)
    assert r.mode == "narrative"
    first = r.insight.tool_calls["calls"][0]
    assert first["ok"] is False and first["error"]["code"] == "forbidden_param" and first["args"]["org_id"] == OTHER_ORG
    # all successful metric calls were executed for the bound org (org-2 has no data, org-1 does)
    assert r.insight.tool_calls["calls"][1]["result"]["value"] not in (0, None)


def test_missing_data_path_requires_and_accepts_the_insufficient_sentence():
    empty_org = "org-3"
    b = make_backends(org=empty_org)
    step = tool_response(("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1}),
                         ("get_metric", {"metric": "mer", "start_date": W0, "end_date": W1}))
    without = "## What happened\nRevenue was fine.\n## Why\n-\n## Top 3 actions\n-\n## Experiments\n-\n"
    with_ = f"## What happened\n{NO_DATA_SENTENCE}\n## Why\n{NO_DATA_SENTENCE}\n## Top 3 actions\n{NO_DATA_SENTENCE}\n## Experiments\n{NO_DATA_SENTENCE}\n"
    p = ScriptedProvider([step, text_response(without), text_response(with_)])
    r = run(p, backends=b, org=empty_org)
    assert r.mode == "narrative" and r.attempts == 2
    assert "missing_disclosure" in p.requests[2]["messages"][-1]["content"]


def test_weak_model_must_be_stated_plainly():
    step = tool_response(("get_model_results", {}))
    omit = "## What happened\nQuiet.\n## Why\n-\n## Top 3 actions\n-\n## Experiments\n-\n"
    p = ScriptedProvider([step, text_response(omit), text_response(omit), text_response(omit)])
    r = run(p, backends=make_backends(model=MODEL_INSUFFICIENT))
    assert r.mode == "fallback" and "missing_disclosure" in r.fallback_reason


def test_tool_budget_and_turn_limits_are_bounded():
    many = tool_response(*[("get_model_results", {})] * 5)
    p = ScriptedProvider([many, many, many, many])
    r = run(p, limits=Limits(max_tool_calls_per_brief=6, max_turns=4))
    assert r.mode == "fallback" and "turn limit" in r.fallback_reason
    assert len([c for c in r.insight.tool_calls["calls"] if c["tool"] == "get_model_results"]) <= 6 + 9  # + default plan
    assert len(p.requests) <= 4


def test_truncated_output_is_not_accepted():
    from ai.provider import ProviderResponse
    trunc = lambda msgs: ProviderResponse([{"type": "text", "text": good_brief(msgs)}], "max_tokens")
    p = ScriptedProvider([TOOLS_STEP, trunc, lambda msgs: text_response(good_brief(msgs))])
    r = run(p)
    assert r.mode == "narrative" and r.attempts == 2


def test_recommendation_formatting_from_payload_only():
    txt = format_recommendation({**REC, "interval_level": 0.8}, "c9", 1)
    assert txt.startswith("1. **Action:** Move ₹50,000.00 of ad budget from meta to google [c9].")
    assert "Expected impact ₹12,500.00 [c9]" in txt and "80% interval ₹4,000.00 to ₹21,000.00 [c9]" in txt
    assert "72% [c9]" in txt and "flatter marginal response" in txt
    # no interval / no probability stored -> says so, invents nothing
    bare = format_recommendation({"type": "budget_shift", "payload": {"from_channel": "a", "to_channel": "b", "shift_paise": 100000},
                                  "expected_impact_paise": 500000}, "c2")
    assert "uncertainty range is stored" in bare and "no probability" in bare and "%" not in bare
    # missing impact -> the standard insufficient-data sentence
    none = format_recommendation({"type": "run_experiment", "payload": {"channel": "meta"}}, "c3")
    assert NO_DATA_SENTENCE in none and "geo holdout experiment on meta" in none
    # unknown type with free text containing sentence breaks stays one sentence
    odd = format_recommendation({"type": "mystery", "payload": {"summary": "Do X. Then Y"}}, "c4")
    assert "Do X; Then Y" in odd


def test_recommendation_blocks_pass_validator():
    from ai.audit import AuditLog
    ex = ToolExecutor(ORG, make_backends(), today=lambda: TODAY)
    o = ex.execute("get_recommendations", {"week_start": W0})
    txt = "## What happened\n-\n## Why\n-\n## Top 3 actions\n" + format_recommendation(json.loads(o.content)["result"]["recommendations"][0], o.call_id, 1) + "\n## Experiments\n-\n"
    rep = validate_brief(txt, ex.audit)
    assert rep.ok, rep.feedback()
