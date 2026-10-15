"""Shared pure helpers for source -> canonical transforms.

Conventions (docs/CONTRACTS.md): money is integer paise via ``app.money``; timestamps are UTC;
``customer_key`` comes from ``app.identity`` (HMAC) and is never raw PII.

GST RULE (the ledger-wide convention, tested in tests/transforms/test_shopify.py):
  * ``orders.gross_revenue_paise``   = item value INCLUDING GST, before discounts, excluding shipping.
  * ``orders.shipping_charged_paise``= shipping charged to the customer (GST-inclusive).
  * ``orders.taxes_paise``           = the GST *contained within* (gross - discounts + shipping).
  * Customer paid                    = gross - discounts + shipping_charged  (GST-inclusive).
  * Ex-GST revenue is therefore ``gross - discounts + shipping - taxes`` (metrics agent applies it).
  If a source reports prices EXCLUDING tax (e.g. Shopify ``taxes_included = false``) the transform adds
  the tax to ``gross_revenue_paise`` so the invariant above still holds, and records an issue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app import identity as identity_lib
from app.money import rupees_to_paise

IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset; India has no DST
MISSING_SKU = "__MISSING_SKU__"
ORDER_STATUSES = ("placed", "shipped", "delivered", "cancelled", "returned", "rto")
# Monotonic precedence so out-of-order updates converge to the same state in any arrival order.
STATUS_RANK = {"placed": 0, "shipped": 1, "delivered": 2, "cancelled": 3, "returned": 4, "rto": 5}
CITY_TIERS = ("metro", "tier_1", "tier_2", "tier_3", "unknown")


class TransformError(ValueError):
    """Raw payload is invalid in a way that makes the record unusable (surfaced, never swallowed)."""


@dataclass
class OrderBundle:
    """Canonical rows produced from ONE raw record (or one group of marketplace rows).

    ``order`` holds only the fields this source owns (None-valued fields are never written), so a
    shipment bundle cannot clobber Shopify financials. ``partial`` bundles cannot create an order.
    """
    org_id: str
    channel: str | None
    external_id: str | None
    order: dict[str, Any] = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)
    fees: list[dict] = field(default_factory=list)       # {type, amount_paise, source}
    returns: list[dict] = field(default_factory=list)    # {type, cost_paise|None, returned_at|None}
    settlements: list[dict] = field(default_factory=list)  # settlement ITEM lines, see make_settlement_item
    shipments: list[dict] = field(default_factory=list)    # shipments rows (courier side)
    raw_ref: dict = field(default_factory=dict)          # {source, external_id}: link back to raw_records
    issues: list[str] = field(default_factory=list)
    partial: bool = False
    channel_ref: str | None = None                       # unresolved channel order reference (shipments)


def to_paise(value: Any, *, field_name: str = "amount", default: int | None = 0) -> int | None:
    """Rupees (str/int/float/Decimal) -> integer paise. None/'' -> ``default``. Garbage raises."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise TransformError(f"{field_name}: boolean is not an amount")
    try:
        text = value.replace(",", "").strip() if isinstance(value, str) else value
        return rupees_to_paise(text)
    except (InvalidOperation, ValueError, TypeError) as e:
        raise TransformError(f"{field_name}: invalid amount {value!r}") from e


def parse_ts(value: Any, *, naive_tz: timezone = timezone.utc, field_name: str = "timestamp") -> datetime | None:
    """ISO-8601 -> aware UTC datetime. Naive values are interpreted in ``naive_tz``. None/'' -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as e:
            raise TransformError(f"{field_name}: invalid timestamp {value!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz)
    return dt.astimezone(timezone.utc)


def ts_iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def ist_date(dt: datetime):
    return dt.astimezone(IST).date()


def customer_key(email: str | None = None, phone: str | None = None, identity: dict | None = None) -> str | None:
    """Ledger customer_key. NO hashing happens here: it is delegated to ``app.identity`` (the single canonical
    implementation, HMAC-SHA256 keyed by CUSTOMER_HASH_KEY).

    * ``identity`` = connector-supplied ``customerIdentity {emailHash, phoneHash}`` (precomputed HMACs, raw PII never
      persisted) -> ``customer_key_from_hashes``. The legacy names ``emailSha256``/``phoneSha256`` are NOT valid and are
      treated as absent.
    * otherwise raw ``email``/``phone`` (CSV sources that carry them) -> ``app.identity.customer_key``.
    """
    ident = identity or {}
    eh, ph = ident.get("emailHash") or None, ident.get("phoneHash") or None
    if eh or ph:
        return identity_lib.customer_key_from_hashes(eh, ph)
    return identity_lib.customer_key(email, phone)


def normalise_pincode(value: Any) -> str | None:
    """Pincode is text, 6 digits, leading zeros kept. Anything else -> None."""
    if value is None:
        return None
    s = re.sub(r"\s", "", str(value))
    return s if re.fullmatch(r"\d{6}", s) else None


def city_tier(city: str | None, tiers: dict[str, str] | None) -> str:
    """Tier from a supplied city->tier mapping (the ``city_tiers`` table). No mapping/city -> 'unknown'.

    Legacy values ``tier1..tier3`` are normalised to ``tier_1..tier_3``. We deliberately ship no built-in
    city classification: inventing one would be unverifiable.
    """
    if not city or not tiers:
        return "unknown"
    t = tiers.get(city.strip().lower())
    if t is None:
        return "unknown"
    t = {"tier1": "tier_1", "tier2": "tier_2", "tier3": "tier_3"}.get(t, t)
    return t if t in CITY_TIERS else "unknown"


def merge_lines(lines: list[dict], issues: list[str]) -> list[dict]:
    """Collapse lines sharing a SKU (natural key order_id+sku) exactly: qty summed, unit price = total/qty."""
    merged: dict[str, dict] = {}
    for ln in lines:
        m = merged.get(ln["sku"])
        if m is None:
            merged[ln["sku"]] = {"sku": ln["sku"], "qty": ln["qty"], "_total": ln["unit_price_paise"] * ln["qty"],
                                 "_prices": {ln["unit_price_paise"]}}
        else:
            m["qty"] += ln["qty"]
            m["_total"] += ln["unit_price_paise"] * ln["qty"]
            m["_prices"].add(ln["unit_price_paise"])
            issues.append(f"sku_lines_merged: {ln['sku']}")
    out = []
    for m in merged.values():
        unit = int((Decimal(m["_total"]) / m["qty"]).quantize(Decimal(1), rounding="ROUND_HALF_UP"))
        if unit * m["qty"] != m["_total"]:
            issues.append(f"unit_price_rounded: {m['sku']}")
        out.append({"sku": m["sku"], "qty": m["qty"], "unit_price_paise": unit})
    return out


def make_settlement_item(*, channel: str, ref: str, source: str, order_channel: str, settled_at: str | None,
                         net_paise: int, gross_paise: int | None, fees_paise: int | None, issues: list[str],
                         line_type: str = "sale") -> dict:
    """One settlement_items line for one order. Sign convention (migration 0007): gross/fees are positive
    magnitudes and net = gross - fees. Missing components are surfaced, never invented."""
    if gross_paise is None and fees_paise is None:
        gross_paise, fees_paise = net_paise, 0
        issues.append(f"settlement_components_unknown: {ref} (only net payout reported; fees/gross not split)")
    elif gross_paise is None:
        gross_paise = net_paise + fees_paise
    elif fees_paise is None:
        fees_paise = gross_paise - net_paise
    if gross_paise - fees_paise != net_paise:
        issues.append(f"settlement_components_inconsistent: {ref} gross {gross_paise} - fees {fees_paise} != net {net_paise}")
    return {"channel": channel, "settlement_ref": ref, "source": source, "order_channel": order_channel,
            "settled_at": settled_at, "line_type": line_type, "gross_paise": gross_paise, "fees_paise": fees_paise,
            "net_paise": net_paise}
