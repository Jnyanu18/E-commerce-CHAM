"""Reconciliation: orders vs settlements vs fees vs returns -> structured discrepancy report.

Read-only. It NEVER adjusts, nets away or "explains" a difference: every unexplained gap is a discrepancy row
with expected / actual / diff in paise and a machine-readable code. It does not compute business metrics.

Expected payout for an order (the identity under test):
    customer_paid = gross_revenue - discounts + shipping_charged                (GST-inclusive, see transforms.common)
    expected_payout = customer_paid - refunds - sum(actual fees whose type is netted by that settlement source)
Which fee types a settlement source nets is an ASSUMPTION (``DEFAULT_NETTED_FEE_TYPES``): marketplace CSV
settlements net all fee types; Shiprocket COD remittance nets only the COD charge ('payment_gateway'). Override
via ``netted_fee_types`` once real samples exist. Only ACTUAL fees (api/csv) count; if an order has only
estimated fees the comparison is still made but flagged ``basis: estimated_fees``.

Codes (severity): settlement_amount_mismatch (error), settlement_missing (warning), settlement_without_order
(error), settlement_for_cancelled_order (error), settlement_for_rto_order (error), possible_duplicate_settlement
(warning), refund_exceeds_paid (error), return_without_refund (warning), rto_prepaid_not_refunded (warning),
status_without_return_record (warning), refund_without_return_record (info), missing_sku (warning),
no_actual_fees (info), items_vs_gross_mismatch (info).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from transforms.common import MISSING_SKU

ALL_FEES = "*"
DEFAULT_NETTED_FEE_TYPES: dict[str, Any] = {"csv_": ALL_FEES, "shiprocket_cod": {"payment_gateway"}}
ACTUAL = ("api", "csv")
SEVERITY = {"settlement_amount_mismatch": "error", "settlement_missing": "warning", "settlement_without_order": "error",
            "settlement_for_cancelled_order": "error", "settlement_for_rto_order": "error",
            "possible_duplicate_settlement": "warning", "refund_exceeds_paid": "error", "return_without_refund": "warning",
            "rto_prepaid_not_refunded": "warning", "status_without_return_record": "warning",
            "refund_without_return_record": "info", "missing_sku": "warning", "no_actual_fees": "info",
            "items_vs_gross_mismatch": "info"}
AMOUNT_CODES = {"settlement_amount_mismatch", "settlement_missing", "settlement_without_order",
                "settlement_for_cancelled_order", "settlement_for_rto_order"}


def _netted(source: str, table: dict) -> Any:
    for prefix, v in table.items():
        if source == prefix or (prefix.endswith("_") and source.startswith(prefix)):
            return v
    return ALL_FEES  # unknown source: assume everything is netted, flagged in assumptions


def _dt(v: Any) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def reconcile_snapshot(org_id: str, start: date, end: date, snap: dict[str, list[dict]], *, as_of: date,
                       tolerance_paise: int = 0, grace_days: int = 14,
                       netted_fee_types: dict[str, Any] | None = None) -> dict:
    table = netted_fee_types or DEFAULT_NETTED_FEE_TYPES
    fees_by, ret_by, items_by, set_by = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for f in snap.get("fees", []):
        fees_by[f["order_id"]].append(f)
    for r in snap.get("returns", []):
        ret_by[r["order_id"]].append(r)
    for i in snap.get("order_items", []):
        items_by[i["order_id"]].append(i)
    orphans = []
    for s in snap.get("settlements", []):
        (set_by[s["order_id"]] if s.get("order_id") else orphans).append(s)

    chans: dict[str, dict] = {}

    def ch(name: str) -> dict:
        return chans.setdefault(name, {"channel": name, "discrepancies": [], "totals": defaultdict(int)})

    def flag(channel: str, code: str, order_ext: str | None, expected=None, actual=None, **detail) -> None:
        d = {"code": code, "severity": SEVERITY[code], "order_external_id": order_ext, "expected_paise": expected,
             "actual_paise": actual, "diff_paise": None if expected is None or actual is None else actual - expected,
             "detail": detail}
        ch(channel)["discrepancies"].append(d)

    for o in sorted(snap.get("orders", []), key=lambda x: (x["channel"], x["external_id"])):
        c, ext, oid = o["channel"], o["external_id"], o["id"]
        t = ch(c)["totals"]
        paid = o["gross_revenue_paise"] - o.get("discounts_paise", 0) + o.get("shipping_charged_paise", 0)
        refunds = o.get("refunds_paise", 0)
        fees = fees_by.get(oid, [])
        actual_fees = [f for f in fees if f["source"] in ACTUAL]
        t["orders"] += 1
        t["customer_paid_paise"] += paid
        t["refunds_paise"] += refunds
        t["actual_fees_paise"] += sum(f["amount_paise"] for f in actual_fees)
        t["estimated_fees_paise"] += sum(f["amount_paise"] for f in fees if f["source"] == "estimated")
        t["return_cost_paise"] += sum(r.get("cost_paise") or 0 for r in ret_by.get(oid, []))
        status, pm = o["status"], o["payment_method"]
        rets = {r["type"] for r in ret_by.get(oid, [])}

        if refunds > paid:
            flag(c, "refund_exceeds_paid", ext, paid, refunds)
        if status == "returned" and "customer_return" not in rets or status == "rto" and "RTO" not in rets:
            flag(c, "status_without_return_record", ext, status=status)
        if "customer_return" in rets and refunds == 0:
            flag(c, "return_without_refund", ext, expected=None, actual=0, order_paid_paise=paid)
        if status == "rto" and pm == "prepaid" and refunds == 0:
            flag(c, "rto_prepaid_not_refunded", ext, paid, 0)
        if refunds > 0 and not rets and status != "cancelled":
            flag(c, "refund_without_return_record", ext, actual=refunds)
        if any(i["sku"] == MISSING_SKU for i in items_by.get(oid, [])):
            flag(c, "missing_sku", ext)
        isum = sum(i["qty"] * i["unit_price_paise"] for i in items_by.get(oid, []))
        if items_by.get(oid) and isum != o["gross_revenue_paise"]:
            flag(c, "items_vs_gross_mismatch", ext, o["gross_revenue_paise"], isum)
        expects_settlement = c != "shopify" or pm == "cod"
        if expects_settlement and status in ("delivered", "returned") and not actual_fees:
            flag(c, "no_actual_fees", ext, estimated_only=bool(fees))

        sets = set_by.get(oid, [])
        settled = sum(s["amount_paise"] for s in sets)
        t["settled_paise"] += settled
        seen = defaultdict(list)
        for s in sets:
            seen[(s["source"], s["amount_paise"])].append(s["settlement_ref"])
        for (src, amt), refs in seen.items():
            if len(refs) > 1:
                flag(c, "possible_duplicate_settlement", ext, actual=amt * len(refs), refs=sorted(refs), source=src)
        if status == "cancelled":
            if settled != 0:
                flag(c, "settlement_for_cancelled_order", ext, 0, settled)
            continue
        if status == "rto" and pm == "cod":
            if settled != 0:
                flag(c, "settlement_for_rto_order", ext, 0, settled)
            continue
        if sets:
            by_src: dict[str, int] = defaultdict(int)
            for s in sets:
                by_src[s["source"]] += s["amount_paise"]
            exp_total = 0
            for src in by_src:
                net = _netted(src, table)
                netted = sum(f["amount_paise"] for f in actual_fees if net == ALL_FEES or f["type"] in net)
                exp_total += paid - refunds - netted if len(by_src) == 1 else 0
            if len(by_src) > 1:  # several settlement sources for one order: cannot attribute fees reliably
                net_all = sum(f["amount_paise"] for f in actual_fees)
                exp_total = paid - refunds - net_all
            t["expected_paise"] += exp_total
            if abs(settled - exp_total) > tolerance_paise:
                flag(c, "settlement_amount_mismatch", ext, exp_total, settled,
                     basis="actual_fees" if actual_fees else "estimated_fees" if fees else "no_fees",
                     sources=sorted(by_src), customer_paid_paise=paid, refunds_paise=refunds)
        elif expects_settlement and status in ("delivered", "returned") and _dt(o["ordered_at"]).date() + timedelta(days=grace_days) <= as_of:
            exp = paid - refunds - sum(f["amount_paise"] for f in actual_fees)
            t["expected_paise"] += exp
            flag(c, "settlement_missing", ext, exp, 0, grace_days=grace_days)

    for s in orphans:
        c = s["channel"]
        ch(c)["totals"]["settled_paise"] += s["amount_paise"]
        flag(c, "settlement_without_order", s["order_external_id"], 0, s["amount_paise"], settlement_ref=s["settlement_ref"],
             source=s["source"])

    out = []
    for name in sorted(chans):
        c = chans[name]
        disc = sorted(c["discrepancies"], key=lambda d: (d["code"], d["order_external_id"] or ""))
        t = dict(c["totals"])
        for k in ("orders", "customer_paid_paise", "refunds_paise", "actual_fees_paise", "estimated_fees_paise",
                  "return_cost_paise", "settled_paise", "expected_paise"):
            t.setdefault(k, 0)
        amt = [d for d in disc if d["code"] in AMOUNT_CODES]
        t["unexplained_abs_paise"] = sum(abs(d["diff_paise"] or 0) for d in amt)
        t["unexplained_net_paise"] = sum(d["diff_paise"] or 0 for d in amt)
        counts: dict[str, int] = defaultdict(int)
        for d in disc:
            counts[d["code"]] += 1
        sev = {d["severity"] for d in disc}
        status = "discrepancies" if sev & {"error", "warning"} else "ok"
        out.append({"channel": name, "status": status, "totals": t, "summary_by_code": dict(sorted(counts.items())),
                    "discrepancies": disc})
    return {"org_id": org_id, "period": {"start": start.isoformat(), "end": end.isoformat()}, "as_of": as_of.isoformat(),
            "tolerance_paise": tolerance_paise, "grace_days": grace_days,
            "assumptions": {"netted_fee_types": {k: (v if v == ALL_FEES else sorted(v)) for k, v in table.items()},
                            "note": "settlement-netting rules are assumed until real settlement samples exist"},
            "channels": out}
