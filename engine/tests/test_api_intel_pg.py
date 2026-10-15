import json, os, time, uuid, base64, hashlib, hmac
import psycopg
import pytest
from fastapi.testclient import TestClient

URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif("localhost" not in URL, reason="needs DATABASE_URL on localhost")

from app.api import get_conn
from app.main import app

SECRET = "api-intel-secret-with-length-123456789"


def tok(sub):
    b = lambda d: base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    h, p = b(b'{"alg":"HS256"}'), b(json.dumps({"sub": sub, "exp": time.time() + 300}).encode())
    return f"{h}.{p}.{b(hmac.new(SECRET.encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest())}"


def dep():
    with psycopg.connect(URL, autocommit=True) as c:
        yield c


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("API_DEV_AUTH_BYPASS", raising=False)
    app.dependency_overrides[get_conn] = dep
    c = psycopg.connect(URL, autocommit=True)
    orgs = {}
    for n in "ab":
        org, user = str(uuid.uuid4()), str(uuid.uuid4())
        c.execute("insert into orgs(id,name) values (%s,%s)", (org, n))
        c.execute("insert into auth.users(id,email) values (%s,%s)", (user, f"{n}@x.invalid"))
        c.execute("insert into org_members(org_id,user_id) values (%s,%s)", (org, user))
        orgs[n] = (org, tok(user))
    yield c, orgs, TestClient(app)
    app.dependency_overrides.clear()
    c.close()


def test_anomalies_and_brief_roundtrip_and_isolation(world):
    c, orgs, client = world
    (a, ta), (b, tb) = orgs["a"], orgs["b"]
    anoms = [{"id": "an-1", "metric": "net_revenue", "date": "2026-09-17", "direction": "down", "severity": "high", "robust_z": -4.2}]
    c.execute("insert into insights(org_id, week_start, anomalies) values (%s,'2026-09-14',%s::jsonb)", (a, json.dumps(anoms)))
    h = lambda t: {"authorization": f"Bearer {t}"}
    r = client.get("/anomalies", params={"org_id": a}, headers=h(ta)).json()
    assert [x["id"] for x in r["anomalies"]] == ["an-1"] and r["anomalies"][0]["week_start"] == "2026-09-14"
    assert client.get("/anomalies", params={"org_id": b}, headers=h(tb)).json() == {"anomalies": []}
    assert client.get("/anomalies", params={"org_id": a}, headers=h(tb)).status_code == 403
    assert client.get("/briefs/latest", params={"org_id": a}, headers=h(ta)).json()["status"] == "none"  # anomalies only, no brief
    calls = {"meta": {"mode": "narrative"}, "calls": [{"call_id": "c1", "tool": "get_metric"}]}
    c.execute("update insights set brief_markdown='## What happened\nx [c1]', tool_calls=%s::jsonb where org_id=%s", (json.dumps(calls), a))
    br = client.get("/briefs/latest", params={"org_id": a}, headers=h(ta)).json()
    assert br["status"] == "ok" and br["mode"] == "narrative" and br["calls"][0]["call_id"] == "c1" and "[c1]" in br["markdown"]
    assert client.get("/briefs/latest", params={"org_id": a}).status_code == 401
