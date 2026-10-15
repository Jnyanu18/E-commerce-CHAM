"""Convert the synthetic canonical orders into RAW source records, so the seed exercises the real pipeline:
seed -> raw_records -> ledger transforms -> metrics -> API.

Shapes mirror what the connectors persist (Shopify GraphQL order node with hashed customerIdentity) and the
ledger's neutral marketplace row schema; Shiprocket field names are the ledger's UNCONFIRMED assumed names.
Everything here is synthetic. `ground_truth()` computes expected totals directly from the canonical seed rows
(plain sums, independent of the metric layer) for end-to-end assertions.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app import identity

from .synthetic import IST, SKUS


def rupees(paise: int) -> str:
    return f"{Decimal(paise) / 100:.2f}"


def _money(paise: int) -> dict:
    return {"shopMoney": {"amount": rupees(paise), "currencyCode": "INR"}}


def _iso(dt) -> str:
    return dt.astimezone(IST).isoformat()


def _index(data: dict) -> tuple[dict, dict, dict]:
    items, fees, rets = defaultdict(list), defaultdict(list), {}
    for it in data["order_items"]:
        items[it["order_ext"]].append(it)
    for f in data["fees"]:
        fees[f["order_ext"]].append(f)
    for r in data["returns"]:
        rets[r["order_ext"]] = r
    return items, fees, rets


def _shopify_node(o: dict, items: list[dict]) -> dict:
    num = o["external_id"].split("-", 1)[1]
    nodes = [{"id": f"gid://shopify/LineItem/{num}{k}", "name": it["sku"], "title": it["sku"], "sku": it["sku"],
              "quantity": it["qty"], "currentQuantity": it["qty"], "originalUnitPriceSet": _money(it["unit_price_paise"]),
              "discountedUnitPriceSet": _money(it["unit_price_paise"]), "totalDiscountSet": _money(0)}
             for k, it in enumerate(items)]
    paid = o["gross_revenue_paise"] - o["discounts_paise"] + o["shipping_charged_paise"]
    cod = o["payment_method"] == "cod"
    refunds = ([{"id": f"gid://shopify/Refund/{num}", "totalRefundedSet": _money(o["refunds_paise"])}]
               if o["refunds_paise"] else [])
    return {
        "id": f"gid://shopify/Order/{num}", "name": f"#{num}", "createdAt": _iso(o["ordered_at"]),
        "updatedAt": _iso(o["ordered_at"]), "processedAt": _iso(o["ordered_at"]), "cancelledAt": None,
        "displayFinancialStatus": "PENDING" if cod else "PAID", "displayFulfillmentStatus": "FULFILLED",
        "currencyCode": "INR", "paymentGatewayNames": ["Cash on Delivery (COD)"] if cod else ["razorpay"], "tags": [],
        "sourceName": "web", "customer": {"id": "gid://shopify/Customer/0"},
        "customerIdentity": {"emailHash": identity.hash_email(o["customer_email"]), "phoneHash": None},
        "subtotalPriceSet": _money(o["gross_revenue_paise"]), "totalPriceSet": _money(paid),
        "totalDiscountsSet": _money(o["discounts_paise"]), "totalShippingPriceSet": _money(o["shipping_charged_paise"]),
        "totalTaxSet": _money(o["taxes_paise"]), "totalRefundedSet": _money(o["refunds_paise"]),
        "shippingAddress": {"city": o["city"], "provinceCode": "XX", "zip": o["pincode"], "countryCodeV2": "IN"},
        "transactions": [], "lineItems": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes},
        "refunds": refunds,
    }


def _shipments(o: dict, fees: list[dict], ret: dict | None) -> list[dict]:
    num = o["external_id"].split("-", 1)[1]
    by = {f["type"]: f["amount_paise"] for f in fees}
    fwd = {"id": int(num) * 10 + 1, "channel_order_id": num, "awb": f"AWB{num}",
           "status": "RTO DELIVERED" if o["status"] == "rto" else "DELIVERED",
           "cod": 1 if o["payment_method"] == "cod" else 0, "freight_charges": rupees(by.get("shipping", 0)),
           "updated_at": _iso(o["ordered_at"])}
    if o["payment_method"] == "cod":
        fwd["cod_charges"] = rupees(by.get("other", 0))
    if o["status"] == "rto" and ret:
        fwd["rto_charges"] = rupees(ret["cost_paise"])
    out = [fwd]
    if o["status"] == "returned" and ret:
        out.append({"id": int(num) * 10 + 2, "channel_order_id": num, "awb": f"RAWB{num}", "status": "DELIVERED",
                    "is_return": True, "freight_charges": rupees(ret["cost_paise"]), "updated_at": _iso(ret["returned_at"])})
    return out


def _marketplace_rows(o: dict, items: list[dict], fees: list[dict], ret: dict | None) -> list[dict]:
    by = {f["type"]: f["amount_paise"] for f in fees}
    status = {"rto": "RTO", "returned": "Returned", "delivered": "Delivered"}[o["status"]]
    rows = []
    for k, it in enumerate(items):
        r = {"order_id": o["external_id"], "line_id": f"{o['external_id']}-{k}", "order_date": _iso(o["ordered_at"]),
             "sku": it["sku"], "qty": it["qty"], "item_price": rupees(it["unit_price_paise"]), "order_status": status,
             "payment_method": "prepaid", "city": o["city"], "pincode": o["pincode"],
             "customer_email": o["customer_email"]}
        if k == 0:  # order-level amounts live on the first line only (the neutral schema sums them)
            r.update({"discount": rupees(o["discounts_paise"]), "tax": rupees(o["taxes_paise"]),
                      "commission_fee": rupees(by.get("commission", 0)), "shipping_fee": rupees(by.get("shipping", 0)),
                      "refund_amount": rupees(o["refunds_paise"])})
            if ret:
                r.update({"return_type": "rto" if ret["type"] == "RTO" else "customer_return",
                          "return_cost": rupees(ret["cost_paise"])})
        rows.append(r)
    return rows


def _ad_rows(data: dict) -> list[dict]:
    """Meta campaign-level daily insights and Google Ads rows, in the shapes the connectors persist."""
    out = []
    for a in data["ad_spend_daily"]:
        day = a["date"].isoformat()
        if a["platform"] == "meta":
            p = {"account_id": "111222333", "account_currency": "INR", "campaign_id": a["campaign_id"],
                 "campaign_name": a["campaign_name"], "date_start": day, "date_stop": day,
                 "spend": rupees(a["spend_paise"]), "impressions": str(a["impressions"]), "clicks": str(a["clicks"]),
                 "actions": [{"action_type": "purchase", "value": f"{a['platform_reported_conversions']:.2f}"}],
                 "action_values": [{"action_type": "purchase", "value": rupees(a["platform_reported_revenue_paise"])}]}
            out.append({"source": "meta_insights", "external_id": f"campaign:111222333:{a['campaign_id']}:{day}", "payload": p})
        else:  # Google: cost in micros; conversions not fetched by default -> stay NULL downstream
            p = {"campaign": {"resourceName": f"customers/1234567890/campaigns/{a['campaign_id']}", "id": a["campaign_id"],
                              "name": a["campaign_name"]},
                 "metrics": {"impressions": str(a["impressions"]), "clicks": str(a["clicks"]),
                             "costMicros": str(a["spend_paise"] * 10_000)},
                 "segments": {"date": day}}
            out.append({"source": "google_ads_row", "external_id": f"campaign:{a['campaign_id']}:{day}", "payload": p})
    return out


def to_raw(data: dict) -> list[dict]:
    """-> [{source, external_id, payload}] ready for raw_records (org_id is added by the loader)."""
    items, fees, rets = _index(data)
    out: list[dict] = []
    for o in data["orders"]:
        ext = o["external_id"]
        if o["channel"] == "shopify":
            node = _shopify_node(o, items[ext])
            out.append({"source": "shopify_order", "external_id": node["id"], "payload": node})
            for s in _shipments(o, fees[ext], rets.get(ext)):
                out.append({"source": "shiprocket_shipment", "external_id": str(s["id"]), "payload": s})
        else:
            for row in _marketplace_rows(o, items[ext], fees[ext], rets.get(ext)):
                out.append({"source": f"csv_{o['channel']}", "external_id": row["line_id"], "payload": row})
    return out + _ad_rows(data)


# Prepaid Shopify payment-gateway fees are not carried by any source record; the ledger estimates them from this rate
# (2% of customer-paid value, matching the seed). COD orders carry an actual cod_charges fee instead.
FEE_RATES_BPS = {"shopify": {"payment_gateway": 200}}


def ground_truth(data: dict) -> dict:
    """Plain sums straight from the canonical seed rows (no metric-layer code). RTO orders realise no sale."""
    cogs = {sku: c * 100 for sku, _, _, c in SKUS}
    items, _fees, _rets = _index(data)
    gt = {"orders": len(data["orders"]), "net_revenue_paise": 0, "refunds_paise": 0, "cogs_paise": 0,
          "by_channel_net_revenue": defaultdict(int), "status_counts": defaultdict(int),
          "fees_paise": sum(f["amount_paise"] for f in data["fees"]),
          "return_cost_paise": sum(r["cost_paise"] for r in data["returns"])}
    for o in data["orders"]:
        gt["status_counts"][o["status"]] += 1
        if o["status"] == "rto":
            continue
        net = o["gross_revenue_paise"] - o["discounts_paise"] - o["taxes_paise"] - o["refunds_paise"]
        gt["net_revenue_paise"] += net
        gt["by_channel_net_revenue"][o["channel"]] += net
        gt["refunds_paise"] += o["refunds_paise"]
        gt["cogs_paise"] += sum(cogs[i["sku"]] * i["qty"] for i in items[o["external_id"]])
    # RTO orders realise no sale/COGS but still carry fees and return cost (metric-layer rule, documented in DECISIONS.md)
    gt["contribution_margin_paise"] = (gt["net_revenue_paise"] - gt["cogs_paise"] - gt["fees_paise"]
                                       - gt["return_cost_paise"])
    gt["ad_spend_paise"] = sum(a["spend_paise"] for a in data["ad_spend_daily"])
    gt["meta_spend_paise"] = sum(a["spend_paise"] for a in data["ad_spend_daily"] if a["platform"] == "meta")
    gt["meta_platform_revenue_paise"] = sum(a["platform_reported_revenue_paise"] for a in data["ad_spend_daily"]
                                            if a["platform"] == "meta")
    gt["by_channel_net_revenue"] = dict(gt["by_channel_net_revenue"])
    gt["status_counts"] = dict(gt["status_counts"])
    return gt
