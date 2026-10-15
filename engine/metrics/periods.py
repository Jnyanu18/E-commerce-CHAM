"""Period helpers. All dates are IST calendar dates."""
from __future__ import annotations

from datetime import date, timedelta

from .types import DateRange


def week_start(d: date) -> date:
    """Monday of the week containing d (Monday-start weeks)."""
    return d - timedelta(days=d.weekday())


def week_range(d: date) -> DateRange:
    s = week_start(d)
    return DateRange(s, s + timedelta(days=6))


def fy_start_year(d: date) -> int:
    """Indian financial year (1 Apr - 31 Mar) containing d, identified by its starting calendar year."""
    return d.year if d.month >= 4 else d.year - 1


def fy_label(d: date) -> str:
    y = fy_start_year(d)
    return f"FY{y}-{(y + 1) % 100:02d}"


def fy_range(start_year: int) -> DateRange:
    """fy_range(2025) -> 2025-04-01 .. 2026-03-31 (FY2025-26)."""
    return DateRange(date(start_year, 4, 1), date(start_year + 1, 3, 31))


def fy_range_for(d: date) -> DateRange:
    return fy_range(fy_start_year(d))
