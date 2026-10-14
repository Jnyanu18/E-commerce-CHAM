"""Estimated fees (source='estimated'). Pure; rates MUST be supplied by the caller (no invented defaults).

Estimates are placeholders for fees not yet reported. The repository guarantees an estimate never overwrites
or coexists with an actual (api/csv) fee of the same type for the same order.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def estimate_fees(order: dict, rates_bps: dict[str, int]) -> list[dict]:
    """rates_bps: fee type -> basis points of customer-paid value (gross - discounts + shipping).

    Returns [] when no rates are configured. Never returns negative fees.
    """
    paid = order["gross_revenue_paise"] - order.get("discounts_paise", 0) + order.get("shipping_charged_paise", 0)
    out = []
    for ftype, bps in sorted(rates_bps.items()):
        amt = int((Decimal(max(paid, 0)) * Decimal(bps) / Decimal(10000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        out.append({"type": ftype, "amount_paise": amt, "source": "estimated"})
    return out
