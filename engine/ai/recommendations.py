"""Plain-language explanations of stored recommendations. Built ONLY from the stored payload (no model, no new numbers).

Every figure comes from the recommendation row returned by the `get_recommendations` tool and carries its citation.
"""
from __future__ import annotations

import re
from typing import Any

from .config import NO_DATA_SENTENCE
from .fmt import format_inr, format_pct


def clean_text(s: Any) -> str:
    """Make free text safe to embed in one sentence (stored text may contain full stops followed by capitals)."""
    return re.sub(r"[.!?]\s+(?=[A-Z\[*])", "; ", str(s)).strip().rstrip(".")


def _name(v: Any) -> str:
    return clean_text(v) if v else "unspecified"


def explain_recommendation(rec: dict[str, Any], call_id: str) -> dict[str, str]:
    """-> {"action", "reason", "impact", "confidence", "evidence"} strings, each figure cited [call_id]."""
    cite = f"[{call_id}]"
    p = rec.get("payload") or {}
    typ = rec.get("type") or "recommendation"

    if typ == "budget_shift" and p.get("shift_paise") is not None:
        action = f"Move {format_inr(p['shift_paise'])} of ad budget from {_name(p.get('from_channel'))} to {_name(p.get('to_channel'))} {cite}."
    elif typ == "run_experiment":
        action = f"Run a {_name(p.get('design', 'geo_holdout')).replace('_', ' ')} experiment" + (f" on {_name(p['channel'])}" if p.get("channel") else "") + f" {cite}."
    else:
        action = f"Recommendation of type {_name(typ)}: " + (clean_text(p.get("summary")) if p.get("summary") else "see the stored payload") + f" {cite}."

    reason = f"{clean_text(p['reason'])} {cite}." if p.get("reason") else f"{NO_DATA_SENTENCE}"

    impact_v, lo, hi = rec.get("expected_impact_paise"), rec.get("ci_low_paise"), rec.get("ci_high_paise")
    if impact_v is None:
        impact = NO_DATA_SENTENCE
        confidence = NO_DATA_SENTENCE
    else:
        impact = f"Expected impact {format_inr(impact_v)} {cite}."
        parts = []
        if lo is not None and hi is not None and rec.get("interval_level") is not None:
            parts.append(f"{format_pct(rec['interval_level'], 0)} interval {format_inr(lo)} to {format_inr(hi)} {cite}")
        else:
            parts.append("no uncertainty range is stored for this recommendation")
        if rec.get("prob_beats_current") is not None:
            parts.append(f"probability of beating the current split {format_pct(rec['prob_beats_current'], 0)} {cite}")
        else:
            parts.append("no probability of beating the current split is stored")
        joined = "; ".join(parts)
        confidence = joined[0].upper() + joined[1:] + "."
    return {"action": action, "reason": reason, "impact": impact, "confidence": confidence, "evidence": cite}


def format_recommendation(rec: dict[str, Any], call_id: str, index: int | None = None) -> str:
    """Markdown block for one recommendation."""
    e = explain_recommendation(rec, call_id)
    head = f"{index}. " if index else "- "
    pad = "   " if index else "  "
    return (f"{head}**Action:** {e['action']}\n{pad}- Reason: {e['reason']}\n{pad}- Expected impact: {e['impact']}\n"
            f"{pad}- Confidence: {e['confidence']}\n{pad}- Evidence: {e['evidence']}")
