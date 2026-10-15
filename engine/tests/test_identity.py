import pytest
from app.identity import (MissingHashKey, customer_key, customer_key_from_hashes, hash_email, hash_phone,
                          normalise_email, normalise_phone)

K = "unit-test-secret-key-0123456789"


def test_phone_normalisation_variants_agree():
    for p in ["9876543210", "+91 98765 43210", "09876543210", "919876543210", "0091-9876543210", "(+91) 98765-43210"]:
        assert normalise_phone(p) == "9876543210", p
    for bad in [None, "", "12345", "5876543210", "98765432101234", "abc"]:
        assert normalise_phone(bad) is None, bad


def test_email_normalisation():
    assert normalise_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert normalise_email("nope") is None and normalise_email(None) is None


def test_hmac_is_keyed_deterministic_and_namespaced():
    a = hash_email("a@x.com", K)
    assert a == hash_email(" A@X.com", K) and len(a) == 64
    assert a != hash_email("a@x.com", K + "x")           # key matters
    assert hash_phone("9876543210", K) == hash_phone("+919876543210", K)
    assert hash_phone("9876543210", K) != hash_email("9876543210@x", K)
    assert customer_key("a@x.com", "9876543210", K) == a   # email preferred
    assert customer_key(None, "9876543210", K) == hash_phone("9876543210", K)
    assert customer_key(None, None, K) is None
    assert customer_key_from_hashes(None, "p") == "p" and customer_key_from_hashes("e", "p") == "e"


def test_key_required(monkeypatch):
    monkeypatch.delenv("CUSTOMER_HASH_KEY", raising=False)
    with pytest.raises(MissingHashKey):
        hash_email("a@x.com")
    monkeypatch.setenv("CUSTOMER_HASH_KEY", K)
    assert hash_email("a@x.com") == hash_email("a@x.com", K)
