"""Shopify order (Admin GraphQL, as stored by connectors/shopify.py) -> canonical bundle.

PAYLOAD SHAPE: the GraphQL Admin `Order` node the connector fetches (fields verified by the connectors agent against
Shopify's docs, see engine/connectors/NOTES.md): ``id`` (gid), ``name`` ("#1001"), ``createdAt``, ``cancelledAt``,
``displayFinancialStatus``, ``displayFulfillmentStatus``, ``currencyCode``, ``paymentGatewayNames``,
``subtotalPriceSet / totalPriceSet / totalDiscountsSet / totalShippingPriceSet / totalTaxSet / totalRefundedSet``
(MoneyBag: ``shopMoney.amount`` decimal strings), ``shippingAddress {city, provinceCode, zip, countryCodeV2}``,
``transactions[] {kind, status, gateway, amountSet}``, ``lineItems.nodes[] {sku, quantity, currentQuantity, title,
originalUnitPriceSet, discountedUnitPriceSet, totalDiscountSet}``, ``refunds[] {totalRefundedSet}``, and
``customerIdentity {emailHash, phoneHash}`` (HMACs from app.identity; raw PII is never stored).

Unverified / assumed (each recorded as a data-quality issue, never invented):
* ``taxesIncluded`` is not requested by the connector. Prices are ASSUMED GST-inclusive (Indian consumer-price
  convention) and ``taxes_included_unknown`` is raised on every order. If a payload does carry ``taxesIncluded``
  (boolean) it is honoured. A mismatch between computed and reported total shows up as ``total_mismatch``.
* ``shopMoney`` is the SHOP currency; only INR shops are supported (anything else raises TransformError).
* Nested connections (``lineItems``, ``refunds``) are fetched with a fixed page size; ``hasNextPage`` true raises
  ``line_items_truncated`` / ``refunds_truncated`` (totals may then be incomplete).
Shopify exposes no payment-gateway fee on the order, so no fee rows are produced.

Identifiers: ``external_id`` = numeric tail of the gid (so Shiprocket ``channel_order_id`` can link).
"""
from __future__ import annotations

from typing import Any

from .common import (MISSING_SKU, OrderBundle, TransformError, city_tier, customer_key, merge_lines,
                     normalise_pincode, parse_ts, to_paise, ts_iso)

SOURCE = "shopify_order"
CHANNEL = "shopify"


def order_external_id(gid: Any) -> str:
    """'gid://shopify/Order/123' -> '123'; a bare numeric id is returned unchanged."""
    return str(gid).rsplit("/", 1)[-1].split("?", 1)[0]


def _bag(node: dict, key: str) -> str | None:
    """Decimal-string amount from a MoneyBag field, or None when the field/bag is absent."""
    v = node.get(key)
    if not isinstance(v, dict):
        return None
    sm = v.get("shopMoney")
    return sm.get("amount") if isinstance(sm, dict) else None


def _money(node: dict, key: str, issues: list[str], *, required: bool = False) -> int:
    amt = _bag(node, key)
    if amt is None:
        if required:
            issues.append(f"field_missing: {key} (treated as 0)")
        return 0
    return to_paise(amt, field_name=key)


def _nodes(conn: Any) -> tuple[list[dict], bool]:
    if isinstance(conn, dict):
        return list(conn.get("nodes") or []), bool((conn.get("pageInfo") or {}).get("hasNextPage"))
    return list(conn or []), False


def _payment_method(p: dict, issues: list[str]) -> str:
    names = list(p.get("paymentGatewayNames") or [])
    if not names:
        names = [t.get("gateway") for t in p.get("transactions") or [] if t.get("gateway")]
    joined = " ".join(str(n).lower() for n in names)
    if not joined:
        issues.append("payment_method_assumed_prepaid: no gateway info")
        return "prepaid"
    return "cod" if ("cod" in joined or "cash on delivery" in joined or "cash_on_delivery" in joined) else "prepaid"


def transform_shopify_order(org_id: str, payload: dict[str, Any], *, raw_external_id: str | None = None,
                            city_tiers: dict[str, str] | None = None) -> OrderBundle:
    if not isinstance(payload, dict) or payload.get("id") in (None, ""):
        raise TransformError("shopify order without id")
    ext = order_external_id(payload["id"])
    issues: list[str] = []
    ordered_at = parse_ts(payload.get("createdAt"), field_name="createdAt")
    if ordered_at is None:
        raise TransformError(f"shopify order {ext}: createdAt missing")
    cur = payload.get("currencyCode")
    if cur not in (None, "INR"):
        raise TransformError(f"shopify order {ext}: unsupported currency {cur!r} (INR only)")
    if cur is None:
        issues.append("currency_missing: assumed INR")

    nodes, li_more = _nodes(payload.get("lineItems"))
    if li_more:
        issues.append("line_items_truncated: lineItems.pageInfo.hasNextPage; item totals may be incomplete")
    lines = []
    for li in nodes:
        qty = li.get("currentQuantity")
        qty = int(li.get("quantity") or 0) if qty is None else int(qty)
        if qty <= 0:
            issues.append("zero_qty_line_skipped")
            continue
        sku = (li.get("sku") or "").strip()
        if not sku:
            sku = MISSING_SKU
            issues.append(f"missing_sku: line '{li.get('title') or li.get('name') or '?'}'")
        price = _bag(li, "originalUnitPriceSet")
        if price is None:
            raise TransformError(f"shopify order {ext}: line item without originalUnitPriceSet")
        lines.append({"sku": sku, "qty": qty, "unit_price_paise": to_paise(price, field_name="originalUnitPrice")})
    if not lines:
        issues.append("no_line_items")
    items = merge_lines(lines, issues)

    gross = sum(i["unit_price_paise"] * i["qty"] for i in items)
    discounts = _money(payload, "totalDiscountsSet", issues, required=True)
    tax = _money(payload, "totalTaxSet", issues, required=True)
    shipping = _money(payload, "totalShippingPriceSet", issues, required=True)
    if "taxesIncluded" not in payload:
        issues.append("taxes_included_unknown: assumed GST-inclusive prices")
    if payload.get("taxesIncluded", True) is False:
        gross += tax  # GST RULE (transforms/common.py): ledger gross is GST-inclusive; uplift tax-exclusive prices
        issues.append("tax_exclusive_prices_uplifted")
    paid = gross - discounts + shipping
    total_amt = _bag(payload, "totalPriceSet")
    if total_amt is None:
        issues.append("field_missing: totalPriceSet (total cross-check skipped)")
    elif to_paise(total_amt, field_name="totalPriceSet") != paid:
        issues.append(f"total_mismatch: computed {paid} != totalPriceSet {to_paise(total_amt)} paise")

    refunds_list, rf_more = _nodes(payload.get("refunds"))
    if rf_more:
        issues.append("refunds_truncated: refunds list incomplete")
    if _bag(payload, "totalRefundedSet") is not None:
        refunds = _money(payload, "totalRefundedSet", issues)
        listed = sum(_money(r, "totalRefundedSet", issues) for r in refunds_list)
        if refunds_list and listed != refunds:
            issues.append(f"refund_sum_mismatch: order total {refunds} != sum of refunds {listed}")
    elif refunds_list:
        refunds = sum(_money(r, "totalRefundedSet", issues) for r in refunds_list)
        issues.append("field_missing: totalRefundedSet (used sum of refunds[])")
    else:
        refunds = sum(to_paise(_bag(t, "amountSet"), field_name="transaction amount") for t in payload.get("transactions") or []
                      if str(t.get("kind", "")).upper() == "REFUND" and str(t.get("status", "")).upper() == "SUCCESS"
                      and _bag(t, "amountSet") is not None)
    if refunds > paid:
        issues.append(f"refund_exceeds_paid: {refunds} > {paid}")

    if payload.get("cancelledAt"):
        status = "cancelled"
    elif str(payload.get("displayFulfillmentStatus") or "").upper() == "FULFILLED":
        status = "shipped"  # Shopify cannot know delivery; Shiprocket promotes to delivered/rto
    else:
        status = "placed"

    addr = payload.get("shippingAddress") or {}
    pin = normalise_pincode(addr.get("zip"))
    if addr.get("zip") and pin is None:
        issues.append(f"invalid_pincode: {addr.get('zip')!r}")
    city = (addr.get("city") or "").strip() or None

    order = {
        "ordered_at": ts_iso(ordered_at),
        "customer_key": customer_key(identity=payload.get("customerIdentity")),
        "city": city, "city_tier": city_tier(city, city_tiers), "pincode": pin,
        "payment_method": _payment_method(payload, issues), "status": status,
        "gross_revenue_paise": gross, "discounts_paise": discounts, "taxes_paise": tax,
        "shipping_charged_paise": shipping, "refunds_paise": refunds,
    }
    if order["customer_key"] is None:
        issues.append("customer_key_missing: no customerIdentity hashes")
    return OrderBundle(org_id=org_id, channel=CHANNEL, external_id=ext, order=order, items=items,
                       raw_ref={"source": SOURCE, "external_id": raw_external_id or ext}, issues=issues)


def shopify_ref_keys(payload: dict) -> list[str]:
    """Identifiers a shipment may use to refer to this order: gid tail, name ('#1001' -> '1001'), order_number."""
    keys = [order_external_id(payload["id"]) if payload.get("id") not in (None, "") else None,
            payload.get("name"), payload.get("order_number")]
    return [str(k).lstrip("#") for k in keys if k not in (None, "")]
