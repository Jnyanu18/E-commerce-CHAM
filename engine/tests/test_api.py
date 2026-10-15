import base64, hashlib, hmac, json, os, time, uuid
import pytest
from fastapi.testclient import TestClient

from app.api import get_conn
from app.auth import verify_jwt, AuthError
from app.main import app

SECRET = "jwt-test-secret-with-enough-length-1234"
ORG = str(uuid.uuid4())
USER = str(uuid.uuid4())


def b64(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def token(sub=USER, exp=None, alg="HS256", secret=SECRET):
    h = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    p = b64(json.dumps({"sub": sub, "exp": exp if exp is not None else time.time() + 600}).encode())
    sig = b64(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


class FakeConn:
    def __init__(self, member=True): self.member = member
    def execute(self, sql, params=None):
        class R:
            def __init__(s, m): s.m = m
            def fetchone(s): return (1,) if s.m else None
        return R(self.member)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("API_DEV_AUTH_BYPASS", raising=False)
    yield
    app.dependency_overrides.clear()


def _dep(member):
    def dep():
        yield FakeConn(member)
    return dep


def client(member=True):
    app.dependency_overrides[get_conn] = _dep(member)
    return TestClient(app)


Q = dict(org_id=ORG, start="2026-09-01", end="2026-09-30")


def test_jwt_verification():
    assert verify_jwt(token(), SECRET)["sub"] == USER
    for bad in [token(exp=time.time() - 5), token(secret="x" * 32), token(alg="none"), "a.b.c", "junk"]:
        with pytest.raises(AuthError):
            verify_jwt(bad, SECRET)


def test_requires_auth_and_membership():
    c = client()
    assert c.get("/metrics/net_revenue", params=Q).status_code == 401
    assert c.get("/metrics/net_revenue", params=Q, headers={"authorization": "Bearer " + token(secret="y" * 32)}).status_code == 401
    assert client(member=False).get("/metrics/net_revenue", params=Q, headers={"authorization": "Bearer " + token()}).status_code == 403


def test_validation_errors():
    h = {"authorization": "Bearer " + token()}
    c = client()
    assert c.get("/metrics/nope", params=Q, headers=h).status_code == 404
    assert c.get("/metrics/net_revenue", params={**Q, "filters": "{bad"}, headers=h).status_code == 400
    assert c.get("/metrics/net_revenue", params={**Q, "filters": "[1]"}, headers=h).status_code == 400
    assert c.get("/metrics/net_revenue", params={**Q, "start": "not-a-date"}, headers=h).status_code == 422


def test_list_metrics_matches_registry():
    from metrics import METRICS
    names = [m["name"] for m in TestClient(app).get("/metrics").json()["metrics"]]
    assert sorted(names) == sorted(METRICS)


def test_cors_preflight_allows_web_origin():
    r = TestClient(app).options("/metrics/net_revenue", headers={"origin": "http://localhost:3000", "access-control-request-method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_dev_bypass_is_explicit(monkeypatch):
    monkeypatch.setenv("API_DEV_AUTH_BYPASS", "1")
    app.dependency_overrides[get_conn] = _dep(False)
    assert TestClient(app).get("/metrics/nope", params=Q).status_code == 404  # got past auth
