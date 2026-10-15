import json

import pytest

from ai.config import NO_DATA_SENTENCE
from ai.fmt import format_inr
from ai.validator import tokenize, validate_brief
from tests.ai.helpers import MODEL_OK, W0, W1, make_backends, make_executor

SECTIONS = "## What happened\n{a}\n## Why\n{b}\n## Top 3 actions\n{c}\n## Experiments\n{d}\n"


@pytest.fixture()
def ctx():
    ex = make_executor(make_backends(model=MODEL_OK))
    env = {}
    for key, (name, args) in {
        "rev": ("get_metric", {"metric": "net_revenue", "start_date": W0, "end_date": W1}),
        "bych": ("get_metric_breakdown", {"metric": "net_revenue", "start_date": W0, "end_date": W1, "group_by": "channel"}),
        "model": ("get_model_results", {}),
        "recs": ("get_recommendations", {"week_start": W0}),
    }.items():
        env[key] = json.loads(ex.execute(name, args).content)
    return ex, env


def brief(a="", b="", c="", d=""):
    return SECTIONS.format(a=a, b=b, c=c, d=d)


def codes(rep):
    return sorted({i.code for i in rep.issues})


def test_good_brief_passes(ctx):
    ex, e = ctx
    rev = e["rev"]["result"]["value"]
    text = brief(a=f"Net revenue was {format_inr(rev)} [c1] from 7 days.",
                 b=f"Model status: OK [c3]. The 80% interval for the shift is {format_inr(400000)} to {format_inr(2100000)} [c4].",
                 c="1. **Shift budget**\n   - Expected impact: " + format_inr(1250000) + " [c4] with a 72% probability of beating the current split [c4].",
                 d="Not enough evidence for an experiment.")
    # "7 days" sits in a cited sentence but 7 is not in c1 -> fails; the rest is fine
    rep = validate_brief(text, ex.audit)
    assert codes(rep) == ["number_not_in_cited_result"] and "'7'" in rep.issues[0].message
    ok = validate_brief(text.replace(" from 7 days", ""), ex.audit)
    assert ok.ok, ok.feedback()
    assert ok.numbers_checked >= 5


def test_uncited_number_rejected(ctx):
    ex, e = ctx
    rep = validate_brief(brief(a=f"Net revenue was {format_inr(e['rev']['result']['value'])}."), ex.audit)
    assert "uncited_number" in codes(rep)


def test_number_not_in_cited_result_rejected(ctx):
    ex, _ = ctx
    rep = validate_brief(brief(a="Net revenue was ₹9.99 lakh [c1]."), ex.audit)
    assert codes(rep) == ["number_not_in_cited_result"]
    assert "contain" in rep.issues[0].message  # repair hint lists real values


def test_wrong_citation_rejected(ctx):
    ex, e = ctx
    # right number, wrong call (c4 is the recommendations result; net revenue is not in it)
    rep = validate_brief(brief(a=f"Net revenue was {format_inr(e['rev']['result']['value'])} [c4]."), ex.audit)
    assert "number_not_in_cited_result" in codes(rep)


def test_unknown_and_failed_call_citations_rejected(ctx):
    ex, e = ctx
    bad = ex.execute("get_metric", {"metric": "nope", "start_date": W0, "end_date": W1})
    assert not bad.ok
    for cid in ("c99", bad.call_id):
        rep = validate_brief(brief(a=f"Net revenue was {format_inr(e['rev']['result']['value'])} [{cid}]."), ex.audit)
        assert "unknown_citation" in codes(rep)


def test_citation_after_period_attaches_to_previous_sentence(ctx):
    ex, e = ctx
    assert validate_brief(brief(a=f"Net revenue was {format_inr(e['rev']['result']['value'])}. [c1]"), ex.audit, require_sections=False).ok


def test_money_formats_and_rounding_tolerance():
    # 1,234,567 paise = Rs 12,345.67
    from ai.audit import AuditLog, ToolCallRecord
    log = AuditLog("o")
    log.add(ToolCallRecord("c1", "get_metric", {}, True, {"metric": "x", "value": 1_234_567, "unit": "paise", "groups": [],
                                                           "data_quality": {"status": "ok"}}, None, "t"))
    v = lambda t: validate_brief(f"Value {t} [c1]", log, require_sections=False).ok
    assert v("₹12,345.67") and v("Rs 12,345.67") and v("Rs. 12346") and v("₹12,345.7") and v("₹0.12 lakh")
    assert v("1,234,567 paise")
    assert not v("₹12,400") and not v("₹1.5 lakh") and not v("₹0.12 crore")
    assert not v("1,234,567") and not v("12,000")  # plain numbers never match paise leaves; need a rupee marker or "paise"


def test_ratio_percent_and_probability_conversions():
    from ai.audit import AuditLog, ToolCallRecord
    log = AuditLog("o")
    log.add(ToolCallRecord("c1", "get_metric", {}, True, {"metric": "mer", "value": 2.3077, "unit": "ratio",
                                                           "prob": 0.725, "pct": 12.5, "count": 1234}, None, "t"))
    v = lambda t: validate_brief(f"x {t} [c1]", log, require_sections=False).ok
    assert v("2.31") and v("2.3077x") and v("72.5%") and v("12.5%") and v("1,234") and v("0.725")
    assert not v("2.4") and not v("73%") and not v("1,235")


def test_derived_difference_requires_both_cited():
    from ai.audit import AuditLog, ToolCallRecord
    log = AuditLog("o")
    for i, val in (("c1", 1_000_000), ("c2", 400_000)):
        log.add(ToolCallRecord(i, "get_metric", {}, True, {"metric": "m", "value": val, "unit": "paise", "groups": [],
                                                              "data_quality": {"status": "ok"}}, None, "t"))
    # difference = 600000 paise = Rs 6,000
    assert validate_brief("Down ₹6,000.00 [c1][c2].", log, require_sections=False).ok
    assert not validate_brief("Down ₹6,000.00 [c1].", log, require_sections=False).ok
    assert validate_brief("Down 60.0% [c1][c2].", log, require_sections=False).ok  # (a-b)/a? 600000/1000000
    assert not validate_brief("Down ₹7,000.00 [c1][c2].", log, require_sections=False).ok


def test_fabricated_interval_rejected(ctx):
    ex, _ = ctx
    # c1 is a metric result: it holds no interval at all
    rep = validate_brief(brief(a="We are 80% confident revenue rises, interval ₹1.00 lakh to ₹2.00 lakh [c1]."), ex.audit)
    assert "interval_not_in_result" in codes(rep) and "number_not_in_cited_result" in codes(rep)
    # even citing the recommendation: invented bounds are not in the result
    rep2 = validate_brief(brief(a="The 80% interval is ₹3.00 lakh to ₹9.00 lakh [c4]."), ex.audit)
    assert "number_not_in_cited_result" in codes(rep2)


def test_interval_claim_without_citation_rejected(ctx):
    ex, _ = ctx
    rep = validate_brief(brief(a="The 90% credible interval is wide."), ex.audit)
    assert "uncited_number" in codes(rep)


def test_insufficient_model_requires_plain_statement_and_blocks_causal_claims():
    ex = make_executor()  # default model: INSUFFICIENT_DATA
    ex.execute("get_model_results", {})
    rep = validate_brief(brief(a="Meta spend caused the revenue rise [c1]."), ex.audit)
    assert {"unsupported_causal_language", "missing_disclosure"} <= set(codes(rep))
    good = brief(a="The marketing model status is INSUFFICIENT_DATA [c1]: 47 weeks < 52, so no causal claim about Meta is made.",
                 b=NO_DATA_SENTENCE, c=NO_DATA_SENTENCE, d=NO_DATA_SENTENCE)
    # c1 reason is "47 weeks < 52": numbers 47 and 52 are in the result
    assert validate_brief(good, ex.audit).ok, validate_brief(good, ex.audit).feedback()


def test_missing_data_requires_exact_sentence():
    ex = make_executor()
    ex.execute("get_metric", {"metric": "mer", "start_date": "2026-01-01", "end_date": "2026-01-07"})
    assert "missing_disclosure" in codes(validate_brief(brief(a="MER cannot be computed."), ex.audit))
    assert validate_brief(brief(a=NO_DATA_SENTENCE, b="-", c="-", d="-"), ex.audit).ok


def test_missing_sections():
    ex = make_executor()
    rep = validate_brief("## What happened\nQuiet week.\n", ex.audit)
    assert codes(rep) == ["missing_section"] and len(rep.issues) == 3


# ------------------------------------------------------------------ carve-outs
@pytest.mark.parametrize("text", [
    "Week of 2026-09-14 to 2026-09-20.", "14-20 Sep 2026 was strong.", "From Sep 14 to Sep 20.", "In September 2026.",
    "FY2026-27 so far.", "FY 2026-27 so far.", "Sent Mondays at 08:00 IST.", "1. First point", "  2) Second point",
    "Action 1: shift budget", "Experiment 2 is proposed", "Recommendation #1", "tier_1 cities and SKU-123 and Q3 and D2C", "the 3rd attempt",
    "In calendar year 2026 overall.", "## Top 3 actions", "Order O12 shipped",
])
def test_carve_outs_not_validated(text):
    assert tokenize(text) == [], text


@pytest.mark.parametrize("text,n", [
    ("Sales 2026 units", 1), ("revenue 5 lakh", 1), ("up 12%", 1), ("2.3x", 1), ("tier 1 cities", 1), ("1.5 crore and 20", 2),
    ("₹1,23,456.78", 1), ("12k", 1),
])
def test_real_numbers_are_tokenized(text, n):
    assert len(tokenize(text)) == n


def test_token_kinds():
    kinds = lambda t: [(x.kind, round(x.value, 4)) for x in tokenize(t)]
    assert kinds("₹1.5 lakh") == [("money", 150000.0)]
    assert kinds("Rs. 2 crore") == [("money", 20000000.0)]
    assert kinds("12.5%") == [("percent", 12.5)]
    assert kinds("500 paise") == [("paise", 500.0)]
    assert kinds("2.3x") == [("plain", 2.3)]
    assert kinds("INR 4,500") == [("money", 4500.0)]
