"""Marketplace / quick-commerce CSV rows -> canonical bundles, via a NEUTRAL intermediate row schema.

ASSUMED SHAPE, NOT A REAL REPORT FORMAT. No sample Amazon/Flipkart/Blinkit/Zepto/Instamart report has been
provided (CLAUDE.md: never guess formats). The connectors agent lands each parsed CSV row in ``raw_records``
(source ``csv_<marketplace>``) after mapping the marketplace's columns onto this neutral schema (via the
``csv_mappings`` flow); until real samples exist every marketplace mapping below is an ASSUMPTION.

Neutral row (one row per order LINE; all money columns are RUPEES, per line, and are SUMMED across the
lines of an order, so repeat a value on one line only):
  order_id*        marketplace order id (string)               line_id        optional unique line id (dedupe key)
  order_date*      ISO-8601; naive values are read as IST      sku, qty*      qty defaults to 1 if blank
  item_price*      UNIT price, GST-inclusive                   discount       line discount total
  tax              GST contained in the line price             shipping_charged  charged to customer
  order_status     free text -> placed|shipped|delivered|cancelled|returned|rto (keyword mapped)
  payment_method   prepaid|cod (default prepaid, issue raised if blank on a non-quick-commerce channel)
  city, pincode, customer_email, customer_phone
  refund_amount    refunded to customer                        return_type    customer_return|rto
  return_cost      logistics/handling loss of the return
  commission_fee, fixed_fee (-> 'fulfilment'), shipping_fee, payment_fee (-> 'payment_gateway'), other_fee
                   marketplace-reported fees, taken exactly as reported (GST on fees, if any, is whatever
                   the column contains; documented per mapping once samples exist). Source tag = 'csv'.
  settlement_ref, settlement_amount, settlement_date   NET payout credited for the order line(s)
  settlement_gross, settlement_fees   optional split (net = gross - fees, both positive); if absent the split is
                   unknown and an issue is raised (net-only item: gross = net, fees = 0)

GST: item_price is assumed GST-inclusive for all marketplaces (the Indian consumer-price convention); ``tax``
is the GST contained in it, so gross_revenue = sum(unit price x qty) with no uplift.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable

from .common import (IST, MISSING_SKU, OrderBundle, TransformError, city_tier, customer_key, make_settlement_item,
                     merge_lines, normalise_pincode, parse_ts, to_paise, ts_iso)

MARKETPLACES = ("amazon", "flipkart", "blinkit", "zepto", "instamart")
QUICK_COMMERCE = ("blinkit", "zepto", "instamart")
FEE_COLUMNS = {"commission_fee": "commission", "fixed_fee": "fulfilment", "shipping_fee": "shipping",
               "payment_fee": "payment_gateway", "other_fee": "other"}


def channel_from_source(source: str) -> str:
    if not source.startswith("csv_") or source[4:] not in MARKETPLACES:
        raise TransformError(f"not a supported marketplace csv source: {source!r}")
    return source[4:]


def map_order_status(raw: str | None) -> str | None:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "rto" in s or "return to origin" in s:
        return "rto"
    if "cancel" in s:
        return "cancelled"
    if "return" in s:
        return "returned"
    if "deliver" in s and "undeliver" not in s:
        return "delivered"
    if any(k in s for k in ("ship", "dispatch", "transit")):
        return "shipped"
    if any(k in s for k in ("new", "placed", "pending", "confirmed", "accepted")):
        return "placed"
    return None


def _rank(status: str) -> int:
    from .common import STATUS_RANK
    return STATUS_RANK[status]


def transform_marketplace_rows(org_id: str, channel: str, rows: Iterable[tuple[str, dict[str, Any]]], *,
                               city_tiers: dict[str, str] | None = None) -> list[OrderBundle]:
    """Group neutral rows by ``order_id`` and build one bundle per order. ``rows`` = (raw external_id, row).

    Unusable input (no order_id / bad amount / no date) raises ``TransformError``; the caller records it.
    """
    if channel not in MARKETPLACES:
        raise TransformError(f"unsupported channel {channel!r}")
    groups: "OrderedDict[str, list[tuple[str, dict]]]" = OrderedDict()
    for raw_id, row in rows:
        oid = str(row.get("order_id") or "").strip()
        if not oid:
            raise TransformError(f"{channel} row {raw_id}: order_id missing")
        groups.setdefault(oid, []).append((raw_id, row))
    return [_build(org_id, channel, oid, g, city_tiers) for oid, g in groups.items()]


def _build(org_id: str, channel: str, oid: str, group: list[tuple[str, dict]], tiers) -> OrderBundle:
    issues: list[str] = []
    seen_lines: set[str] = set()
    lines, tax, disc, ship, refunds, ret_cost = [], 0, 0, 0, 0, None
    fee_tot: dict[str, int] = {}
    settle: "OrderedDict[str, dict]" = OrderedDict()
    statuses, ret_types = [], set()
    first = group[0][1]
    ordered_at = None
    for raw_id, r in group:
        lid = str(r.get("line_id") or "").strip()
        if lid:
            if lid in seen_lines:
                issues.append(f"duplicate_line_skipped: {lid}")
                continue
            seen_lines.add(lid)
        d = parse_ts(r.get("order_date"), naive_tz=IST, field_name="order_date")
        if d is not None and (ordered_at is None or d < ordered_at):
            ordered_at = d
        sku = (r.get("sku") or "").strip()
        if not sku:
            sku = MISSING_SKU
            issues.append(f"missing_sku: order {oid}")
        qty = int(str(r.get("qty") or "1").strip() or 1)
        if qty <= 0:
            issues.append("zero_qty_line_skipped")
            continue
        lines.append({"sku": sku, "qty": qty, "unit_price_paise": to_paise(r.get("item_price"), field_name="item_price")})
        tax += to_paise(r.get("tax"), field_name="tax")
        disc += to_paise(r.get("discount"), field_name="discount")
        ship += to_paise(r.get("shipping_charged"), field_name="shipping_charged")
        refunds += to_paise(r.get("refund_amount"), field_name="refund_amount")
        rc = to_paise(r.get("return_cost"), field_name="return_cost", default=None)
        if rc is not None:
            ret_cost = (ret_cost or 0) + rc
        for col, ftype in FEE_COLUMNS.items():
            v = to_paise(r.get(col), field_name=col, default=None)
            if v is not None:
                fee_tot[ftype] = fee_tot.get(ftype, 0) + v
        st = map_order_status(r.get("order_status"))
        if st:
            statuses.append(st)
        elif r.get("order_status"):
            issues.append(f"unmapped_order_status: {r.get('order_status')!r}")
        rt = str(r.get("return_type") or "").strip().lower()
        if rt in ("customer_return", "rto"):
            ret_types.add(rt)
        elif rt:
            issues.append(f"unmapped_return_type: {rt!r}")
        sref = str(r.get("settlement_ref") or "").strip()
        if sref and r.get("settlement_amount") not in (None, ""):
            s = settle.setdefault(sref, {"ref": sref, "net": 0, "gross": None, "fees": None,
                                         "at": ts_iso(parse_ts(r.get("settlement_date"), naive_tz=IST,
                                                               field_name="settlement_date"))})
            s["net"] += to_paise(r.get("settlement_amount"), field_name="settlement_amount")
            for k, col in (("gross", "settlement_gross"), ("fees", "settlement_fees")):
                v = to_paise(r.get(col), field_name=col, default=None)
                if v is not None:
                    s[k] = (s[k] or 0) + v

    if ordered_at is None:
        raise TransformError(f"{channel} order {oid}: order_date missing")
    if not lines:
        issues.append("no_line_items")
    items = merge_lines(lines, issues)
    gross = sum(i["unit_price_paise"] * i["qty"] for i in items)

    status = max(statuses, key=_rank) if statuses else "placed"
    if not statuses:
        issues.append("order_status_missing: defaulted to placed")
    pm = str(first.get("payment_method") or "").strip().lower()
    if pm not in ("prepaid", "cod"):
        if pm:
            issues.append(f"unmapped_payment_method: {pm!r}")
        pm = "prepaid"
        if channel not in QUICK_COMMERCE or not first.get("payment_method"):
            issues.append("payment_method_assumed_prepaid")
    if refunds > gross - disc + ship:
        issues.append(f"refund_exceeds_paid: {refunds} > {gross - disc + ship}")

    city = (first.get("city") or "").strip() or None
    pin = normalise_pincode(first.get("pincode"))
    if first.get("pincode") and pin is None:
        issues.append(f"invalid_pincode: {first.get('pincode')!r}")

    returns = []
    if status == "rto" or "rto" in ret_types:
        returns.append({"type": "RTO", "cost_paise": ret_cost, "returned_at": None})
    if status == "returned" or "customer_return" in ret_types:
        returns.append({"type": "customer_return", "cost_paise": ret_cost, "returned_at": None})
    if returns and ret_cost is None:
        issues.append("return_cost_missing: return recorded with unknown cost")

    order = {
        "ordered_at": ts_iso(ordered_at),
        "customer_key": customer_key(first.get("customer_email"), first.get("customer_phone")),
        "city": city, "city_tier": city_tier(city, tiers), "pincode": pin,
        "payment_method": pm, "status": status, "gross_revenue_paise": gross, "discounts_paise": disc,
        "taxes_paise": tax, "shipping_charged_paise": ship, "refunds_paise": refunds,
    }
    fees = [{"type": t, "amount_paise": a, "source": "csv"} for t, a in sorted(fee_tot.items())]
    return OrderBundle(org_id=org_id, channel=channel, external_id=oid, order=order, items=items, fees=fees,
                       returns=returns,
                       settlements=[make_settlement_item(channel=channel, ref=s["ref"], source="csv", order_channel=channel,
                                                         settled_at=s["at"], net_paise=s["net"], gross_paise=s["gross"],
                                                         fees_paise=s["fees"], issues=issues) for s in settle.values()],
                       raw_ref={"source": f"csv_{channel}", "external_id": group[0][0]}, issues=issues)
