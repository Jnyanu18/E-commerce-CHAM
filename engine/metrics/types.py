"""Shared result types for the metric layer (shape per docs/CONTRACTS.md section 3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

Value = int | Decimal | None


class MetricError(ValueError):
    """Invalid request (unsupported filter / group_by, bad dates). The API should map this to HTTP 400."""


@dataclass(frozen=True)
class DateRange:
    """Inclusive range of IST calendar dates (orders.ordered_date_ist)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise MetricError(f"date_range start {self.start} is after end {self.end}")

    @classmethod
    def of(cls, value: "DateRange | tuple[date, date]") -> "DateRange":
        return value if isinstance(value, DateRange) else cls(value[0], value[1])

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


Filters = Mapping[str, "str | Iterable[str]"]


def normalise_filters(filters: Filters | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, val in (filters or {}).items():
        vals = [val] if isinstance(val, str) else list(val)
        out[key] = sorted({str(v) for v in vals})
    return out


@dataclass(frozen=True)
class Group:
    key: str
    value: Value

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": _json_value(self.value)}


@dataclass
class DataQuality:
    status: str  # ok | degraded | insufficient
    issues: list[str] = field(default_factory=list)
    row_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "issues": list(self.issues), "row_count": self.row_count}


def _json_value(v: Value) -> int | float | None:
    return float(v) if isinstance(v, Decimal) else v


@dataclass(frozen=True)
class MetricResult:
    metric: str
    value: Value
    unit: str  # paise | ratio | count
    currency: str | None  # "INR" for paise; None for ratio/count
    period: DateRange
    filters: dict[str, list[str]]
    group_by: str | None
    groups: list[Group]
    definition: str
    data_quality: DataQuality

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict in exactly the contract shape. Ratios are emitted as floats (4 dp)."""
        return {
            "metric": self.metric,
            "value": _json_value(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "period": self.period.to_dict(),
            "filters": {k: list(v) for k, v in self.filters.items()},
            "group_by": self.group_by,
            "groups": [g.to_dict() for g in self.groups],
            "definition": self.definition,
            "data_quality": self.data_quality.to_dict(),
        }
