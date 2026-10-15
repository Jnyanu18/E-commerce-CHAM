"""Shared result helpers. Weak data is always an explicit INSUFFICIENT_DATA, never a fabricated number."""
from __future__ import annotations

from statistics import NormalDist
from typing import Any

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# 80% central interval (contract: ci_low / ci_high are 80%).
Z80 = NormalDist().inv_cdf(0.90)


def insufficient(reason: str, fallback: str = "descriptive", **extra: Any) -> dict[str, Any]:
    """Contract section 6 shape: {status, reason, fallback}."""
    return {"status": INSUFFICIENT_DATA, "reason": reason, "fallback": fallback, **extra}


def is_insufficient(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == INSUFFICIENT_DATA
