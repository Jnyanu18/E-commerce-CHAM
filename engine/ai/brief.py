"""Weekly brief generation: tool loop -> traceability validation -> bounded repair -> data-only fallback.

    result = generate_brief(org_id, week_start, provider=..., backends=ToolBackends(repo, ...), store=...)

Email delivery and scheduling (Mondays 08:00 IST) are main's integration work; this is the function it calls.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .audit import AuditLog, utcnow
from .config import FALLBACK_MARKER, NO_DATA_SENTENCE, SYSTEM_PROMPT, REPAIR_PROMPT, BriefConfig
from .fmt import format_inr, format_value
from .provider import LLMProvider, ProviderError
from .recommendations import clean_text, format_recommendation
from .store import InsightRecord, InsightStore
from .tools import ToolBackends, ToolExecutor, result_is_insufficient
from .validator import Issue, ValidationReport, validate_brief


@dataclass
class BriefResult:
    org_id: str
    week_start: date
    markdown: str
    mode: str  # "narrative" (AI text, validated) | "fallback" (deterministic data-only summary)
    validation: ValidationReport
    audit: AuditLog
    model_id: str
    prompt_version: str
    attempts: int
    fallback_reason: str | None = None
    insight: InsightRecord | None = None


def user_prompt(week_start: date) -> str:
    end, prev_s, prev_e = week_start + timedelta(days=6), week_start - timedelta(days=7), week_start - timedelta(days=1)
    return (f"Write the weekly brief for the IST week {week_start.isoformat()} (Monday) to {end.isoformat()} (Sunday). "
            f"The comparison week is {prev_s.isoformat()} to {prev_e.isoformat()}. week_start for week-scoped tools is {week_start.isoformat()}. "
            "Use the tools; cite every number.")


def default_plan(week_start: date) -> list[tuple[str, dict[str, Any]]]:
    s, e = week_start.isoformat(), (week_start + timedelta(days=6)).isoformat()
    metric = lambda m: ("get_metric", {"metric": m, "start_date": s, "end_date": e})
    return [metric("net_revenue"), metric("contribution_margin"), metric("contribution_after_marketing"), metric("mer"),
            ("get_anomalies", {"week_start": s}), ("get_model_results", {}), ("get_recommendations", {"week_start": s}),
            ("get_experiment_candidates", {}), ("get_data_quality", {"start_date": s, "end_date": e})]


# ----------------------------------------------------------------------------- fallback
def _scalar(key: str, v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return format_inr(v) if key.endswith("_paise") else (str(v) if isinstance(v, int) else repr(round(v, 6)))
    return clean_text(v)


def _flatten(o: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(o, dict):
        for k, v in o.items():
            out += _flatten(v, f"{prefix}{k}" if not prefix else f"{prefix}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += _flatten(v, prefix)
    else:
        out.append((prefix, o))
    return out


def _render_item(item: dict[str, Any], cite: str) -> str:
    pairs = [f"{k.split('.')[-1].replace('_', ' ')}: {_scalar(k, v)}" for k, v in _flatten(item) if v is not None]
    return "- " + "; ".join(pairs) + f" {cite}"


def build_fallback_summary(audit: AuditLog, executor: ToolExecutor, week_start: date, reason: str) -> str:
    """Deterministic data-only summary from stored tool results. No interpretation, no model text.
    If the run produced no successful calls, the default plan is executed first (still audited)."""
    if not audit.ok_records():
        for name, args in default_plan(week_start):
            executor.execute(name, args)
    last = lambda t: next(iter(reversed(audit.ok_records(t))), None)
    L: list[str] = [f"# Weekly brief - {FALLBACK_MARKER}", "",
                    f"> **{FALLBACK_MARKER}.** The AI-written narrative did not pass traceability validation ({re.sub(r'\d+', 'N', clean_text(reason))}), "
                    "so this page lists only figures returned directly by the tools, each cited, with no interpretation.", ""]
    L += ["## What happened"]
    any_metric = False
    for r in audit.ok_records():
        if r.tool not in ("get_metric", "get_metric_breakdown"):
            continue
        any_metric = True
        res, cite = r.result, f"[{r.call_id}]"
        per = res["period"]
        flt = (" filters " + ", ".join(f"{k}={'/'.join(v)}" for k, v in res["filters"].items())) if res["filters"] else ""
        if res["value"] is None:
            L.append(f"- {res['metric']} ({per['start']} to {per['end']}{flt}): {NO_DATA_SENTENCE} {cite}")
            continue
        L.append(f"- {res['metric']} ({per['start']} to {per['end']}{flt}): {format_value(res['value'], res['unit'])} {cite}")
        if res.get("group_by"):
            for g in res["groups"][:12]:
                L.append(f"  - {res['group_by']} {g['key']}: {format_value(g['value'], res['unit'])} {cite}")
        if res["data_quality"]["status"] != "ok":
            L.append(f"  - data quality {res['data_quality']['status']}: {clean_text('; '.join(res['data_quality']['issues'])) or 'no detail'} {cite}")
    if not any_metric:
        L.append(f"- {NO_DATA_SENTENCE}")

    L += ["", "## Why"]
    an, rc = last("get_anomalies"), audit.ok_records("get_root_causes")
    if an and an.result.get("anomalies"):
        L += [_render_item(a, f"[{an.call_id}]") for a in an.result["anomalies"][:10]]
    elif an:
        L.append(f"- No anomalies were flagged by the detector for this week [{an.call_id}].")
    else:
        L.append(f"- {NO_DATA_SENTENCE}")
    for r in rc:
        L.append(_render_item({k: v for k, v in r.result.items() if k != "status"}, f"[{r.call_id}]"))

    L += ["", "## Top 3 actions"]
    rec = last("get_recommendations")
    if rec and rec.result.get("recommendations"):
        L += [format_recommendation(x, rec.call_id, i) for i, x in enumerate(rec.result["recommendations"][:3], start=1)]
    else:
        L.append(NO_DATA_SENTENCE)

    L += ["", "## Experiments"]
    ex = last("get_experiment_candidates")
    if ex and ex.result.get("experiments"):
        L += [_render_item(x, f"[{ex.call_id}]") for x in ex.result["experiments"][:5]]
    else:
        L.append(NO_DATA_SENTENCE)

    L += ["", "## Data notes"]
    m = last("get_model_results")
    if m:
        st = m.result.get("status")
        L.append(f"- Model status: {st}" + (f" ({clean_text(m.result['reason'])})" if m.result.get("reason") else "")
                 + f" [{m.call_id}]." + (" Causal claims are not supported." if st != "OK" else ""))
    dq = last("get_data_quality")
    if dq:
        L.append("- " + clean_text(f"Data quality {dq.result.get('status')}: " + "; ".join(map(str, dq.result.get("issues") or []))) + f" [{dq.call_id}]")
    if any(result_is_insufficient(r.result) for r in audit.ok_records()):
        L.append(f"- {NO_DATA_SENTENCE}")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------- main
def _anomalies_from(audit: AuditLog) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in audit.ok_records("get_anomalies"):
        for a in r.result.get("anomalies", []):
            key = json.dumps(a, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append({**a, "source_call_id": r.call_id})
    return out


def _tool_result_message(outcomes: list[tuple[str, str, bool]]) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content, **({"is_error": True} if not ok else {})}
                                        for tid, content, ok in outcomes]}


def generate_brief(org_id: str, week_start: date, *, provider: LLMProvider, backends: ToolBackends,
                   store: InsightStore | None = None, config: BriefConfig | None = None,
                   today: Callable[[], date] | None = None) -> BriefResult:
    cfg = config or BriefConfig()
    lim = cfg.limits
    ex = ToolExecutor(org_id, backends, limits=lim, today=today)
    audit = ex.audit
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt(week_start)}]
    repairs = turns = attempts = 0
    report: ValidationReport | None = None
    markdown, mode, reason = "", "narrative", None

    try:
        while True:
            if turns >= lim.max_turns:
                reason = "turn limit reached"
                break
            turns += 1
            resp = provider.complete(system=SYSTEM_PROMPT, messages=messages, tools=ex.definitions, max_tokens=cfg.max_tokens)
            messages.append({"role": "assistant", "content": resp.content})
            uses = resp.tool_uses
            if uses:
                outs = []
                for u in uses:
                    if len(audit.records) >= lim.max_tool_calls_per_brief:
                        outs.append((u.id, json.dumps({"ok": False, "error": {"code": "tool_budget_exhausted", "message": "no more tool calls allowed; write the brief"}}), False))
                        continue
                    o = ex.execute(u.name, u.input)
                    outs.append((u.id, o.content, o.ok))
                messages.append(_tool_result_message(outs))
                continue
            attempts += 1
            text = resp.text.strip()
            report = validate_brief(text, audit)
            if resp.stop_reason == "max_tokens":
                report.issues.append(Issue("truncated", "the output was cut off at the token limit; write a shorter brief"))
                report.ok = False
            if not audit.ok_records():
                report.issues.append(Issue("no_tool_calls", "no successful tool call was made; every figure must come from tools"))
                report.ok = False
            if report.ok:
                markdown = text
                break
            if repairs >= lim.max_repairs:
                reason = f"validation failed after {attempts} attempt(s): " + ", ".join(sorted({i.code for i in report.issues}))
                break
            repairs += 1
            messages.append({"role": "user", "content": REPAIR_PROMPT.format(attempt=repairs, total=lim.max_repairs, feedback=report.feedback())})
    except ProviderError as e:
        reason = f"provider error: {e}"

    if reason is not None:
        mode = "fallback"
        markdown = build_fallback_summary(audit, ex, week_start, reason)
        report = validate_brief(markdown, audit)  # the fallback is held to the same standard

    meta = {
        "prompt_version": cfg.prompt_version, "model_id": getattr(provider, "model_id", cfg.model_id), "mode": mode,
        "fallback_reason": reason, "attempts": attempts, "turns": turns,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "validation": report.to_dict() if report else None, "week_start": week_start.isoformat(),
    }
    insight = InsightRecord(str(org_id), week_start, markdown, _anomalies_from(audit), audit.tool_calls_json(meta), utcnow())
    if store is not None:
        store.save(insight)
    return BriefResult(str(org_id), week_start, markdown, mode, report, audit, meta["model_id"], cfg.prompt_version,
                       attempts, reason, insight)
