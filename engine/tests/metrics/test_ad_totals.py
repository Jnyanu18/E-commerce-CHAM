"""ad_spend, platform_reported_revenue, city_tier grouping (fixtures in fx.py)."""
from datetime import date

import pytest

from metrics import InMemoryRepository, MetricError, ad_spend, contribution_margin, net_revenue, platform_reported_revenue as prr
from metrics.repository import AdSpendRow
from tests.metrics.fx import ORG, PERIOD, main_repo, repo_with_spend
from metrics import DateRange


def groups(res):
    return {g.key: g.value for g in res.groups}


def test_ad_spend():
    # meta 7 x 10000 = 70000 ; google 20000 + 10000 = 30000 ; total 100000 over 9 rows
    repo = repo_with_spend()
    r = ad_spend(ORG, PERIOD, repo=repo)
    assert r.value == 100000 and r.unit == "paise" and r.data_quality.row_count == 9 and r.data_quality.status == "ok"
    assert groups(ad_spend(ORG, PERIOD, group_by="platform", repo=repo)) == {"meta": 70000, "google": 30000}
    assert groups(ad_spend(ORG, PERIOD, group_by="week", repo=repo)) == {"2026-09-14": 100000}
    assert ad_spend(ORG, PERIOD, {"platform": ["google"]}, repo=repo).value == 30000


def test_ad_spend_empty_and_invalid():
    r = ad_spend(ORG, PERIOD, repo=InMemoryRepository())
    assert r.value is None and r.data_quality.status == "insufficient"
    with pytest.raises(MetricError):
        ad_spend(ORG, PERIOD, group_by="channel", repo=repo_with_spend())


def test_platform_reported_revenue():
    # meta 7 x 25000 = 175000 ; google 70000 (14 Sep) ; google 15 Sep is null -> excluded: total 245000
    repo = repo_with_spend()
    r = prr(ORG, PERIOD, repo=repo)
    assert r.value == 245000 and "platform-reported, not causal" in r.definition
    assert "platform_revenue_missing_for_rows: 1 of 9 (excluded)" in r.data_quality.issues
    assert r.data_quality.status == "degraded" and r.data_quality.row_count == 8
    assert groups(prr(ORG, PERIOD, group_by="platform", repo=repo)) == {"meta": 175000, "google": 70000}
    assert prr(ORG, PERIOD, {"platform": ["meta"]}, repo=repo).value == 175000


def test_platform_reported_revenue_null_cases():
    assert prr(ORG, PERIOD, repo=InMemoryRepository()).value is None
    repo = InMemoryRepository().add(ORG, ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 500, None)])
    r = prr(ORG, PERIOD, repo=repo)
    assert r.value is None and r.data_quality.status == "insufficient"
    repo2 = InMemoryRepository().add(ORG, ad_spend=[AdSpendRow("meta", "m", date(2026, 9, 14), 500, 0)])
    assert prr(ORG, PERIOD, repo=repo2).value == 0  # reported zero is a real zero


def test_group_by_city_tier_ledger_metrics():
    # O4 (22 Sep, tier_1): gross 7000 -> net 7000 ; COGS A 5000 -> CM 2000. metro: 130000 net / 87500 CM.
    dr = DateRange(date(2026, 9, 14), date(2026, 9, 27))
    repo = main_repo()
    assert groups(net_revenue(ORG, dr, group_by="city_tier", repo=repo)) == {"metro": 130000, "tier_1": 7000}
    assert groups(contribution_margin(ORG, dr, group_by="city_tier", repo=repo)) == {"metro": 87500, "tier_1": 2000}
