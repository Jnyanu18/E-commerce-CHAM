"""Helpers that assemble MetricResult objects consistently."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

from .types import DataQuality, DateRange, Filters, Group, MetricResult, Value, normalise_filters

CURRENCY = "INR"
NO_ORDERS = "no_orders_in_period"


def ratio(num: int, den: int) -> Decimal | None:
    """num/den to 4 dp (half-up); None when den <= 0 (never 0 or inf)."""
    if den <= 0:
        return None
    return (Decimal(num) / Decimal(den)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def div_paise(num: int, den: int) -> int | None:
    """Integer paise num/den rounded half-up; None when den <= 0."""
    if den <= 0:
        return None
    return (2 * num + den) // (2 * den)


def make_result(
    *,
    metric: str,
    value: Value,
    unit: str,
    dr: DateRange,
    filters: Filters | None,
    group_by: str | None,
    group_values: Mapping[str, Value] | None,
    definition: str,
    issues: list[str],
    row_count: int,
) -> MetricResult:
    issues = list(issues)
    groups = [Group(k, v) for k, v in sorted((group_values or {}).items())]
    undefined = [g.key for g in groups if g.value is None]
    if undefined and value is not None:
        issues.append(f"groups_with_undefined_value: {undefined}")
    status = "insufficient" if value is None else ("degraded" if issues else "ok")
    return MetricResult(
        metric=metric,
        value=value,
        unit=unit,
        currency=CURRENCY if unit == "paise" else None,
        period=dr,
        filters=normalise_filters(filters),
        group_by=group_by,
        groups=groups,
        definition=definition,
        data_quality=DataQuality(status=status, issues=issues, row_count=row_count),
    )
