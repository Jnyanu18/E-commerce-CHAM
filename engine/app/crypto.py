"""App-level AES-GCM for integration credentials (stored in integrations.credentials_enc)."""
import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(key_b64: str) -> bytes:
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("credentials key must be 32 bytes, base64-encoded")
    return key


def encrypt(data: dict, key_b64: str, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key(key_b64)).encrypt(nonce, json.dumps(data).encode(), aad)


def decrypt(blob: bytes, key_b64: str, aad: bytes = b"") -> dict:
    blob = bytes(blob)
    return json.loads(AESGCM(_key(key_b64)).decrypt(blob[:12], blob[12:], aad))
