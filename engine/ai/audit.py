"""Audit trail: every tool call (name, args, result, call_id, timestamp). Serialises to `insights.tool_calls`."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import AUDIT_SCHEMA_VERSION


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ToolCallRecord:
    call_id: str
    tool: str
    args: dict[str, Any]  # exactly what the model supplied (never includes the server-bound org_id)
    ok: bool
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    timestamp: str  # UTC ISO-8601
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id, "tool": self.tool, "args": self.args, "ok": self.ok,
            "result": self.result, "error": self.error, "timestamp": self.timestamp, "duration_ms": round(self.duration_ms, 3),
        }


@dataclass
class AuditLog:
    org_id: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    records: list[ToolCallRecord] = field(default_factory=list)
    _seq: int = 0

    def next_call_id(self) -> str:
        self._seq += 1
        return f"c{self._seq}"

    def add(self, rec: ToolCallRecord) -> None:
        self.records.append(rec)

    def get(self, call_id: str) -> ToolCallRecord | None:
        """Only successful calls are citable."""
        for r in self.records:
            if r.call_id == call_id and r.ok:
                return r
        return None

    def ok_records(self, tool: str | None = None) -> list[ToolCallRecord]:
        return [r for r in self.records if r.ok and (tool is None or r.tool == tool)]

    def tool_calls_json(self, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        """Value stored in insights.tool_calls: {"schema_version", "run_id", "meta", "calls": [...]}. No secrets."""
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "meta": meta or {},
            "calls": [r.to_dict() for r in self.records],
        }
