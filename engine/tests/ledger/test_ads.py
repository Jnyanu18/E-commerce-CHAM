"""Ad raw rows -> ad_spend_daily. Fixtures come from the connectors' own SYNTHETIC ad fixtures (test-only import).
In-memory always; real Postgres when DATABASE_URL is reachable. Nothing here is live API output."""
import copy
import os
from datetime import date
from decimal import Decimal

import pytest

from ledger.pipeline import run_transform
from ledger.repository import InMemoryRepository
from tests.connectors.test_ads import grow, minsight
from tests.ledger.fixtures import ORG, ORG2
from transforms.ads import find_ad_spend_gaps, transform_google_ads_row, transform_meta_insights
from transforms.common import TransformError

M = lambda cid, day, **kw: (f"campaign:111222333:{cid}:{day}", minsight(cid, day, **kw))  # noqa: E731


# ------------------------------------------------------------------ pure transforms
def test_meta_exact_paise_and_fields():
    ext, p = M("42", "2026-10-01", spend="100.50")
    p["action_values"] = [{"action_type": "purchase", "value": "1999.99"}, {"action_type": "add_to_cart", "value": "5"}]
    b = transform_meta_insights(ORG, p, raw_external_id=ext)
    [r] = b.rows
    assert r == {"platform": "meta", "campaign_id": "42", "campaign_name": "C42", "date": date(2026, 10, 1),
                 "spend_paise": 10050, "impressions": 1000, "clicks": 20, "platform_reported_conversions": Decimal(2),
                 "platform_reported_revenue_paise": 199999}
    assert b.raw_ref == {"source": "meta_insights", "external_id": ext}


def test_meta_revenue_and_conversions_null_when_not_fetched_never_zero():
    ext, p = M("42", "2026-10-01")
    del p["actions"]
    [r] = transform_meta_insights(ORG, p, raw_external_id=ext).rows
    assert r["platform_reported_revenue_paise"] is None and r["platform_reported_conversions"] is None
    b = transform_meta_insights(ORG, minsight("42", "2026-10-01"), raw_external_id=ext)
    assert b.rows[0]["platform_reported_revenue_paise"] is None            # fixture has actions but no action_values
    assert any(i.startswith("platform_revenue_unavailable") for i in b.issues)


def test_meta_non_inr_rejected_not_converted_and_non_campaign_levels_skipped():
    ext, p = M("42", "2026-10-01")
    p["account_currency"] = "USD"
    b = transform_meta_insights(ORG, p, raw_external_id=ext)
    assert b.rows == [] and any(i.startswith("non_inr_currency") for i in b.issues)
    p2 = minsight("42", "2026-10-01")
    del p2["account_currency"]
    assert transform_meta_insights(ORG, p2, raw_external_id=ext).rows == []
    b = transform_meta_insights(ORG, minsight("42", "2026-10-01"), raw_external_id="adset:111222333:9:2026-10-01")
    assert b.rows == [] and any(i.startswith("meta_row_skipped") for i in b.issues)
    multi = minsight("42", "2026-10-01")
    multi["date_stop"] = "2026-10-03"
    assert transform_meta_insights(ORG, multi, raw_external_id=ext).rows == []


def test_meta_invalid_values_raise():
    ext, p = M("42", "2026-10-01", spend="lots")
    with pytest.raises(TransformError):
        transform_meta_insights(ORG, p, raw_external_id=ext)
    ext, p = M("42", "2026-10-01")
    p["clicks"] = "1.5"
    with pytest.raises(TransformError):
        transform_meta_insights(ORG, p, raw_external_id=ext)
    ext, p = M("42", "2026-13-45")
    with pytest.raises(TransformError):
        transform_meta_insights(ORG, p, raw_external_id=ext)
    with pytest.raises(TransformError):
        transform_meta_insights(ORG, "x")


def test_google_micros_exact_decimal_conversion_and_rounding():
    assert transform_google_ads_row(ORG, grow(7, "2026-10-01", "12500000")).rows[0]["spend_paise"] == 1250
    assert transform_google_ads_row(ORG, grow(7, "2026-10-01", "0")).rows[0]["spend_paise"] == 0
    assert transform_google_ads_row(ORG, grow(7, "2026-10-01", "12345678")).rows[0]["spend_paise"] == 1235   # 1234.5678 half-up
    assert transform_google_ads_row(ORG, grow(7, "2026-10-01", "4999")).rows[0]["spend_paise"] == 0       # 0.4999 paise
    assert transform_google_ads_row(ORG, grow(7, "2026-10-01", "5000")).rows[0]["spend_paise"] == 1       # 0.5 paise -> half-up
    big = transform_google_ads_row(ORG, grow(7, "2026-10-01", "123456789012345678")).rows[0]["spend_paise"]
    assert big == 12345678901235                                                                          # beyond float precision


def test_google_fields_issues_and_null_conversions():
    b = transform_google_ads_row(ORG, grow(7, "2026-10-01"), raw_external_id="1234567890:7:2026-10-01")
    [r] = b.rows
    assert (r["platform"], r["campaign_id"], r["campaign_name"], r["impressions"], r["clicks"]) == ("google", "7", "G7", 500, 10)
    assert r["platform_reported_conversions"] is None and r["platform_reported_revenue_paise"] is None
    assert any(i.startswith("currency_unverified") for i in b.issues) and any(i.startswith("cost_micros_unit_assumed") for i in b.issues)
    g = grow(7, "2026-10-01")
    g["metrics"].update(conversions="3.5", conversionsValue="750.25")
    r = transform_google_ads_row(ORG, g).rows[0]
    assert r["platform_reported_conversions"] == Decimal("3.5") and r["platform_reported_revenue_paise"] == 75025


def test_google_invalid_rows_raise():
    for bad in ({}, {"campaign": {"id": "1"}, "segments": {}}, grow(7, "2026-10-01", "abc"),
                {**grow(7, "2026-10-01"), "metrics": {"impressions": "5"}}):
        with pytest.raises(TransformError):
            transform_google_ads_row(ORG, bad)


def test_find_gaps():
    d = lambda n: date(2026, 10, n)  # noqa: E731
    assert find_ad_spend_gaps(set()) == [] and find_ad_spend_gaps({d(1)}) == []
    assert find_ad_spend_gaps({d(1), d(2), d(5)}) == [d(3), d(4)]


# ------------------------------------------------------------------ pipeline (in memory)
def seeded():
    repo = InMemoryRepository()
    for day in ("2026-10-01", "2026-10-02", "2026-10-04"):                      # 10-03 missing
        ext, p = M("42", day)
        repo.add_raw_record(ORG, "meta_insights", ext, p)
    repo.add_raw_record(ORG, "meta_insights", "adset:111222333:9:2026-10-01", minsight("42", "2026-10-01", spend="999.00"))
    repo.add_raw_record(ORG, "google_ads_row", "1234567890:7:2026-10-01", grow(7, "2026-10-01"))
    repo.add_raw_record(ORG, "google_ads_row", "1234567890:7:2026-10-02", grow(7, "2026-10-02", "30000000"))
    return repo


def test_pipeline_ad_spend_rows_idempotent_and_gap_reported():
    repo = seeded()
    s = run_transform(repo, ORG)
    assert s.errors == [] and s.ad_spend_inserted == 5 and len(repo.ad_spend) == 5
    assert repo.ad_spend[(ORG, "meta", "42", date(2026, 10, 1))]["spend_paise"] == 10050     # adset row did not double count
    assert repo.ad_spend[(ORG, "google", "7", date(2026, 10, 2))]["spend_paise"] == 3000
    assert s.ad_spend_missing == [{"platform": "meta", "issue": "ad_spend_missing", "dates": ["2026-10-03"]}]
    before = copy.deepcopy(repo.ad_spend)
    s2 = run_transform(repo, ORG)
    assert repo.ad_spend == before and s2.ad_spend_inserted == 0 and s2.ad_spend_updated == 5


def test_repull_overwrites_revised_day_not_duplicates():
    repo = seeded()
    run_transform(repo, ORG)
    ext, p = M("42", "2026-10-01", spend="120.25")                                         # later revised
    repo.add_raw_record(ORG, "meta_insights", ext, p)
    run_transform(repo, ORG)
    assert len(repo.ad_spend) == 5 and repo.ad_spend[(ORG, "meta", "42", date(2026, 10, 1))]["spend_paise"] == 12025


def test_bad_ad_rows_collected_and_tenants_isolated():
    repo = seeded()
    repo.add_raw_record(ORG, "meta_insights", "campaign:1:1:2026-10-09", {"campaign_id": "1", "account_currency": "INR", "date_start": "2026-10-09", "spend": "x"})
    repo.add_raw_record(ORG2, "google_ads_row", "1234567890:7:2026-10-01", grow(7, "2026-10-01"))
    s = run_transform(repo, ORG)
    assert len(s.errors) == 1 and all(k[0] == ORG for k in repo.ad_spend)
    run_transform(repo, ORG2)
    assert {k[0] for k in repo.ad_spend} == {ORG, ORG2} and len([k for k in repo.ad_spend if k[0] == ORG2]) == 1


# ------------------------------------------------------------------ real Postgres
@pytest.fixture()
def conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    try:
        c = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("database unreachable")
    for o, n in ((ORG, "ads-1"), (ORG2, "ads-2")):
        c.execute("delete from orgs where id=%s", (o,))
        c.execute("insert into orgs (id, name) values (%s,%s)", (o, n))
    yield c
    c.execute("delete from orgs where id = any(%s::uuid[])", ([ORG, ORG2],))
    c.close()


def _raw(conn, org, source, ext, payload):
    from psycopg.types.json import Jsonb
    conn.execute("insert into raw_records (org_id, source, external_id, payload) values (%s,%s,%s,%s) "
                 "on conflict (org_id, source, external_id) do update set payload=excluded.payload", (org, source, ext, Jsonb(payload)))


def test_postgres_exact_paise_idempotency_overwrite_and_isolation(conn):
    from ledger.pg_repository import PsycopgRepository
    repo = PsycopgRepository(conn)
    for day in ("2026-10-01", "2026-10-02", "2026-10-04"):
        ext, p = M("42", day)
        p["action_values"] = [{"action_type": "purchase", "value": "1999.99"}] if day == "2026-10-01" else None
        if p["action_values"] is None:
            del p["action_values"]
        _raw(conn, ORG, "meta_insights", ext, p)
    _raw(conn, ORG, "meta_insights", "adset:111222333:9:2026-10-01", minsight("42", "2026-10-01", spend="999.00"))
    _raw(conn, ORG, "google_ads_row", "1234567890:7:2026-10-01", grow(7, "2026-10-01", "12345678"))
    _raw(conn, ORG2, "google_ads_row", "1234567890:7:2026-10-01", grow(7, "2026-10-01", "50000000"))
    s = run_transform(repo, ORG)
    assert s.errors == [] and s.ad_spend_inserted == 4
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    assert q("select spend_paise, impressions, clicks, platform_reported_revenue_paise, platform_reported_conversions "
             "from ad_spend_daily where org_id=%s and platform='meta' and date='2026-10-01'", ORG) == [(10050, 1000, 20, 199999, Decimal(2))]
    assert q("select platform_reported_revenue_paise from ad_spend_daily where org_id=%s and date='2026-10-02'", ORG)[0][0] is None   # NULL not 0
    assert q("select spend_paise from ad_spend_daily where org_id=%s and platform='google'", ORG) == [(1235,)]
    assert s.ad_spend_missing == [{"platform": "meta", "issue": "ad_spend_missing", "dates": ["2026-10-03"]}]
    assert q("select count(*) from ad_spend_daily where org_id=%s", ORG2) == [(0,)]                                         # isolation
    total = q("select count(*), sum(spend_paise) from ad_spend_daily where org_id=%s", ORG)
    s2 = run_transform(repo, ORG)
    assert q("select count(*), sum(spend_paise) from ad_spend_daily where org_id=%s", ORG) == total and s2.ad_spend_updated == 4
    ext, p = M("42", "2026-10-01", spend="120.25")                                                                          # revised re-pull
    _raw(conn, ORG, "meta_insights", ext, p)
    run_transform(repo, ORG)
    assert q("select count(*) from ad_spend_daily where org_id=%s", ORG) == [(4,)]
    assert q("select spend_paise, platform_reported_revenue_paise from ad_spend_daily where org_id=%s and platform='meta' and date='2026-10-01'", ORG) == [(12025, None)]
    run_transform(repo, ORG2)
    assert q("select spend_paise from ad_spend_daily where org_id=%s", ORG2) == [(5000,)]
    assert q("select spend_paise from ad_spend_daily where org_id=%s and platform='google'", ORG) == [(1235,)]
