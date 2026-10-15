"""Shopify Admin GraphQL connector (orders, line items, refunds).

Verified against official docs on 2026-10-14 (details and gaps in engine/connectors/NOTES.md):
- https://shopify.dev/docs/api/admin-graphql/latest            (endpoint, auth header, errors)
- https://shopify.dev/docs/api/admin-graphql/latest/queries/orders  (args, filters, pageInfo)
- https://shopify.dev/docs/apps/build/apis/graphql-admin/rate-limits (cost / throttleStatus)
- https://shopify.dev/docs/api/usage/access-scopes             (read_orders = last 60 days; read_all_orders)
- https://shopify.dev/docs/api/usage/versioning                (2026-10 latest stable)
Never live-tested: no store/credentials were available.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

from .base import Connector, parse_ts
from .credentials import Credentials
from .errors import (AuthError, ConfigError, PermanentError, RateLimitError, ResponseShapeError,
                     TransientError)
from .httpclient import default_classify, parse_retry_after
from .identity import hash_email, hash_phone
from .store import RawRecord

API_VERSION = "2026-10"
SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

MONEY = "{ shopMoney { amount currencyCode } }"


def build_orders_query(line_items: int, refunds: int, refund_line_items: int, transactions: int) -> str:
    """Only fields verified on the Order / LineItem / Refund / RefundLineItem / MailingAddress /
    OrderTransaction / MoneyBag pages. Deliberately NOT requested (minimisation): customer name,
    address1/2, shipping name/phone, payment card details, geolocation."""
    return f"""
query Orders($first: Int!, $after: String, $query: String) {{
  orders(first: $first, after: $after, query: $query) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      id name createdAt updatedAt processedAt cancelledAt
      displayFinancialStatus displayFulfillmentStatus currencyCode
      paymentGatewayNames tags sourceName email phone
      customer {{ id }}
      subtotalPriceSet {MONEY}
      totalPriceSet {MONEY}
      totalDiscountsSet {MONEY}
      totalShippingPriceSet {MONEY}
      totalTaxSet {MONEY}
      totalRefundedSet {MONEY}
      shippingAddress {{ city provinceCode zip countryCodeV2 }}
      transactions(first: {transactions}) {{ id kind status gateway processedAt amountSet {MONEY} }}
      lineItems(first: {line_items}) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ id name title sku quantity currentQuantity
                 originalUnitPriceSet {MONEY} discountedUnitPriceSet {MONEY}
                 totalDiscountSet {MONEY} }}
      }}
      refunds(first: {refunds}) {{
        id createdAt updatedAt note totalRefundedSet {MONEY}
        refundLineItems(first: {refund_line_items}) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ quantity restockType subtotalSet {MONEY} totalTaxSet {MONEY} lineItem {{ id sku }} }}
        }}
      }}
    }}
  }}
}}"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_order(order: dict, pepper: bytes | None = None) -> dict:
    """Drop raw email/phone, add hashed identity. The rest of the node is stored verbatim."""
    out = dict(order)
    email, phone = out.pop("email", None), out.pop("phone", None)
    out["customerIdentity"] = {"emailSha256": hash_email(email, pepper), "phoneSha256": hash_phone(phone, pepper),
                               "hmac": bool(pepper)}
    return out


class ShopifyConnector(Connector):
    provider = "shopify"
    source = "shopify_order"
    watermark_path = ("updatedAt",)
    overlap = timedelta(minutes=10)

    def __init__(self, *a, api_version: str = API_VERSION, page_size: int = 10, line_items: int = 15,
                 refunds: int = 3, refund_line_items: int = 10, transactions: int = 5,
                 backfill_start: datetime | None = None, window_days: int = 30,
                 identity_pepper: bytes | None = None, **kw):
        super().__init__(*a, **kw)
        shop = self.integration.account_ref.strip().lower()
        if not SHOP_RE.match(shop):
            raise ConfigError("account_ref must be a '<shop>.myshopify.com' domain", provider=self.provider)
        self.url = f"https://{shop}/admin/api/{api_version}/graphql.json"
        self.page_size, self.window_days, self.pepper = page_size, window_days, identity_pepper
        self.backfill_start = backfill_start
        self.query = build_orders_query(line_items, refunds, refund_line_items, transactions)
        self._available: float | None = None
        self._restore: float | None = None
        self._last_cost: float = 0

    # --- error classification ------------------------------------------------
    def classify(self, resp: httpx.Response) -> None:
        default_classify(resp, self.provider)  # 4xx/5xx; Retry-After honoured if sent (not documented)
        try:
            body = resp.json()
        except ValueError:
            raise ResponseShapeError("non-JSON response from Shopify", provider=self.provider, status=resp.status_code)
        errors = body.get("errors") if isinstance(body, dict) else None
        if not errors:
            return
        if isinstance(errors, dict):  # e.g. {"errors": "..."} style bodies
            errors = [{"message": str(errors)}]
        codes = {(e.get("extensions") or {}).get("code") for e in errors if isinstance(e, dict)}
        msg = "; ".join(str(e.get("message", ""))[:200] for e in errors if isinstance(e, dict))
        kw = {"provider": self.provider, "status": resp.status_code}
        if "THROTTLED" in codes:
            raise RateLimitError("Shopify THROTTLED", retry_after=self._deficit_wait(body), **kw)
        if codes & {"ACCESS_DENIED", "SHOP_INACTIVE"}:
            raise AuthError(f"Shopify {sorted(c for c in codes if c)[0]}: {msg}", **kw)
        if "INTERNAL_SERVER_ERROR" in codes:
            raise TransientError("Shopify INTERNAL_SERVER_ERROR", **kw)
        if "MAX_COST_EXCEEDED" in codes:
            raise PermanentError("query cost exceeds Shopify per-query maximum; reduce page/nested sizes", **kw)
        raise PermanentError(f"GraphQL error: {msg}", **kw)

    @staticmethod
    def _cost_ext(body: dict) -> dict:
        ext = (body.get("extensions") or {}).get("cost") or {}
        return ext if isinstance(ext, dict) else {}

    def _deficit_wait(self, body: dict) -> float | None:
        c = self._cost_ext(body)
        ts = c.get("throttleStatus") or {}
        try:
            need = float(c["requestedQueryCost"]) - float(ts["currentlyAvailable"])
            return max(need / float(ts["restoreRate"]), 1.0)   # docs recommend >= 1s backoff
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    def _pace(self) -> None:
        """Proactively wait for the leaky bucket to refill enough for the previous query's cost."""
        if self._available is not None and self._restore and self._available < self._last_cost:
            self.http.sleep((self._last_cost - self._available) / self._restore)
            self._available = self._last_cost

    def _gql(self, query: str, variables: dict) -> dict:
        self._pace()
        resp = self.http.request(
            "POST", self.url, json={"query": query, "variables": variables},
            headers={"X-Shopify-Access-Token": self.credentials.get("access_token", provider=self.provider),
                     "Content-Type": "application/json"}, classify=self.classify)
        body = resp.json()
        c = self._cost_ext(body)
        ts = c.get("throttleStatus") or {}
        try:
            self._available = float(ts["currentlyAvailable"]); self._restore = float(ts["restoreRate"])
            self._last_cost = float(c.get("requestedQueryCost") or c.get("actualQueryCost") or 0)
        except (KeyError, TypeError, ValueError):
            pass
        data = body.get("data")
        if not isinstance(data, dict) or "orders" not in data:
            raise ResponseShapeError("Shopify response missing data.orders", provider=self.provider)
        return data["orders"]

    # --- lifecycle -------------------------------------------------------------
    def validate(self) -> None:
        self._gql("query { orders(first: 1) { nodes { id } } }", {})  # also proves read_orders scope

    def _pages(self, search: str | None) -> Iterator[list[dict]]:
        after = None
        while True:
            conn = self._gql(self.query, {"first": self.page_size, "after": after, "query": search})
            nodes = conn.get("nodes")
            pi = conn.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(pi, dict):
                raise ResponseShapeError("Shopify orders connection malformed", provider=self.provider)
            yield nodes
            if not pi.get("hasNextPage"):
                return
            after = pi.get("endCursor")
            if not after:
                raise ResponseShapeError("hasNextPage without endCursor", provider=self.provider)

    def _to_records(self, nodes: list[dict]) -> list[RawRecord]:
        recs = []
        for n in nodes:
            if not n.get("id"):
                raise ResponseShapeError("order without id", provider=self.provider)
            for conn_name in ("lineItems",):
                if ((n.get(conn_name) or {}).get("pageInfo") or {}).get("hasNextPage"):
                    self.warnings.append(f"{n['id']}: {conn_name} truncated at page size (not paginated further)")
            for r in n.get("refunds") or []:
                if ((r.get("refundLineItems") or {}).get("pageInfo") or {}).get("hasNextPage"):
                    self.warnings.append(f"{n['id']}: refundLineItems truncated at page size")
            recs.append(RawRecord(self.integration.org_id, self.source, n["id"], sanitize_order(n, self.pepper)))
        return recs

    def fetch(self, mode: str, since: datetime | None) -> Iterator[list[RawRecord]]:
        if mode == "incremental" and since is not None:
            searches = [f"updated_at:>='{_iso(since)}'"]
        elif mode == "incremental":
            searches = [None]  # nothing stored yet: full pull
        else:
            # Pagination of object arrays is capped at 25,000 objects (rate-limits page), so the
            # backfill walks created_at windows. Requires read_all_orders for orders older than 60 days.
            now = self.clock()
            start = self.backfill_start or (now - timedelta(days=3 * 365))
            searches, lo = [], start
            while lo < now:
                hi = min(lo + timedelta(days=self.window_days), now + timedelta(seconds=1))
                searches.append(f"created_at:>='{_iso(lo)}' created_at:<'{_iso(hi)}'")
                lo = hi
        for s in searches:
            n_in_window = 0
            for nodes in self._pages(s):
                n_in_window += len(nodes)
                yield self._to_records(nodes)
            if n_in_window >= 20000:
                self.warnings.append(f"window '{s}' returned {n_in_window} orders, near the 25,000 pagination cap; "
                                     "reduce window_days")
