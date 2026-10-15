from datetime import date, timedelta

import pytest

from intelligence.rootcause import decompose, explain_revenue_change, explain_roas_change, lmdi
from intelligence.sources import StaticSource, series_from_values

P0 = (date(2026, 8, 1), date(2026, 8, 14))
P1 = (date(2026, 8, 15), date(2026, 8, 28))


def test_lmdi_sums_exactly():
    f0 = {"a": 1000.0, "b": 0.02, "c": 150_000.0}
    f1 = {"a": 1100.0, "b": 0.015, "c": 160_000.0}
    c = lmdi(f0, f1)
    assert sum(c.values()) == pytest.approx(1100 * 0.015 * 160_000 - 1000 * 0.02 * 150_000, rel=1e-12)


def test_paise_contributions_sum_exactly_integer():
    f0 = {"sessions": 12345.0, "cvr": 0.0213, "aov": 149_900.0}
    f1 = {"sessions": 12001.0, "cvr": 0.0161, "aov": 151_000.0}
    r = decompose("net_revenue", ("sessions", "cvr", "aov"), f0, f1, unit="paise", counts=[300, 190])
    assert sum(d.contribution for d in r["drivers"]) == r["total_change"]
    assert all(isinstance(d.contribution, int) for d in r["drivers"])
    assert r["drivers"][0].rank == 1 and r["drivers"][0].driver == "cvr"


def test_zero_change_and_zero_factor():
    f = {"a": 2.0, "b": 3.0}
    r = decompose("x", ("a", "b"), f, f, unit="ratio")
    assert r["total_change"] == 0 and all(d.contribution == 0 for d in r["drivers"]) and r["drivers"][0].confidence == "low"
    assert decompose("x", ("a", "b"), f, {"a": 0.0, "b": 3.0}, unit="ratio")["status"] == "INSUFFICIENT_DATA"
    assert decompose("x", ("a", "b"), f, {"a": 1.0}, unit="ratio")["status"] == "INSUFFICIENT_DATA"


def _src(sess_drop=1.0, cvr_drop=1.0, aov_up=1.0):
    """14 days at baseline then 14 days with a planted CVR shift only (sessions/AOV unchanged)."""
    n = 28
    sess, orders, rev = [], [], []
    for i in range(n):
        shifted = i >= 14
        s = 2000 * (sess_drop if shifted else 1)
        o = s * 0.02 * (cvr_drop if shifted else 1)
        sess.append(s); orders.append(o); rev.append(o * 150_000 * (aov_up if shifted else 1))
    st = date(2026, 8, 1)
    return StaticSource({"sessions": series_from_values("sessions", st, sess, "count"),
                         "orders": series_from_values("orders", st, orders, "count"),
                         "net_revenue": series_from_values("net_revenue", st, rev)})


def test_planted_cvr_shift_is_top_driver_and_sums():
    r = explain_revenue_change(_src(cvr_drop=0.7), "org", P0, P1)
    assert r["status"] == "ok"
    top = r["drivers"][0]
    assert top.driver.startswith("conversion") and top.share_of_change == pytest.approx(1.0, abs=1e-3)
    assert top.confidence in ("high", "medium")
    by = {d.driver.split()[0]: d for d in r["drivers"]}
    assert abs(by["sessions"].contribution) <= 1 and abs(by["AOV"].contribution) <= 1
    assert sum(d.contribution for d in r["drivers"]) == r["total_change"]
    assert r["total_change"] == pytest.approx(-0.3 * r["from_value"], rel=1e-3)


def test_mixed_shift_ranks_and_sums():
    r = explain_revenue_change(_src(sess_drop=0.9, cvr_drop=0.8, aov_up=1.05), "org", P0, P1)
    assert sum(d.contribution for d in r["drivers"]) == r["total_change"]
    assert [d.rank for d in r["drivers"]] == [1, 2, 3]
    assert r["drivers"][0].driver.startswith("conversion")


def test_missing_sessions_is_insufficient_not_invented():
    src = _src()
    del src._s["sessions"]
    r = explain_revenue_change(src, "org", P0, P1)
    assert r["status"] == "INSUFFICIENT_DATA" and "sessions" in r["reason"]


def test_roas_chain_ctr_planted():
    st = date(2026, 8, 1)
    cols = {k: [] for k in ("ad_spend", "impressions", "clicks", "conversions", "platform_revenue")}
    for i in range(28):
        ctr = 0.012 if i < 14 else 0.008
        imp = 100_000; clk = imp * ctr; conv = clk * 0.03
        cols["ad_spend"].append(5_000_000); cols["impressions"].append(imp); cols["clicks"].append(clk)
        cols["conversions"].append(conv); cols["platform_revenue"].append(conv * 120_000)
    src = StaticSource({k: series_from_values(k, st, v, "count") for k, v in cols.items()})
    r = explain_roas_change(src, "org", P0, P1)
    assert r["status"] == "ok" and r["drivers"][0].driver == "CTR"
    assert sum(d.contribution for d in r["drivers"]) == pytest.approx(r["total_change"], rel=1e-9)
    assert "not causal" in r["label"]
    cpm = [d for d in r["drivers"] if d.driver.startswith("CPM")][0]
    assert abs(cpm.contribution) < 1e-9 and "Rs 500.00 -> Rs 500.00" in cpm.evidence
