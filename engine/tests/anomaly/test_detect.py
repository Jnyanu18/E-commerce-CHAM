from datetime import date, timedelta

import numpy as np

from anomaly import AnomalyConfig, detect_anomalies, detect_many
from intelligence.sources import StaticSource, series_from_values

START = date(2026, 6, 1)


def synth(n=90, seed=1, spike_at=None, spike=0.0, base=1_000_000):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    wk = np.array([0.9, 0.95, 1.0, 1.0, 1.05, 1.2, 1.1])[(t + START.weekday()) % 7]
    y = base * wk * (1 + 0.002 * t) * (1 + rng.normal(0, 0.02, n))
    if spike_at is not None:
        y[spike_at] += spike
    return [float(v) for v in y]


def test_planted_drop_found_and_weekend_peak_not_flagged():
    vals = synth(spike_at=60, spike=-500_000)
    r = detect_anomalies(series_from_values("net_revenue", START, vals))
    assert r["status"] == "ok"
    days = {a.date: a for a in r["anomalies"]}
    target = START + timedelta(days=60)
    assert target in days and days[target].direction == "down" and days[target].favourable is False
    assert days[target].severity in ("high", "critical")
    assert abs(days[target].expected - (vals[60] + 500_000)) < 0.1 * (vals[60] + 500_000)
    assert len(r["anomalies"]) <= 2  # weekly peaks are expected behaviour, not anomalies


def test_clean_series_false_positive_rate_low():
    flags = sum(len(detect_anomalies(series_from_values("net_revenue", START, synth(90, seed=s))) ["anomalies"]) for s in range(30))
    assert flags <= 6  # <= ~0.2 per 90-day series (observed rate is well below this)


def test_deviation_not_size_small_metric():
    vals = [v / 1e6 for v in synth(spike_at=40, spike=300_000)]  # MER-like ratio ~1.0
    r = detect_anomalies(series_from_values("mer", START, vals, "ratio"))
    assert r["anomalies"][0].date == START + timedelta(days=40)
    assert r["anomalies"][0].direction == "up"


def test_short_series_insufficient():
    r = detect_anomalies(series_from_values("net_revenue", START, synth(13)))
    assert r["status"] == "INSUFFICIENT_DATA" and "13" in r["reason"]
    assert detect_anomalies(series_from_values("net_revenue", START, synth(14)))["status"] == "ok"


def test_empty_and_undefined_and_duplicates():
    assert detect_anomalies(series_from_values("x", START, []))["status"] == "INSUFFICIENT_DATA"
    vals = synth(40)
    for i in range(0, 40, 2):
        vals[i] = None
    assert detect_anomalies(series_from_values("x", START, vals))["status"] == "INSUFFICIENT_DATA"
    s = series_from_values("x", START, synth(20))
    s.dates[5] = s.dates[4]
    assert detect_anomalies(s)["status"] == "INSUFFICIENT_DATA"


def test_missing_days_interpolated_never_flagged():
    vals = synth(60, spike_at=45, spike=-400_000)
    vals[10] = None
    r = detect_anomalies(series_from_values("net_revenue", START, vals))
    flagged = {a.date for a in r["anomalies"]}
    assert r["status"] == "ok" and START + timedelta(days=45) in flagged
    assert START + timedelta(days=10) not in flagged


def test_constant_series_no_crash():
    r = detect_anomalies(series_from_values("x", START, [100.0] * 30))
    assert r["status"] == "ok" and r["anomalies"] == []


def test_threshold_configurable_and_rto_up_is_unfavourable():
    vals = synth(spike_at=30, spike=200_000)
    s = series_from_values("expected_rto_loss", START, vals)
    lo = detect_anomalies(s, AnomalyConfig(threshold=2.5))["anomalies"]
    hi = detect_anomalies(s, AnomalyConfig(threshold=6.0))["anomalies"]
    assert len(lo) >= len(hi)
    a = [x for x in lo if x.date == START + timedelta(days=30)][0]
    assert a.direction == "up" and a.favourable is False


def test_detect_many_reports_unavailable_metric():
    src = StaticSource({"net_revenue": series_from_values("net_revenue", START, synth())})
    res = detect_many(src, "org", START, START + timedelta(days=89), ("net_revenue", "conversion"))
    assert res["net_revenue"]["status"] == "ok"
    assert res["conversion"]["status"] == "INSUFFICIENT_DATA"
