from datetime import date, timedelta

import pytest

from intelligence.experiments import load_city_sales
from intelligence.sources import MetricLayerSource, Series
from metrics import METRICS
from tests.metrics.fx import ORG, PERIOD, main_repo


def test_daily_series_equals_direct_metric_calls_and_keeps_none():
    repo = main_repo()
    src = MetricLayerSource(repo)
    s = src.series("net_revenue", ORG, PERIOD.start, PERIOD.end, "day")
    assert len(s.dates) == 7
    for d, v in zip(s.dates, s.values):
        direct = METRICS["net_revenue"].callable(ORG, (d, d), None, None, repo=repo).value
        # a day with no orders is undefined for the metric but a real zero in an order-based daily series (an outage is the anomaly)
        assert v == (0.0 if direct is None else float(direct))
    assert s.unit == "paise" and len(s.calls) == 1  # one metric call per series


def test_weekly_series_uses_group_by_week():
    repo = main_repo()
    s = MetricLayerSource(repo).series("net_revenue", ORG, PERIOD.start, PERIOD.end, "week")
    res = METRICS["net_revenue"].callable(ORG, (PERIOD.start, PERIOD.end), None, "week", repo=repo)
    assert s.grain == "week" and s.values == [float(g.value) for g in res.groups]
    assert s.calls[0]["metric"] == "net_revenue"


def test_unknown_metric_and_extra_provider_and_unsupported_grain():
    repo = main_repo()
    src = MetricLayerSource(repo, extra={"sessions": lambda org, a, b, g: Series("sessions", "count", [a], [10.0])})
    assert src.series("sessions", ORG, PERIOD.start, PERIOD.end).values == [10.0]
    with pytest.raises(KeyError):
        src.series("bogus_metric", ORG, PERIOD.start, PERIOD.end)
    with pytest.raises(ValueError):
        src.series("platform_roas", ORG, PERIOD.start, PERIOD.end, "month")


def test_city_sales_loader_sums_to_metric_total():
    repo = main_repo()
    cs = load_city_sales(repo, ORG, date(2026, 9, 14), date(2026, 9, 20))
    total = METRICS["net_revenue"].callable(ORG, (date(2026, 9, 14), date(2026, 9, 20)), None, None, repo=repo).value
    assert cs.weeks == [date(2026, 9, 14)] and sum(v[0] for v in cs.sales.values()) == float(total)


def test_new_canonical_series_and_no_traffic_is_none_not_zero():
    repo = main_repo()
    src = MetricLayerSource(repo)
    o = src.series("orders", ORG, PERIOD.start, PERIOD.end, "day")
    assert len(o.values) == 7 and sum(o.values) > 0
    sess = src.series("sessions", ORG, PERIOD.start, PERIOD.end, "day")
    assert all(v is None for v in sess.values)  # no traffic_daily rows: undefined, never zero
    assert all(v is None for v in src.series("conversion", ORG, PERIOD.start, PERIOD.end, "day").values)
    w = src.series("ad_spend:meta", ORG, PERIOD.start, PERIOD.end, "week")
    assert w.metric == "ad_spend:meta" and len(w.values) == 1
