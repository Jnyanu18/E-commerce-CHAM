import json
from datetime import date, datetime, timezone

import pytest

from metrics import METRICS, DateRange, fy_label, fy_range, fy_range_for, fy_start_year, run_metric, week_range, week_start
from metrics.repository import PostgresRepository
from tests.metrics.fx import ORG, PERIOD, repo_with_spend


def test_week_start_is_monday_ist_week():
    assert week_start(date(2026, 9, 14)) == date(2026, 9, 14)  # Monday
    assert week_start(date(2026, 9, 20)) == date(2026, 9, 14)  # Sunday
    assert week_start(date(2026, 9, 21)) == date(2026, 9, 21)
    assert week_range(date(2026, 9, 17)) == DateRange(date(2026, 9, 14), date(2026, 9, 20))
    assert week_start(date(2026, 1, 1)) == date(2025, 12, 29)  # across year boundary


def test_indian_fy():
    assert fy_range(2025) == DateRange(date(2025, 4, 1), date(2026, 3, 31))
    assert fy_start_year(date(2026, 3, 31)) == 2025
    assert fy_start_year(date(2026, 4, 1)) == 2026
    assert fy_label(date(2026, 3, 31)) == "FY2025-26"
    assert fy_label(date(2026, 4, 1)) == "FY2026-27"
    assert fy_label(date(2099, 12, 1)) == "FY2099-00"
    assert fy_range_for(date(2026, 10, 14)) == DateRange(date(2026, 4, 1), date(2027, 3, 31))
    assert fy_range(2023).end == date(2024, 3, 31)  # leap-year FY


def test_registry_lists_every_metric_with_one_callable():
    assert set(METRICS) == {"net_revenue", "contribution_margin", "expected_rto_loss", "blended_cac", "mer", "platform_roas", "contribution_after_marketing", "ad_spend", "platform_reported_revenue",
                           "orders", "aov", "sessions", "ad_impressions", "ad_clicks", "platform_reported_conversions"}
    repo = repo_with_spend()
    for name, spec in METRICS.items():
        assert spec.name == name and spec.description and spec.callable and "org_id" in spec.params
        res = run_metric(name, ORG, PERIOD, None, None, repo=repo, **({"min_history": 1} if name == "expected_rto_loss" else {}))
        assert res.metric == name
        assert res.definition == spec.description
        json.dumps(res.to_dict())  # JSON-safe for API and tool results


def test_registry_unknown_metric():
    with pytest.raises(KeyError):
        run_metric("nope", ORG, PERIOD, repo=repo_with_spend())


def test_registry_declares_group_by_support():
    assert "pincode" in METRICS["net_revenue"].allowed_group_by
    assert METRICS["mer"].allowed_group_by == ("day", "week")
    assert METRICS["platform_roas"].allowed_group_by == ("day", "week", "platform")
    assert "day" in METRICS["net_revenue"].allowed_group_by


class _Cur:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def cursor(self):
        return _Cur(self.rows, self.log)


def test_postgres_repository_maps_rows_in_select_order():
    at = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)
    row = ("uuid-1", "shopify", "e1", at, date(2026, 9, 14), "cod", "placed", 100, 10, 5, 0, 0, "ck", "Pune", "tier_1", "411001")
    conn = _Conn([row])
    (o,) = PostgresRepository(conn).orders("org", date(2026, 9, 1), date(2026, 9, 30))
    assert o.city_tier == "tier_1" and o.gross_revenue_paise == 100 and o.pincode == "411001"
    sql, params = conn.log[0]
    assert "org_id = %s" in sql and params == ("org", date(2026, 9, 1), date(2026, 9, 30))
    assert "sum(" not in sql.lower() and "group by" not in sql.lower()  # no formulas/aggregation in SQL
    assert PostgresRepository(_Conn([])).items("org", []) == []
