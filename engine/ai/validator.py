"""Traceability validator for AI briefs.

A brief passes only if EVERY number in it is cited with `[call_id]` markers that resolve to a successful tool
result stored in this run's audit log, AND the number equals a value in that result after documented conversion.

Number grammar (see `tokenize`)
  * `₹1,23,456.78`, `Rs 5000`, `INR 2.5 crore`, `1.2 lakh`, `3.4 crore`  -> money in RUPEES
  * `12 paise` / `1200 paise`                                              -> raw paise
  * `12.5%`                                                                -> percent
  * `2.3x`, `1,234`, `0.42`                                                -> plain (counts, ratios, scores)
Conversions
  * a money token matches a `*_paise` leaf (or `value` of a result whose unit is `paise`) divided by 100;
  * a percent token matches a plain leaf p (as 12.5) or a fraction f with |f| <= 1 (as 100*f);
  * a plain token matches a plain leaf; plain tokens never match paise leaves (write rupees or say "paise");
  * rounding: the token must lie within HALF A UNIT OF ITS LAST DISPLAYED DIGIT of the stored value; lakh/crore
    abbreviations are additionally capped at 5% relative error; signs are ignored (magnitude is compared);
  * numbers appearing inside STRING values of a cited result (e.g. "47 weeks < 52", pincode 110001) count as plain leaves.
Derived values (only when the sentence cites two or more distinct results): |a-b| and a+b for money, a/b for
ratios, a/b as percent, and (a-b)/b as percent change, over leaves of the cited results.

Not validated as numbers (documented carve-outs)
  * citation markers themselves; ISO dates; day-month(-year), month-day, month-year; FY labels (FY2026-27);
  * clock times (08:00); markdown list ordinals at line start (`1.`, `2)`); `Top 3` in headings;
    labels `Action 1`, `Experiment 2`, `#1`; ordinal words (3rd); numbers glued to letters/underscores/hyphen-after-letter
    (`tier_1`, `SKU-123`, `Q3`) because they are identifiers;
  * number WORDS ("three") are not detected - a documented limitation, rely on the prompt rule.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .audit import AuditLog, ToolCallRecord
from .config import NO_DATA_SENTENCE, REQUIRED_SECTIONS
from .fmt import format_inr
from .tools import result_is_insufficient

CITE_RE = re.compile(r"\[\s*(c\d+(?:\s*[,;]\s*c\d+)*)\s*\]", re.I)
LOOKS_LIKE_CITE_RE = re.compile(r"\[\s*(?:call[_-]?id|call[_-]?\d+|c\d+[^\]]*)\s*\]", re.I)
_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_EXEMPT = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?(?:\s?[-–]\s?\d{{1,2}}(?:st|nd|rd|th)?)?\s+{_MONTH}\b\.?(?:,?\s+\d{{4}})?", re.I),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s?[-–]\s?\d{{1,2}}(?:st|nd|rd|th)?)?\b(?:,?\s+\d{{4}})?", re.I),
    re.compile(r"\bFY\s?\d{2,4}(?:\s?[-–/]\s?\d{2,4})?", re.I),
    re.compile(r"\b(?:calendar\s+)?year\s+(?:19|20)\d{2}\b", re.I),
    re.compile(r"\b\d{1,2}:\d{2}\b"),
    re.compile(r"^\s*(?:[-*+]\s+)?\d{1,2}[.)](?=\s)"),
    re.compile(r"\bTop\s+\d+\b", re.I),
    re.compile(r"\b(?:Action|Experiment|Hypothesis|Recommendation|Anomaly|Priority|Option|Step)\s*#?\d+\b", re.I),
    re.compile(r"(?<![\w&])#\d{1,2}\b"),
]
NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
UNIT_RE = re.compile(r"^\s?(lakhs?|lacs?|crores?|cr\b|k\b|%|percent\b|per cent\b|x\b|×|paise\b|rupees?\b)", re.I)
CUR_RE = re.compile(r"(?:₹|\bRs[.․]?|\bINR)\s*[*_]*$", re.I)
ABBREV_RE = re.compile(r"\b(vs|approx|etc|No|St)\.|\be\.g\.|\bi\.e\.", re.I)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[*])")
INTERVAL_RE = re.compile(r"\b(interval|credible|CI)\b|confidence (?:range|band|bound)|(?:low|high) end of", re.I)
NEGATION_RE = re.compile(r"\b(not|no|cannot|can't|unable|insufficient|without|unclear|unproven|isn't|doesn't|does not|don't|never|unavailable)\b", re.I)
CAUSAL_RE = re.compile(r"\b(caus(?:ed|es|al|ally|ing)|drove|driven|incremental(?:ly)?|attributable|lift|uplift|prove[sd]?)\b", re.I)
MARKETING_RE = re.compile(r"\b(ads?|spend|campaigns?|channels?|meta|google|budget|roas|marketing|mmm|model)\b", re.I)
INTERVAL_KEYS = re.compile(r"(^|_)(ci_low|ci_high|lower|upper|interval_level|interval)($|_)|^(lower|upper)", re.I)


@dataclass
class Issue:
    code: str
    message: str
    line: int = 0
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "line": self.line, "snippet": self.snippet}


@dataclass
class ValidationReport:
    ok: bool
    issues: list[Issue] = field(default_factory=list)
    numbers_checked: int = 0
    numbers_exempt_blanked: int = 0

    def feedback(self, limit: int = 20) -> str:
        rows = [f"- line {i.line}: [{i.code}] {i.message}" + (f' (text: "{i.snippet}")' if i.snippet else "") for i in self.issues[:limit]]
        if len(self.issues) > limit:
            rows.append(f"- ... and {len(self.issues) - limit} more")
        return "\n".join(rows)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "numbers_checked": self.numbers_checked, "issues": [i.to_dict() for i in self.issues]}


# ----------------------------------------------------------------------------- tokenizing
@dataclass(frozen=True)
class NumberToken:
    text: str
    kind: str  # money | paise | percent | plain
    value: float  # money: rupees; paise: paise; percent: percent points; plain: as written (k expanded)
    half_unit: float  # rounding tolerance in the same unit as `value`
    abbreviated: bool  # lakh/crore: 5% relative cap applies
    pos: int


def blank_exempt(text: str) -> tuple[str, int]:
    """Replace carve-out spans with spaces (offsets preserved). Returns (masked, spans_blanked)."""
    n = 0
    for pat in _EXEMPT:
        def rep(m):
            nonlocal n
            n += 1
            return " " * len(m.group(0))
        text = pat.sub(rep, text)
    return text, n


def tokenize(text: str) -> list[NumberToken]:
    """Number tokens of `text` (carve-outs and citations already blanked by the caller where relevant)."""
    masked, _ = blank_exempt(CITE_RE.sub(lambda m: " " * len(m.group(0)), text))
    out: list[NumberToken] = []
    for m in NUM_RE.finditer(masked):
        raw = m.group(1)
        before, after = masked[: m.start()], masked[m.end():]
        if re.search(r"[A-Za-z]-$", before):  # SKU-123 style identifier
            continue
        um = UNIT_RE.match(after)
        unit = um.group(1).lower().replace(" ", "") if um else ""
        if not um and re.match(r"(st|nd|rd|th)\b", after, re.I):  # ordinal word
            continue
        num = float(raw.replace(",", ""))
        dec = len(raw.split(".")[1]) if "." in raw else 0
        half = 0.5 * 10 ** (-dec)
        cur = bool(CUR_RE.search(before))
        mult = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7, "k": 1e3}.get(unit, 1.0)
        abbreviated = unit in ("lakh", "lakhs", "lac", "lacs", "crore", "crores", "cr")
        if unit in ("%", "percent"):
            kind = "percent"
        elif unit == "paise":
            kind = "paise"
        elif cur or abbreviated or unit in ("rupee", "rupees"):
            kind = "money"
        else:
            kind = "plain"
        shown = masked[m.start(): m.end() + (len(um.group(0)) if um else 0)]
        out.append(NumberToken(shown.strip(), kind, num * mult, half * mult, abbreviated, m.start()))
    return out


# ----------------------------------------------------------------------------- leaves
@dataclass(frozen=True)
class Leaf:
    value: float
    kind: str  # paise | plain


def extract_leaves(result: dict[str, Any]) -> list[Leaf]:
    unit = result.get("unit") if "metric" in result else None
    leaves: list[Leaf] = []

    def walk(o: Any, key: str = "") -> None:
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            paise = key.endswith("_paise") or (unit == "paise" and key == "value")
            leaves.append(Leaf(abs(float(o)), "paise" if paise else "plain"))
        elif isinstance(o, str):
            for t in tokenize(o):
                leaves.append(Leaf(t.value, "plain"))
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, str(k))
        elif isinstance(o, list):
            for v in o:
                walk(v, key)

    walk(result)
    return leaves


def _close(tok: NumberToken, cand: float) -> bool:
    tol = tok.half_unit
    if tok.abbreviated:
        tol = min(tol, 0.05 * abs(cand))
    return abs(tok.value - cand) <= tol + 1e-9 * max(1.0, abs(cand))


def _candidates(kind: str, leaves: Iterable[Leaf]) -> list[float]:
    ls = list(leaves)
    if kind == "money":
        return [l.value / 100 for l in ls if l.kind == "paise"]
    if kind == "paise":
        return [l.value for l in ls if l.kind == "paise"]
    plain = [l.value for l in ls if l.kind == "plain"]
    if kind == "percent":
        return plain + [v * 100 for v in plain if v <= 1]
    return plain


def _derived(kind: str, per_result: list[list[Leaf]]) -> list[float]:
    """Pairwise derived values across leaves of >= 2 cited results (bounded)."""
    pool_p = [l.value for ls in per_result for l in ls if l.kind == "paise"][:150]
    pool_n = [l.value for ls in per_result for l in ls if l.kind == "plain"][:150]
    out: list[float] = []
    if kind in ("money", "paise"):
        scale = 100 if kind == "money" else 1
        for a, b in itertools.combinations(pool_p, 2):
            out += [abs(a - b) / scale, (a + b) / scale]
    elif kind == "percent":
        for a, b in itertools.permutations(pool_p, 2):
            if b:
                out += [a / b * 100, abs(a - b) / b * 100]
        for a, b in itertools.permutations(pool_n, 2):
            if b:
                out += [a / b * 100, abs(a - b) / b * 100]
    else:
        for a, b in itertools.permutations(pool_p, 2):
            if b:
                out.append(a / b)
        for a, b in itertools.permutations(pool_n, 2):
            if b:
                out.append(a / b)
        for a, b in itertools.combinations(pool_n, 2):
            out += [abs(a - b), a + b]
    return out


def _fmt_cand(kind: str, v: float) -> str:
    if kind == "money":
        return format_inr(round(v * 100))
    if kind == "percent":
        return f"{v:g}%"
    return f"{v:g}"


# ----------------------------------------------------------------------------- sentences
def _sentences(line: str) -> list[tuple[int, int]]:
    """(start, end) spans of sentences in `line`; a segment beginning with a citation joins the previous sentence."""
    if line.lstrip().startswith("|"):
        return [(0, len(line))]
    prot = ABBREV_RE.sub(lambda m: m.group(0).replace(".", "․"), line)
    bounds = [0] + [m.end() for m in SENT_SPLIT_RE.finditer(prot)] + [len(line)]
    spans: list[tuple[int, int]] = []
    for s, e in zip(bounds, bounds[1:]):
        if s == e:
            continue
        if spans and CITE_RE.match(line[s:e].lstrip()):
            spans[-1] = (spans[-1][0], e)
        else:
            spans.append((s, e))
    return spans or [(0, len(line))]


def _has_interval_fields(result: dict[str, Any]) -> bool:
    found = False

    def walk(o: Any) -> None:
        nonlocal found
        if isinstance(o, dict):
            for k, v in o.items():
                if INTERVAL_KEYS.search(str(k)) and v is not None:
                    found = True
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(result)
    return found


def _model_statuses(audit: AuditLog) -> list[str]:
    out = []
    for r in audit.ok_records("get_model_results"):
        out.append(str((r.result or {}).get("status")))
        for m in (r.result or {}).get("models", []) or []:
            if isinstance(m, dict) and m.get("status"):
                out.append(str(m["status"]))
    return out


# ----------------------------------------------------------------------------- main entry
def validate_brief(markdown: str, audit: AuditLog, *, require_sections: bool = True) -> ValidationReport:
    issues: list[Issue] = []
    checked = 0
    blanked_total = 0
    model_statuses = _model_statuses(audit)
    weak_model = any(s != "OK" for s in model_statuses)
    leaf_cache: dict[str, list[Leaf]] = {}

    def leaves_of(rec: ToolCallRecord) -> list[Leaf]:
        if rec.call_id not in leaf_cache:
            leaf_cache[rec.call_id] = extract_leaves(rec.result or {})
        return leaf_cache[rec.call_id]

    lines = markdown.splitlines()
    for ln, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        for s, e in _sentences(line):
            sent = line[s:e]
            ids: list[str] = []
            for m in CITE_RE.finditer(sent):
                ids += [x.strip().lower() for x in re.split(r"[,;]", m.group(1))]
            ids = list(dict.fromkeys(ids))
            recs = [audit.get(i) for i in ids]
            unresolved = [i for i, r in zip(ids, recs) if r is None]
            good = [r for r in recs if r is not None]
            for i in unresolved:
                issues.append(Issue("unknown_citation", f"citation [{i}] does not resolve to a successful tool result in this run", ln, sent.strip()[:80]))
            if not ids and LOOKS_LIKE_CITE_RE.search(sent):
                issues.append(Issue("unknown_citation", "malformed citation; use [c3] with a real call_id", ln, sent.strip()[:80]))
            toks = tokenize(sent)
            _, bl = blank_exempt(sent)
            blanked_total += bl
            for t in toks:
                checked += 1
                if not ids:
                    issues.append(Issue("uncited_number", f"number {t.text!r} has no [call_id] citation in its sentence", ln, sent.strip()[:80]))
                    continue
                if not good:
                    continue  # unresolved citations already reported
                per = [leaves_of(r) for r in good]
                allv = [l for ls in per for l in ls]
                if any(_close(t, c) for c in _candidates(t.kind, allv)):
                    continue
                if len(good) >= 2 and any(_close(t, c) for c in _derived(t.kind, per)):
                    continue
                near = sorted(_candidates(t.kind, allv), key=lambda c: abs(c - t.value))[:4]
                hint = ("; the cited result(s) contain: " + ", ".join(_fmt_cand(t.kind, c) for c in near)) if near else "; the cited result(s) hold no value of that kind"
                issues.append(Issue("number_not_in_cited_result",
                                    f"number {t.text!r} does not match any value in {', '.join('[' + r.call_id + ']' for r in good)}{hint}", ln, sent.strip()[:80]))
            # intervals / confidence
            if INTERVAL_RE.search(sent) and not (NEGATION_RE.search(sent) and not toks):
                if good and not any(_has_interval_fields(r.result or {}) for r in good):
                    issues.append(Issue("interval_not_in_result", "interval/confidence wording but the cited result(s) contain no stored interval", ln, sent.strip()[:80]))
                elif not good and toks:
                    issues.append(Issue("interval_not_in_result", "interval/confidence numbers without a resolving citation", ln, sent.strip()[:80]))
            # causal language while a model is weak
            if weak_model and CAUSAL_RE.search(sent) and MARKETING_RE.search(sent) and not NEGATION_RE.search(sent):
                issues.append(Issue("unsupported_causal_language",
                                    f"causal wording while the model status is {sorted(set(model_statuses) - {'OK'})}; describe, do not attribute", ln, sent.strip()[:80]))

    # mandatory disclosures
    if any(result_is_insufficient(r.result) for r in audit.ok_records()) and NO_DATA_SENTENCE not in markdown:
        issues.append(Issue("missing_disclosure", f'a tool result reported missing/insufficient data; the brief must state: "{NO_DATA_SENTENCE}"'))
    for st, word in (("INSUFFICIENT_DATA", r"insufficient"), ("WEAK", r"weak")):
        if st in model_statuses and not re.search(word, markdown, re.I):
            issues.append(Issue("missing_disclosure", f"model status {st} must be stated plainly in the brief"))
    if require_sections:
        pos = -1
        for h in REQUIRED_SECTIONS:
            m = re.search(rf"^##\s+{re.escape(h)}\s*$", markdown, re.I | re.M)
            if not m or m.start() < pos:
                issues.append(Issue("missing_section", f"required section '## {h}' is missing or out of order"))
            else:
                pos = m.start()
    return ValidationReport(not issues, issues, checked, blanked_total)
