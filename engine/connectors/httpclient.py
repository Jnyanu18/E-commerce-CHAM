"""HTTP layer: retries, exponential backoff with full jitter, Retry-After handling.

Providers supply a `classify(response)` that raises typed errors (see errors.py) for failures
signalled in provider-specific ways (e.g. Shopify THROTTLED inside a 200). Only retryable errors
are retried. Sleep, RNG and transport are injectable so tests are deterministic.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .errors import AuthError, ConnectorError, PermanentError, RateLimitError, TransientError
from .credentials import redact

log = logging.getLogger("connectors.http")
Classifier = Callable[[httpx.Response], None]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0        # delay_n = U(0, min(max_delay, base * 2**(n-1)))  (full jitter)
    max_delay: float = 60.0
    max_retry_after: float = 300.0  # refuse to wait longer than this for a provider-supplied delay


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After in delta-seconds form only (HTTP-date form is ignored -> None)."""
    if value is None:
        return None
    try:
        s = float(value.strip())
    except ValueError:
        return None
    return s if s >= 0 else None


def default_classify(resp: httpx.Response, provider: str | None = None) -> None:
    s = resp.status_code
    if s < 400:
        return
    kw = {"provider": provider, "status": s}
    if s == 401:
        raise AuthError("unauthorized (401)", **kw)
    if s == 403:
        raise AuthError("forbidden (403): missing permission/scope or access not granted", **kw)
    if s == 429:
        raise RateLimitError("rate limited (429)",
                             retry_after=parse_retry_after(resp.headers.get("Retry-After")), **kw)
    if s == 408 or s >= 500:
        raise TransientError(f"provider error ({s})", **kw)
    raise PermanentError(f"request rejected ({s})", **kw)


class HttpClient:
    def __init__(self, *, provider: str, transport: httpx.BaseTransport | None = None,
                 policy: RetryPolicy | None = None, sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random, timeout: float = 30.0,
                 secrets: list[str] | None = None, classify: Classifier | None = None):
        self.provider = provider
        self.policy = policy or RetryPolicy()
        self._sleep, self._rng = sleep, rng
        self._secrets = secrets or []
        self._classify = classify or (lambda r: default_classify(r, provider))
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self.requests_made = 0

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def close(self) -> None:
        self._client.close()

    def backoff(self, attempt: int) -> float:
        return self._rng() * min(self.policy.max_delay, self.policy.base_delay * 2 ** (attempt - 1))

    def request(self, method: str, url: str, *, classify: Classifier | None = None, **kw) -> httpx.Response:
        classify = classify or self._classify
        last: ConnectorError | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                self.requests_made += 1
                resp = self._client.request(method, url, **kw)
                classify(resp)
                return resp
            except httpx.TransportError as e:  # includes timeouts
                last = TransientError(redact(f"network error: {type(e).__name__}", self._secrets),
                                      provider=self.provider)
            except ConnectorError as e:
                if not e.retryable:
                    raise
                last = e
            if attempt == self.policy.max_attempts:
                break
            delay = self.backoff(attempt)
            if isinstance(last, RateLimitError) and last.retry_after is not None:
                if last.retry_after > self.policy.max_retry_after:
                    raise last
                delay = max(delay, last.retry_after)
            log.warning("%s: retry %d/%d in %.2fs (%s)", self.provider, attempt,
                        self.policy.max_attempts, delay, last.code)
            self._sleep(delay)
        assert last is not None
        raise last
