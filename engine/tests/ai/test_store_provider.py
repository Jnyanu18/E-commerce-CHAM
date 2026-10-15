import json
import os
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from ai.brief import generate_brief
from ai.provider import AnthropicProvider, ProviderError, ProviderResponse
from ai.store import InMemoryInsightStore, InsightRecord, PostgresInsightStore
from ai.validator import validate_brief
from tests.ai.helpers import TODAY, W0, W1, WEEK, make_backends, make_executor
from tests.ai.test_brief import TOOLS_STEP, good_brief
from ai.provider import ScriptedProvider, text_response

URL = os.environ.get("DATABASE_URL")


def rec(org, md="x", n=1):
    return InsightRecord(org, WEEK, md, [{"id": "a"}], {"schema_version": 1, "meta": {"prompt_version": "brief-v1", "model_id": "m"}, "calls": [{"n": n}]},
                         datetime(2026, 10, 1, tzinfo=timezone.utc))


def test_in_memory_store_upserts_per_org_week():
    s = InMemoryInsightStore()
    s.save(rec("o1", "first"))
    s.save(rec("o1", "second"))
    s.save(rec("o2", "other"))
    assert s.get("o1", WEEK).brief_markdown == "second" and s.get("o2", WEEK).brief_markdown == "other"
    assert s.get("o3", WEEK) is None and len(s.rows) == 2


@pytest.fixture()
def pg():
    psycopg = pytest.importorskip("psycopg")
    if not URL:
        pytest.skip("DATABASE_URL not set")
    try:
        conn = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database unreachable: {e}")
    org = str(uuid.uuid4())
    conn.execute("insert into orgs(id, name) values (%s, 'ai-it')", (org,))
    yield conn, org
    conn.execute("delete from orgs where id = %s", (org,))
    conn.close()


def test_postgres_store_roundtrip_and_upsert(pg):
    conn, org = pg
    s = PostgresInsightStore(conn)
    s.save(rec(org, "v1", 1))
    conn.execute("update insights set emailed_at = now() where org_id = %s", (org,))
    s.save(rec(org, "v2", 2))  # re-generation replaces content but keeps emailed_at
    got = s.get(org, WEEK)
    assert got.brief_markdown == "v2" and got.tool_calls["calls"] == [{"n": 2}] and got.tool_calls["meta"]["prompt_version"] == "brief-v1"
    assert got.anomalies == [{"id": "a"}]
    n, emailed = conn.execute("select count(*), bool_and(emailed_at is not null) from insights where org_id = %s", (org,)).fetchone()
    assert n == 1 and emailed is True


def test_postgres_end_to_end_brief_persisted(pg):
    conn, org = pg
    # the db org id is a uuid, the metric fixtures are keyed by "org-1": use a repo/sources keyed by the uuid
    from ai.mocks import StaticAnomalySource, StaticDataQualitySource, StaticModelSource, StaticRecommendationSource
    from tests.ai.helpers import ANOM, CAUSE, EXP, MODEL_INSUFFICIENT, REC
    from tests.metrics.fx import ORG, repo_with_spend
    repo = repo_with_spend()
    repo._orders[org], repo._items[org], repo._fees[org] = repo._orders[ORG], repo._items[ORG], repo._fees[ORG]
    repo._returns[org], repo._sku_cogs[org], repo._skus[org], repo._ad_spend[org] = repo._returns[ORG], repo._sku_cogs[ORG], repo._skus[ORG], repo._ad_spend[ORG]
    from ai.tools import ToolBackends
    b = ToolBackends(repo, StaticAnomalySource({org: [ANOM]}, {org: {"an-1": CAUSE}}), StaticModelSource({org: MODEL_INSUFFICIENT}),
                     StaticRecommendationSource({org: [REC]}, {org: [EXP]}), StaticDataQualitySource({org: {"status": "ok", "issues": []}}))
    p = ScriptedProvider([TOOLS_STEP, lambda msgs: text_response(good_brief(msgs))])
    r = generate_brief(org, WEEK, provider=p, backends=b, store=PostgresInsightStore(conn), today=lambda: TODAY)
    assert r.mode == "narrative", r.validation.feedback()
    row = conn.execute("select brief_markdown, tool_calls, anomalies from insights where org_id = %s", (org,)).fetchone()
    assert row[0] == r.markdown and len(row[1]["calls"]) == 7 and row[1]["meta"]["mode"] == "narrative" and row[2][0]["id"] == "an-1"


# ------------------------------------------------------------------ Anthropic provider (fake client; NOT a live test)
class FakeClient:
    def __init__(self, resp):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self._resp = resp

    def _create(self, **kw):
        self.calls.append(kw)
        return self._resp


def _block(d):
    return SimpleNamespace(model_dump=lambda exclude_none=True: d, **d)


def test_anthropic_provider_request_shape_and_response_mapping(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 1}),
                           content=[_block({"type": "text", "text": "hi"}),
                                    _block({"type": "tool_use", "id": "toolu_1", "name": "get_model_results", "input": {}})])
    client = FakeClient(resp)
    prov = AnthropicProvider(client=client)
    out = prov.complete(system="S", messages=[{"role": "user", "content": "u"}], tools=[{"name": "t"}], max_tokens=100)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "S" and kw["max_tokens"] == 100
    for banned in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert banned not in kw
    assert kw["extra_body"]["output_config"] == {"effort": "high"}
    assert out.tool_uses[0].name == "get_model_results" and out.text == "hi" and out.stop_reason == "tool_use"


def test_anthropic_provider_refusal_and_errors_become_provider_error():
    refusal = SimpleNamespace(stop_reason="refusal", usage=None, content=[])
    with pytest.raises(ProviderError):
        AnthropicProvider(client=FakeClient(refusal)).complete(system="", messages=[], tools=[], max_tokens=1)

    class Bad:
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("secret-key-xyz in message")))
    with pytest.raises(ProviderError) as ei:
        AnthropicProvider(client=Bad()).complete(system="", messages=[], tools=[], max_tokens=1)
    assert "secret-key-xyz" not in str(ei.value)  # raw SDK error text is not propagated into the audit/brief


def test_anthropic_provider_without_sdk_raises_provider_error():
    import importlib.util
    if importlib.util.find_spec("anthropic"):
        pytest.skip("anthropic SDK installed")
    with pytest.raises(ProviderError):
        AnthropicProvider().complete(system="", messages=[], tools=[], max_tokens=1)


# ------------------------------------------------------------------ more adversarial briefs
@pytest.fixture()
def rev_ctx():
    ex = make_executor()
    env = json.loads(ex.execute("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1}).content)
    return ex, env


def test_lakh_crore_confusion_is_rejected():
    from ai.audit import AuditLog, ToolCallRecord
    log = AuditLog("o")  # 12,34,56,789.00 rupees = 12.35 crore = 1234.57 lakh
    log.add(ToolCallRecord("c1", "get_metric", {}, True, {"metric": "m", "value": 12_345_678_900, "unit": "paise", "groups": [],
                                                           "data_quality": {"status": "ok"}}, None, "t"))
    ok = lambda t: validate_brief(f"Revenue {t} [c1].", log, require_sections=False).ok
    assert ok("₹12.35 crore") and ok("₹1234.57 lakh") and ok("₹12,34,56,789")
    assert not ok("₹12.35 lakh") and not ok("₹1.23 crore") and not ok("₹123.46 crore") and not ok("₹15 crore")


def test_number_hidden_in_table_and_bold_still_checked(rev_ctx):
    ex, _ = rev_ctx
    assert not validate_brief("| Metric | Value |\n|---|---|\n| Net revenue | **₹99.99 lakh** [c1] |", ex.audit, require_sections=False).ok
    assert not validate_brief("**Net revenue was ₹99.99 lakh**", ex.audit, require_sections=False).ok


def test_citation_in_a_different_sentence_does_not_cover_number(rev_ctx):
    ex, env = rev_ctx
    from ai.fmt import format_inr
    txt = f"Net revenue was {format_inr(env['result']['value'])}. Everything else is fine [c1]."
    assert "uncited_number" in {i.code for i in validate_brief(txt, ex.audit, require_sections=False).issues}


def test_percent_change_claim_without_both_citations_rejected():
    ex = make_executor()
    ex.execute("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1})
    ex.execute("get_metric", {"metric": "contribution_margin", "start_date": W0, "end_date": W1})
    assert not validate_brief("Revenue is up 23% [c1].", ex.audit, require_sections=False).ok
    assert not validate_brief("Revenue is up 23% [c1][c2].", ex.audit, require_sections=False).ok  # not an actual derived value
