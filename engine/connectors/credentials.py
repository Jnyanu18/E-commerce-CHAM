"""Credential handling: decrypt via app.crypto, never repr/log secret values."""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import AuthError


@dataclass(frozen=True)
class Credentials:
    """Decrypted credential bag. repr() and str() never reveal values."""
    values: dict = field(repr=False)

    def get(self, key: str, required: bool = True, provider: str | None = None) -> str | None:
        v = self.values.get(key)
        if required and not v:
            raise AuthError(f"missing credential field '{key}'", provider=provider)
        return v

    def secret_values(self) -> list[str]:
        return [str(v) for v in self.values.values() if isinstance(v, str) and len(v) >= 6]

    def __repr__(self) -> str:
        return f"Credentials(keys={sorted(self.values)})"

    __str__ = __repr__


def decrypt_credentials(blob: bytes | None, key_b64: str, aad: bytes = b"",
                        provider: str | None = None) -> Credentials:
    from app.crypto import decrypt  # main-owned shared utility
    if not blob:
        raise AuthError("integration has no stored credentials", provider=provider)
    try:
        return Credentials(decrypt(blob, key_b64, aad))
    except Exception:  # cryptography raises opaque InvalidTag; do not chain (could echo data)
        raise AuthError("could not decrypt integration credentials", provider=provider) from None


def redact(text: str, secrets: list[str]) -> str:
    for s in sorted(secrets, key=len, reverse=True):
        if s:
            text = text.replace(s, "[REDACTED]")
    return text
