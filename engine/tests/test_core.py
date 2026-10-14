import base64, os
import pytest
from app.crypto import decrypt, encrypt
from app.money import paise_to_rupees_str, rupees_to_paise

KEY = base64.b64encode(os.urandom(32)).decode()

def test_crypto_roundtrip_and_tamper():
    blob = encrypt({"token": "abc"}, KEY, b"org1")
    assert decrypt(blob, KEY, b"org1") == {"token": "abc"}
    with pytest.raises(Exception):
        decrypt(blob, KEY, b"org2")

def test_money():
    assert rupees_to_paise("499.50") == 49950
    assert rupees_to_paise(0.1) == 10
    assert rupees_to_paise("1.005") == 101
    assert paise_to_rupees_str(123456789) == "1,234,567.89"


def test_seed_deterministic_and_realistic():
    from seed.synthetic import generate
    a, b = generate(weeks=8), generate(weeks=8)
    assert a["orders"] == b["orders"]
    orders = a["orders"]
    assert len(orders) > 1000
    assert {o["channel"] for o in orders} == {"shopify", "amazon", "blinkit"}
    cod = [o for o in orders if o["payment_method"] == "cod"]
    pre = [o for o in orders if o["channel"] == "shopify" and o["payment_method"] == "prepaid"]
    rto = lambda xs: sum(o["status"] == "rto" for o in xs) / len(xs)
    assert rto(cod) > 2 * rto(pre)
