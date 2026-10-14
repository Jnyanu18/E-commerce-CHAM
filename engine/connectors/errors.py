"""Typed, structured connector errors. Messages must never contain credentials."""
from __future__ import annotations


class ConnectorError(Exception):
    """Base class. `retryable` tells the HTTP layer / job runner whether a retry can help."""

    retryable = False
    code = "connector_error"

    def __init__(self, message: str, *, provider: str | None = None, status: int | None = None,
                 detail: dict | None = None):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "provider": self.provider,
                "status": self.status, "retryable": self.retryable}


class AuthError(ConnectorError):
    """Credentials missing/invalid/expired or scope not granted. Needs human action."""
    code = "auth_error"


class RateLimitError(ConnectorError):
    """Provider throttled us. `retry_after` is seconds when the provider told us."""
    retryable = True
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class TransientError(ConnectorError):
    """Network error, timeout or provider 5xx. Safe to retry."""
    retryable = True
    code = "transient_error"


class PermanentError(ConnectorError):
    """Request is wrong (4xx other than auth/limit, bad query, bad cost). Retrying will not help."""
    code = "permanent_error"


class ResponseShapeError(PermanentError):
    """Provider response did not match the documented shape we parse."""
    code = "response_shape_error"


class ConfigError(ConnectorError):
    """Integration is mis-configured (bad shop domain, missing credential key, ...)."""
    code = "config_error"


class NotVerifiedError(ConnectorError):
    """Feature deliberately not implemented because it could not be verified against official docs."""
    code = "not_verified"


class CsvValidationError(ConnectorError):
    """Whole-file CSV problem (e.g. missing required column). Row-level problems go in the report."""
    code = "csv_validation_error"
