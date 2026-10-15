"""Shared helpers. All provider responses in this suite are SYNTHETIC, recorded-shape fixtures;
nothing here touches the network (httpx.MockTransport) and no real credentials exist."""
from datetime import datetime, timezone

import httpx
import pytest

from connectors.credentials import Credentials
from connectors.httpclient import HttpClient, RetryPolicy
from connectors.store import InMemoryStore, IntegrationRow

ORG = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)


class Sleeper:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, s):
        self.calls.append(s)


@pytest.fixture
def sleeper():
    return Sleeper()


@pytest.fixture
def store():
    return InMemoryStore()


def make_http(handler, provider, sleeper, creds=None, classify=None, attempts=4):
    return HttpClient(provider=provider, transport=httpx.MockTransport(handler),
                      policy=RetryPolicy(max_attempts=attempts, base_delay=1.0, max_delay=8.0),
                      sleep=sleeper, rng=lambda: 0.5, secrets=creds.secret_values() if creds else [],
                      classify=classify)


def make_integration(store, provider, account_ref, **kw):
    return store.add_integration(IntegrationRow(id=f"int-{provider}", org_id=ORG, provider=provider,
                                                account_ref=account_ref, **kw))


def creds(**kw):
    return Credentials(kw)


@pytest.fixture(autouse=True)
def _hash_key(monkeypatch):
    monkeypatch.setenv("CUSTOMER_HASH_KEY", "test-only-hash-key-0123456789")
