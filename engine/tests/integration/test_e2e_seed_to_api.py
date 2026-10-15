"""END-TO-END: synthetic brand -> raw_records -> worker (transform job) -> ledger -> metric layer -> HTTP API.

Expected values come from `seed.raw.ground_truth`, which sums the canonical seed rows directly (no metric-layer
code), so agreement is a genuine cross-check of connectors' shapes, ledger transforms, DB schema, metric
definitions and API. Runs only against a local Postgres (DATABASE_URL); everything is synthetic.
"""
import base64, hashlib, hmac, json, os, time, uuid
from decimal import Decimal, ROUND_HALF_UP

import psycopg
import pytest
from fastapi.testclient import TestClient

URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif("localhost" not in URL, reason="needs DATABASE_URL on localhost (writes test data)")

from app.api import get_conn
from app.main import app
from seed.loader import load
from seed.raw import FEE_RATES_BPS, ground_truth
from worker.main import drain
from worker.queue import enqueue

SECRET = "e2e-jwt-secret-with-enough-length-12345"
START, END = "2025-04-01", "2025-06-30"


def jwt(sub):
    b = lambda d: base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    h = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    p = b(json.dumps({"sub": sub, "exp": time.time() + 600}).encode())
    return f"{h}.{p}.{b(hmac.new(SECRET.encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest())}"


@pytest.fixture(scope="module")
def world():
    os.environ["SUPABASE_JWT_SECRET"] = SECRET
    os.environ.pop("API_DEV_AUTH_BYPASS", None)
    conn = psycopg.connect(URL, autocommit=True)
    orgs = {}
    for name, seed in (("A", 7), ("B", 99)):
        user = str(uuid.uuid4())
        conn.execute("insert into auth.users(id, email) values (%s, %s)", (user, f"{name}@e2e.invalid"))
        org, data = load(conn, name=f"E2E {name}", weeks=8, seed=seed)
        conn.execute("insert into org_members(org_id, user_id, role) values (%s,%s,'owner')", (org, user))
        enqueue(conn, "transform", org, {"fee_rates_bps": FEE_RATES_BPS})
        orgs[name] = {"org": org, "user": user, "token": jwt(user), "gt": ground_truth(data), "data": data}
    assert drain(conn) == 2
    app.dependency_overrides[get_conn] = _conn_dep
    yield conn, orgs, TestClient(app)
    app.dependency_overrides.clear()
    conn.close()


def _conn_dep():
    with psycopg.connect(URL, autocommit=True) as c:
        yield c


def metric(client, o, name, group_by=None, **extra):
    params = {"org_id": o["org"], "start": START, "end": END, **extra}
    if group_by:
        params["group_by"] = group_by
    r = client.get(f"/metrics/{name}", params=params, headers={"authorization": f"Bearer {o['token']}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_jobs_completed_cleanly(world):
    conn, _, _ = world
    assert conn.execute("select status, count(*) from jobs group by 1").fetchall() == [("done", 2)]


def test_orders_and_statuses_match_seed(world):
    conn, orgs, _ = world
    for o in orgs.values():
        rows = dict(conn.execute("select status, count(*) from orders where org_id=%s group by 1", (o["org"],)).fetchall())
        assert rows == o["gt"]["status_counts"]
        assert sum(rows.values()) == o["gt"]["orders"]


def test_net_revenue_total_and_by_channel_match_ground_truth(world):
    _, orgs, client = world
    for o in orgs.values():
        r = metric(client, o, "net_revenue")
        assert r["value"] == o["gt"]["net_revenue_paise"] and r["data_quality"]["status"] == "ok"
        by = {g["key"]: g["value"] for g in metric(client, o, "net_revenue", "channel")["groups"]}
        assert by == o["gt"]["by_channel_net_revenue"]


def test_contribution_margin_matches_ground_truth_to_the_paisa(world):
    _, orgs, client = world
    for o in orgs.values():
        r = metric(client, o, "contribution_margin")
        assert r["value"] == o["gt"]["contribution_margin_paise"]
        # the prepaid gateway fee is an estimate, and the metric layer must say so rather than hide it
        assert r["data_quality"]["status"] == "degraded"
        assert any(i.startswith("fees_estimated_for_orders") for i in r["data_quality"]["issues"])


def test_marketing_metrics_match_ground_truth(world):
    _, orgs, client = world
    for o in orgs.values():
        gt = o["gt"]
        assert metric(client, o, "ad_spend")["value"] == gt["ad_spend_paise"]
        assert metric(client, o, "ad_spend", "platform")["groups"] == [
            {"key": "google", "value": gt["ad_spend_paise"] - gt["meta_spend_paise"]},
            {"key": "meta", "value": gt["meta_spend_paise"]}]
        pr = metric(client, o, "platform_reported_revenue")
        assert pr["value"] == gt["meta_platform_revenue_paise"]  # Google conversions are not fetched -> NULL, excluded
        assert "not causal" in pr["definition"]
        mer = Decimal(gt["net_revenue_paise"]) / Decimal(gt["ad_spend_paise"])
        assert metric(client, o, "mer")["value"] == pytest.approx(float(mer.quantize(Decimal("0.0001"), ROUND_HALF_UP)), abs=1e-4)
        cam = metric(client, o, "contribution_after_marketing")
        assert cam["value"] == gt["contribution_margin_paise"] - gt["ad_spend_paise"]


def test_transform_rerun_is_idempotent(world):
    conn, orgs, client = world
    o = orgs["A"]
    counts = lambda: [conn.execute(f"select count(*) from {t} where org_id=%s", (o["org"],)).fetchone()[0]
                      for t in ("orders", "order_items", "fees", "returns", "ad_spend_daily", "raw_records")]
    before, cm = counts(), metric(client, o, "contribution_margin")["value"]
    enqueue(conn, "transform", o["org"], {"fee_rates_bps": FEE_RATES_BPS})
    assert drain(conn) == 1
    assert counts() == before and metric(client, o, "contribution_margin")["value"] == cm


def test_tenant_isolation_and_auth(world):
    _, orgs, client = world
    a, b = orgs["A"], orgs["B"]
    assert a["gt"]["net_revenue_paise"] != b["gt"]["net_revenue_paise"]
    params = {"org_id": b["org"], "start": START, "end": END}
    # user A asking for org B's numbers
    r = client.get("/metrics/net_revenue", params=params, headers={"authorization": f"Bearer {a['token']}"})
    assert r.status_code == 403
    assert client.get("/metrics/net_revenue", params=params).status_code == 401


def test_customer_keys_join_across_sources(world):
    """Shopify (hashed at the edge) and marketplace CSV rows (raw email hashed by the ledger) share one key space."""
    conn, orgs, _ = world
    o = orgs["A"]
    from app import identity
    emails = {x["customer_email"] for x in o["data"]["orders"]}
    keys = {r[0] for r in conn.execute("select distinct customer_key from orders where org_id=%s", (o["org"],)).fetchall()}
    assert None not in keys and keys == {identity.hash_email(e) for e in emails}
    multi = conn.execute("select count(*) from (select customer_key from orders where org_id=%s group by 1 "
                         "having count(distinct channel) > 1) t", (o["org"],)).fetchone()[0]
    assert multi > 0  # at least some customers really span channels


def test_profit_waterfall_identities_hold_through_the_api(world):
    """The UI draws the waterfall from these metrics without doing arithmetic, so the steps must add up exactly."""
    _, orgs, client = world
    steps = client.get("/metrics").json()["waterfall"]
    assert [s["metric"] for s in steps][0] == "gross_revenue" and steps[-1]["metric"] == "contribution_after_marketing"
    for o in orgs.values():
        v = {s["metric"]: metric(client, o, s["metric"])["value"] for s in steps}
        assert v["gross_revenue"] - v["discounts"] - v["taxes"] - v["refunds"] == v["net_revenue"] == o["gt"]["net_revenue_paise"]
        assert v["net_revenue"] - v["cogs"] - v["fees"] - v["shipping_cost"] - v["return_cost"] == v["contribution_margin"] \
            == o["gt"]["contribution_margin_paise"]
        assert v["cogs"] == o["gt"]["cogs_paise"]
        assert v["contribution_margin"] - v["ad_spend"] == v["contribution_after_marketing"]
        by = {g["key"]: g["value"] for g in metric(client, o, "contribution_margin", "channel")["groups"]}
        assert sum(by.values()) == v["contribution_margin"]
