"""Shopify order -> canonical bundle.

ASSUMED PAYLOAD SHAPE (UNVERIFIED against current Shopify docs; the connectors agent must confirm):
modelled on the Admin REST ``Order`` resource: ``id``, ``name``, ``order_number``, ``created_at``,
``cancelled_at``, ``fulfillment_status``, ``financial_status``, ``currency``, ``taxes_included``,
``total_price``, ``total_discounts``, ``total_tax``, ``total_shipping_price_set.shop_money.amount`` (or
``shipping_lines[].price``), ``payment_gateway_names`` (or ``gateway``), ``line_items[] {sku, quantity, price}``,
``refunds[].transactions[] {kind, status, amount}``, ``shipping_address {city, zip, phone}``, ``email``,
``phone``, ``customer {email, phone}``. Amounts are rupee strings. If the connector uses GraphQL shapes, only
this adapter changes. Shopify exposes no payment-gateway fee on the order, so no fee rows are produced.
"""
from __future__ import annotations

from typing import Any

from .common import (MISSING_SKU, OrderBundle, TransformError, city_tier, customer_key, merge_lines,
                     normalise_pincode, parse_ts, to_paise, ts_iso)

SOURCE = "shopify_order"
CHANNEL = "shopify"


def _payment_method(p: dict, issues: list[str]) -> str:
    names = p.get("payment_gateway_names") or ([p["gateway"]] if p.get("gateway") else [])
    joined = " ".join(str(n).lower() for n in names)
    if not joined:
        issues.append("payment_method_assumed_prepaid: no gateway info")
        return "prepaid"
    return "cod" if ("cod" in joined or "cash on delivery" in joined or "cash_on_delivery" in joined) else "prepaid"


def _shipping(p: dict) -> int:
    ps = ((p.get("total_shipping_price_set") or {}).get("shop_money") or {}).get("amount")
    if ps is not None:
        return to_paise(ps, field_name="shipping")
    return sum(to_paise(s.get("price"), field_name="shipping_line") for s in p.get("shipping_lines") or [])


def transform_shopify_order(org_id: str, payload: dict[str, Any], *, raw_external_id: str | None = None,
                            city_tiers: dict[str, str] | None = None) -> OrderBundle:
    if not isinstance(payload, dict) or payload.get("id") in (None, ""):
        raise TransformError("shopify order without id")
    ext = str(payload["id"])
    issues: list[str] = []
    ordered_at = parse_ts(payload.get("created_at"), field_name="created_at")
    if ordered_at is None:
        raise TransformError(f"shopify order {ext}: created_at missing")

    lines = []
    for li in payload.get("line_items") or []:
        qty = int(li.get("quantity") or 0)
        if qty <= 0:
            issues.append("zero_qty_line_skipped")
            continue
        sku = (li.get("sku") or "").strip()
        if not sku:
            sku = MISSING_SKU
            issues.append(f"missing_sku: line '{li.get('title') or li.get('name') or '?'}'")
        lines.append({"sku": sku, "qty": qty, "unit_price_paise": to_paise(li.get("price"), field_name="price")})
    if not lines:
        issues.append("no_line_items")
    items = merge_lines(lines, issues)

    gross = sum(i["unit_price_paise"] * i["qty"] for i in items)
    discounts = to_paise(payload.get("total_discounts"), field_name="total_discounts")
    tax = to_paise(payload.get("total_tax"), field_name="total_tax")
    shipping = _shipping(payload)
    if "taxes_included" not in payload:
        issues.append("taxes_included_unknown: assumed GST-inclusive prices")
    if payload.get("taxes_included", True) is False:
        gross += tax  # GST RULE: ledger gross is GST-inclusive; uplift tax-exclusive prices
        issues.append("tax_exclusive_prices_uplifted")
    paid = gross - discounts + shipping
    total_price = to_paise(payload.get("total_price"), field_name="total_price", default=None)
    if total_price is not None and total_price != paid:
        issues.append(f"total_mismatch: computed {paid} != total_price {total_price} paise")

    refunds = 0
    for r in payload.get("refunds") or []:
        for t in r.get("transactions") or []:
            if str(t.get("kind", "refund")).lower() == "refund" and str(t.get("status", "success")).lower() == "success":
                refunds += to_paise(t.get("amount"), field_name="refund")
    if refunds > paid:
        issues.append(f"refund_exceeds_paid: {refunds} > {paid}")

    if payload.get("cancelled_at"):
        status = "cancelled"
    elif str(payload.get("fulfillment_status") or "").lower() == "fulfilled":
        status = "shipped"  # Shopify cannot know delivery; Shiprocket promotes to delivered/rto
    else:
        status = "placed"

    addr = payload.get("shipping_address") or {}
    cust = payload.get("customer") or {}
    pin = normalise_pincode(addr.get("zip"))
    if addr.get("zip") and pin is None:
        issues.append(f"invalid_pincode: {addr.get('zip')!r}")
    city = (addr.get("city") or "").strip() or None

    order = {
        "ordered_at": ts_iso(ordered_at),
        "customer_key": customer_key(payload.get("email") or cust.get("email"),
                                     payload.get("phone") or cust.get("phone") or addr.get("phone")),
        "city": city, "city_tier": city_tier(city, city_tiers), "pincode": pin,
        "payment_method": _payment_method(payload, issues), "status": status,
        "gross_revenue_paise": gross, "discounts_paise": discounts, "taxes_paise": tax,
        "shipping_charged_paise": shipping, "refunds_paise": refunds,
    }
    return OrderBundle(org_id=org_id, channel=CHANNEL, external_id=ext, order=order, items=items,
                       raw_ref={"source": SOURCE, "external_id": raw_external_id or ext}, issues=issues)


def shopify_ref_keys(payload: dict) -> list[str]:
    """Identifiers a shipment may use to refer to this order (id, name, order_number); for linking."""
    keys = [payload.get("id"), payload.get("name"), payload.get("order_number")]
    return [str(k).lstrip("#") for k in keys if k not in (None, "")]
