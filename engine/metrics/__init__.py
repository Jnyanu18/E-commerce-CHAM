"""Canonical metric layer. Every business metric is defined once, here."""
from .finance import contribution_margin, net_revenue
from .marketing import blended_cac, contribution_after_marketing, mer, platform_roas
from .marketing_spend import ad_spend, platform_reported_revenue
from .periods import fy_label, fy_range, fy_range_for, fy_start_year, week_range, week_start
from .registry import METRICS, MetricSpec, run_metric
from .repository import InMemoryRepository, MetricsRepository, PostgresRepository
from .rto import expected_rto_loss
from .types import DataQuality, DateRange, Group, MetricError, MetricResult

__all__ = [
    "METRICS", "MetricSpec", "run_metric", "MetricResult", "DataQuality", "DateRange", "Group", "MetricError",
    "net_revenue", "contribution_margin", "expected_rto_loss", "blended_cac", "mer", "platform_roas",
    "contribution_after_marketing", "ad_spend", "platform_reported_revenue", "InMemoryRepository", "MetricsRepository", "PostgresRepository",
    "week_start", "week_range", "fy_range", "fy_range_for", "fy_label", "fy_start_year",
]
