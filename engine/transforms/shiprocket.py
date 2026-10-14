"""Shiprocket shipment -> PARTIAL canonical bundle (status patch, shipping fees, RTO/return cost, COD remittance).

ASSUMED PAYLOAD SHAPE (UNVERIFIED; connectors agent must confirm against Shiprocket's current API docs):
``id`` or ``shipment_id``, ``awb`` (or ``awb_code``), ``channel_order_id`` (reference to the merchant order:
assumed to be the Shopify order id, name or order_number), ``status`` (free text, e.g. "DELIVERED",
"RTO DELIVERED", "IN TRANSIT"), ``is_return`` (bool; reverse shipment for a customer return),
``freight_charges``, ``cod_charges``, ``rto_charges`` (rupees), ``cod`` (0/1), ``cod_remitted_amount``,
``cod_remittance_date``, ``cod_remittance_ref``, ``delivered_date``, ``updated_at``. Status strings are mapped
by keyword; unmapped statuses leave the order status unchanged and raise an issue.

Mapping choices:
* forward ``freight_charges``          -> fee type 'shipping', source 'api'
* ``cod_charges``                       -> fee type 'payment_gateway' (COD collection fee), source 'api'
* RTO: returns row type 'RTO', cost = ``rto_charges`` (reverse freight). Forward freight stays a shipping fee.
* ``is_return`` shipment: returns row 'customer_return', cost = ``freight_charges`` (reverse freight);
  no 'shipping' fee row is created for it.
* ``cod_remitted_amount``               -> settlement (source 'shiprocket_cod').
"""
from __future__ import annotations

from typing import Any

from .common import OrderBundle, TransformError, parse_ts, to_paise, ts_iso

SOURCE = "shiprocket_shipment"


def map_status(raw: str | None, is_return: bool) -> str | None:
    s = (raw or "").strip().upper()
    if not s:
        return None
    if "RTO" in s:
        return "rto"
    if "CANCEL" in s:
        return "cancelled"
    if is_return:
        return "returned" if "DELIVERED" in s else None
    if "DELIVERED" in s and "UNDELIVERED" not in s:
        return "delivered"
    if any(k in s for k in ("SHIPPED", "TRANSIT", "OUT FOR DELIVERY", "PICKED", "DISPATCH")):
        return "shipped"
    return None


def transform_shiprocket_shipment(org_id: str, payload: dict[str, Any], *, raw_external_id: str | None = None) -> OrderBundle:
    if not isinstance(payload, dict):
        raise TransformError("shiprocket payload is not an object")
    sid = payload.get("id") or payload.get("shipment_id") or payload.get("awb") or payload.get("awb_code")
    ref = payload.get("channel_order_id")
    if sid in (None, ""):
        raise TransformError("shiprocket shipment without id/awb")
    if ref in (None, ""):
        raise TransformError(f"shiprocket shipment {sid}: channel_order_id missing, cannot link to an order")
    issues: list[str] = []
    is_return = bool(payload.get("is_return"))
    status = map_status(payload.get("status"), is_return)
    if status is None and payload.get("status"):
        issues.append(f"unmapped_shipment_status: {payload.get('status')!r}")

    when = parse_ts(payload.get("delivered_date") or payload.get("updated_at"), field_name="shipment date")
    freight = to_paise(payload.get("freight_charges"), field_name="freight_charges", default=None)
    cod_ch = to_paise(payload.get("cod_charges"), field_name="cod_charges", default=None)
    rto_ch = to_paise(payload.get("rto_charges"), field_name="rto_charges", default=None)

    fees, returns, settlements = [], [], []
    if freight is not None and not is_return:
        fees.append({"type": "shipping", "amount_paise": freight, "source": "api"})
    if cod_ch:
        fees.append({"type": "payment_gateway", "amount_paise": cod_ch, "source": "api"})
    if status == "rto":
        if rto_ch is None:
            issues.append("rto_cost_missing: RTO recorded with unknown cost")
        returns.append({"type": "RTO", "cost_paise": rto_ch, "returned_at": ts_iso(when)})
    if is_return:
        if freight is None:
            issues.append("customer_return_cost_missing")
        returns.append({"type": "customer_return", "cost_paise": freight,
                        "returned_at": ts_iso(when) if status == "returned" else None})

    remitted = to_paise(payload.get("cod_remitted_amount"), field_name="cod_remitted_amount", default=None)
    if remitted is not None:
        sref = str(payload.get("cod_remittance_ref") or f"shiprocket:{sid}")
        settlements.append({"settlement_ref": sref, "source": "shiprocket_cod", "amount_paise": remitted,
                            "settled_at": ts_iso(parse_ts(payload.get("cod_remittance_date"), field_name="cod_remittance_date"))})

    return OrderBundle(org_id=org_id, channel=None, external_id=None, order={"status": status}, fees=fees,
                       returns=returns, settlements=settlements, partial=True, channel_ref=str(ref).lstrip("#"),
                       raw_ref={"source": SOURCE, "external_id": raw_external_id or str(sid)}, issues=issues)
