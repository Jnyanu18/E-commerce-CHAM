"""raw_records -> transforms -> repository. Idempotent and order-independent.

Re-running over the same raw records changes nothing (all writes are keyed upserts with convergent merge rules).
Bad records are collected in ``TransformSummary.errors`` and never abort the batch; data-quality ``issues``
are surfaced, never swallowed. Records that cannot be linked yet (a shipment whose order has not arrived)
are listed in ``deferred`` and are picked up by the next run because everything is re-derivable from raw.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from transforms.common import OrderBundle, TransformError
from transforms.fees import estimate_fees
from transforms.marketplace import MARKETPLACES, channel_from_source, transform_marketplace_rows
from transforms.shiprocket import transform_shiprocket_shipment
from transforms.shopify import shopify_ref_keys, transform_shopify_order

from .repository import Repository

KNOWN_SOURCES = ("shopify_order", "shiprocket_shipment") + tuple(f"csv_{m}" for m in MARKETPLACES)


@dataclass
class TransformSummary:
    orders_applied: int = 0
    shipments_applied: int = 0
    fees_inserted: int = 0
    fees_updated: int = 0
    fees_skipped: int = 0
    deferred: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    skipped_sources: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def apply_bundle(repo: Repository, b: OrderBundle, summary: TransformSummary,
                 fee_rates: dict[str, dict[str, int]] | None = None) -> None:
    org, ch, ext = b.org_id, b.channel, b.external_id
    if b.partial:
        found = repo.find_order(org, ch, ext) if ch and ext else None
        for sh in b.shipments:  # courier rows never depend on the order existing; they link late
            repo.upsert_shipment(org, sh, found["id"] if found else None, b.raw_ref)
        if not found:
            summary.deferred.append({"raw": b.raw_ref, "reason": "order_not_in_ledger", "order_ref": b.channel_ref})
            return
        order_id = found["id"]
        repo.upsert_order(org, ch, ext, {k: v for k, v in b.order.items() if v is not None})
        summary.shipments_applied += 1
    else:
        order_id = repo.upsert_order(org, ch, ext, b.order)
        repo.upsert_items(org, order_id, b.items)
        summary.orders_applied += 1
    fees = list(b.fees)
    rates = (fee_rates or {}).get(ch or "")
    if rates and not b.partial:
        fees += estimate_fees(b.order, rates)
    for f in fees:
        action = repo.upsert_fee(org, order_id, f["type"], f["amount_paise"], f["source"])
        if action == "inserted":
            summary.fees_inserted += 1
        elif action == "updated":
            summary.fees_updated += 1
        else:
            summary.fees_skipped += 1
    for r in b.returns:
        repo.upsert_return(org, order_id, r["type"], r.get("cost_paise"), r.get("returned_at"))
    for s in b.settlements:
        repo.upsert_settlement_item(org, ext, s, b.raw_ref)
    for i in b.issues:
        summary.issues.append({"raw": b.raw_ref, "order": f"{ch}:{ext}", "issue": i})


def _build_shopify_index(rows: list[dict]) -> dict[str, str | None]:
    """ref (id / name / order_number) -> shopify external id; None marks an ambiguous ref."""
    idx: dict[str, str | None] = {}
    for r in rows:
        p = r["payload"]
        if not isinstance(p, dict) or p.get("id") in (None, ""):
            continue
        ext = str(p["id"])
        for k in shopify_ref_keys(p):
            idx[k] = ext if idx.get(k, ext) == ext else None
    return idx


def run_transform(repo: Repository, org_id: str, *, sources: list[str] | None = None, since: str | None = None,
                  fee_rates: dict[str, dict[str, int]] | None = None) -> TransformSummary:
    summary = TransformSummary()
    wanted = [s for s in (sources or KNOWN_SOURCES) if s in KNOWN_SOURCES]
    raw = repo.iter_raw_records(org_id, wanted, since)
    for r in repo.iter_raw_records(org_id, None, since):
        if r["source"] not in KNOWN_SOURCES:
            summary.skipped_sources[r["source"]] = summary.skipped_sources.get(r["source"], 0) + 1
    tiers = repo.load_city_tiers()
    bundles: list[OrderBundle] = []

    def guarded(raw_ref, fn):
        try:
            return fn()
        except (TransformError, ValueError, TypeError, KeyError) as e:
            summary.errors.append({"raw": raw_ref, "error": f"{type(e).__name__}: {e}"})
            return None

    mkt: dict[str, "OrderedDict[str, list]"] = {}
    for r in raw:
        ref = {"source": r["source"], "external_id": r["external_id"]}
        src, p = r["source"], r["payload"]
        if src == "shopify_order":
            b = guarded(ref, lambda: transform_shopify_order(org_id, p, raw_external_id=r["external_id"], city_tiers=tiers))
            if b:
                bundles.append(b)
        elif src == "shiprocket_shipment":
            b = guarded(ref, lambda: transform_shiprocket_shipment(org_id, p, raw_external_id=r["external_id"]))
            if b:
                bundles.append(b)
        else:  # csv_<marketplace>: group by order_id so one bad order cannot sink the file
            if not isinstance(p, dict) or not str(p.get("order_id") or "").strip():
                summary.errors.append({"raw": ref, "error": "TransformError: order_id missing"})
                continue
            mkt.setdefault(channel_from_source(src), OrderedDict()).setdefault(str(p["order_id"]).strip(), []).append((r["external_id"], p))
    for ch, groups in mkt.items():
        for oid, rows in groups.items():
            out = guarded({"source": f"csv_{ch}", "external_id": rows[0][0]},
                          lambda: transform_marketplace_rows(org_id, ch, rows, city_tiers=tiers))
            bundles.extend(out or [])

    index = _build_shopify_index(repo.iter_raw_records(org_id, ["shopify_order"]))
    for b in bundles:
        if b.partial and b.channel_ref is not None:  # resolve the shipment's order reference
            ext = index.get(b.channel_ref, b.channel_ref)
            if ext is None:
                summary.errors.append({"raw": b.raw_ref, "error": f"ambiguous order reference {b.channel_ref!r}"})
                b.channel = None
                continue
            b.channel, b.external_id = "shopify", ext
    bundles = [b for b in bundles if not (b.partial and b.channel is None and any(
        e["raw"] == b.raw_ref for e in summary.errors))]
    # full orders first so shipments arriving in the same batch can link; the result is order-independent anyway
    for b in sorted(bundles, key=lambda x: x.partial):
        apply_bundle(repo, b, summary, fee_rates)
    return summary
