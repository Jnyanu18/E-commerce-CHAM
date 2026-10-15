"""LLM provider interface, a deterministic scripted mock, and the Anthropic implementation.

Messages use the Anthropic Messages format (assistant content blocks incl. `tool_use`; user turns carry
`tool_result` blocks), so the loop in brief.py is provider-neutral.

STATUS: AnthropicProvider has NOT been live-tested (no ANTHROPIC_API_KEY, `anthropic` SDK not installed in the
engine venv at the time of writing). The SDK is imported lazily. Everything else is exercised via ScriptedProvider.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .config import DEFAULT_MODEL_ID, refusal_fallback_enabled


class ProviderError(RuntimeError):
    """Provider failure (refusal, transport, SDK missing). The brief falls back to the data-only summary."""


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ProviderResponse:
    content: list[dict[str, Any]]  # raw assistant content blocks, appended verbatim to the transcript
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens | refusal
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[ToolUse]:
        return [ToolUse(b["id"], b["name"], b.get("input") or {}) for b in self.content if b.get("type") == "tool_use"]


class LLMProvider(Protocol):
    model_id: str

    def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> ProviderResponse: ...


# ----------------------------------------------------------------------------- scripted mock
def text_response(text: str) -> ProviderResponse:
    return ProviderResponse([{"type": "text", "text": text}], "end_turn")


def tool_response(*calls: tuple[str, dict[str, Any]], text: str = "") -> ProviderResponse:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for i, (name, args) in enumerate(calls):
        blocks.append({"type": "tool_use", "id": f"toolu_mock_{i}", "name": name, "input": args})
    return ProviderResponse(blocks, "tool_use")


def tool_results_by_call_id(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Parse every tool_result envelope in the transcript -> {call_id: envelope}. Used by scripted briefs."""
    out: dict[str, dict[str, Any]] = {}
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    env = json.loads(b["content"])
                    out[env["call_id"]] = env
    return out


Step = ProviderResponse | Callable[[list[dict[str, Any]]], ProviderResponse]


@dataclass
class ScriptedProvider:
    """Plays a fixed script, one step per `complete` call. A step is a ProviderResponse or a callable receiving
    the transcript (so a scripted brief can quote the real tool results). Records every request for assertions."""

    steps: list[Step]
    model_id: str = "scripted-mock"
    requests: list[dict[str, Any]] = field(default_factory=list)
    _i: int = 0

    def complete(self, *, system, messages, tools, max_tokens) -> ProviderResponse:
        self.requests.append({"system": system, "messages": json.loads(json.dumps(messages)), "tools": tools})
        if self._i >= len(self.steps):
            raise ProviderError("scripted provider exhausted")
        step = self.steps[self._i]
        self._i += 1
        return step(messages) if callable(step) else step


# ----------------------------------------------------------------------------- Anthropic (not live-tested)
class AnthropicProvider:
    """Thin wrapper over the Anthropic Messages API (manual tool loop is in brief.py).

    Request shape follows the claude-api skill (2026-10): no `temperature`/`top_p` (rejected on current models),
    no forced `tool_choice` (400 on claude-opus-5-5), no assistant prefill, adaptive thinking left at the model default
    with thinking blocks echoed back unchanged inside `content`. `output_config.effort` and the server-side
    refusal fallback (beta header `server-side-fallback-2026-07-01`) are sent via extra_body/extra_headers so the code
    does not depend on the installed SDK's typed parameters. UNVERIFIED against the live API.
    """

    def __init__(self, model_id: str | None = None, *, effort: str = "high", client: Any = None, use_refusal_fallback: bool | None = None):
        self.model_id = model_id or DEFAULT_MODEL_ID
        self.effort = effort
        self._client = client
        self.use_refusal_fallback = refusal_fallback_enabled() if use_refusal_fallback is None else use_refusal_fallback

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic  # lazy: dependency request logged in PROGRESS.md
            except ImportError as e:  # pragma: no cover
                raise ProviderError("the 'anthropic' package is not installed") from e
            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        return self._client

    def complete(self, *, system, messages, tools, max_tokens) -> ProviderResponse:
        extra_body: dict[str, Any] = {"output_config": {"effort": self.effort}}
        extra_headers: dict[str, str] = {}
        if self.use_refusal_fallback:
            extra_body["fallbacks"] = "default"
            extra_headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        try:
            resp = self._get_client().messages.create(
                model=self.model_id, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                extra_body=extra_body, extra_headers=extra_headers or None,
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"{type(e).__name__}: Anthropic request failed") from e
        if getattr(resp, "stop_reason", None) == "refusal":
            raise ProviderError("model refused the request (stop_reason=refusal)")
        content = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b) for b in resp.content]
        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else {}
        return ProviderResponse(content, resp.stop_reason or "end_turn", usage)
