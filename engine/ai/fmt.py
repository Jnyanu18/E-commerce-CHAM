"""Display formatting (paise -> rupees, lakh/crore). Formatting only; no business metrics."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

LAKH = Decimal(10) ** 5
CRORE = Decimal(10) ** 7


def rupees(paise: int | float | Decimal) -> Decimal:
    return Decimal(str(paise)) / 100


def format_inr(paise: int | float | Decimal | None) -> str:
    """paise -> '₹12,345.67' (< 1 lakh), '₹1.23 lakh', '₹4.50 crore'. Sign precedes the symbol."""
    if paise is None:
        return "n/a"
    r = rupees(paise)
    sign = "-" if r < 0 else ""
    a = abs(r)
    q2 = Decimal("0.01")
    if a >= CRORE:
        body = f"₹{(a / CRORE).quantize(q2, rounding=ROUND_HALF_UP)} crore"
    elif a >= LAKH:
        body = f"₹{(a / LAKH).quantize(q2, rounding=ROUND_HALF_UP)} lakh"
    else:
        body = f"₹{a.quantize(q2, rounding=ROUND_HALF_UP):,}"
    return sign + body


def format_value(value, unit: str | None) -> str:
    if value is None:
        return "n/a"
    if unit == "paise":
        return format_inr(value)
    if unit == "ratio":
        return f"{float(value):.4f}".rstrip("0").rstrip(".") if float(value) != int(float(value)) else str(int(float(value)))
    return str(value)


def format_pct(fraction: float, digits: int = 1) -> str:
    return f"{Decimal(str(fraction)) * 100:.{digits}f}%"
