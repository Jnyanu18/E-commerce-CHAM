"""Persistence abstraction for the canonical ledger.

``Repository`` is the only thing the pipeline talks to. ``InMemoryRepository`` backs unit tests;
``ledger.pg_repository.PsycopgRepository`` is the Postgres implementation. Both implement the SAME merge rules:

* orders   key (org_id, channel, external_id). Non-None fields overwrite; None fields are ignored. ``status``
           merges by precedence (transforms.common.STATUS_RANK) so out-of-order updates converge.
* items    key (order_id, sku)            upsert in place.
* fees     key (order_id, type, source)   precedence api > csv > estimated per (order, type):
           an estimate is skipped if an actual exists; an actual removes a lower-precedence row.
* returns  key (order_id, type)           cost/returned_at overwritten only by non-None values.
* settlements  header key (org_id, channel, external_id); items key (settlement, order_external_id, line_type)
           (migration 0007). Header gross/fees/net are RECOMPUTED from items after every item upsert, so partial
           runs cannot shrink a header. Item order_id is resolved from (org, order_channel, order_external_id) and
           stays null (unreconciled) until the order exists.
* shipments  key (org_id, source, external_id); status merges by precedence, other fields only overwritten by non-None.
"""
from __future__ import annotations

import copy
import uuid
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from transforms.common import STATUS_RANK, ist_date

FEE_PRECEDENCE = {"estimated": 0, "csv": 1, "api": 2}
SHIPMENT_RANK = {"unknown": 0, "created": 1, "in_transit": 2, "out_for_delivery": 3, "delivered": 4, "cancelled": 4,
                 "lost": 4, "rto_initiated": 5, "rto_delivered": 6}
ORDER_REQUIRED = ("ordered_at", "gross_revenue_paise", "payment_method")
ORDER_FIELDS = ("ordered_at", "customer_key", "city", "city_tier", "pincode", "payment_method", "status",
                "gross_revenue_paise", "discounts_paise", "taxes_paise", "shipping_charged_paise", "refunds_paise")


class LedgerError(RuntimeError):
    pass


def merge_status(old: str | None, new: str | None) -> str | None:
    if new is None:
        return old
    if old is None:
        return new
    return new if STATUS_RANK[new] >= STATUS_RANK[old] else old


class Repository(ABC):
    @abstractmethod
    def find_order(self, org_id: str, channel: str, external_id: str) -> dict | None: ...
    @abstractmethod
    def upsert_order(self, org_id: str, channel: str, external_id: str, fields: dict) -> str: ...
    @abstractmethod
    def upsert_items(self, org_id: str, order_id: str, items: list[dict]) -> None: ...
    @abstractmethod
    def upsert_fee(self, org_id: str, order_id: str, fee_type: str, amount_paise: int, source: str) -> str:
        """Returns 'inserted' | 'updated' | 'skipped_lower_precedence'."""
    @abstractmethod
    def upsert_return(self, org_id: str, order_id: str, rtype: str, cost_paise: int | None, returned_at: str | None) -> None: ...
    @abstractmethod
    def upsert_settlement_item(self, org_id: str, order_external_id: str, item: dict, raw_ref: dict) -> None:
        """item = transforms.common.make_settlement_item(...) output."""
    @abstractmethod
    def upsert_shipment(self, org_id: str, shipment: dict, order_id: str | None, raw_ref: dict) -> None: ...
    @abstractmethod
    def iter_raw_records(self, org_id: str, sources: list[str] | None = None, since: str | None = None) -> list[dict]: ...
    @abstractmethod
    def load_city_tiers(self) -> dict[str, str]: ...
    @abstractmethod
    def fetch_snapshot(self, org_id: str, start: date, end: date) -> dict[str, list[dict]]:
        """Ledger rows for orders with ordered_date_ist in [start, end] plus settlements (see reconciliation)."""


class InMemoryRepository(Repository):
    def __init__(self) -> None:
        self.orders: dict[tuple, dict] = {}
        self.items: dict[tuple, dict] = {}
        self.fees: dict[tuple, dict] = {}
        self.returns: dict[tuple, dict] = {}
        self.settlements: dict[tuple, dict] = {}       # header: (org, channel, external_id)
        self.settlement_items: dict[tuple, dict] = {}  # (org, channel, external_id, order_external_id, line_type)
        self.shipments: dict[tuple, dict] = {}
        self.raw: dict[tuple, dict] = {}
        self.city_tiers: dict[str, str] = {}

    # test helper: mimics connectors' idempotent raw upsert on (org_id, source, external_id)
    def add_raw_record(self, org_id: str, source: str, external_id: str, payload: dict, ingested_at: str = "1970-01-01T00:00:00+00:00") -> None:
        self.raw[(org_id, source, external_id)] = {"org_id": org_id, "source": source, "external_id": external_id,
                                                   "payload": copy.deepcopy(payload), "ingested_at": ingested_at}

    def find_order(self, org_id, channel, external_id):
        o = self.orders.get((org_id, channel, external_id))
        return copy.deepcopy(o) if o else None

    def upsert_order(self, org_id, channel, external_id, fields):
        key = (org_id, channel, external_id)
        unknown = set(fields) - set(ORDER_FIELDS)
        if unknown:
            raise LedgerError(f"unknown order fields: {sorted(unknown)}")
        cur = self.orders.get(key)
        if cur is None:
            missing = [f for f in ORDER_REQUIRED if fields.get(f) is None]
            if missing:
                raise LedgerError(f"cannot create order {key}: missing {missing}")
            cur = {"id": str(uuid.uuid4()), "org_id": org_id, "channel": channel, "external_id": external_id,
                   "status": "placed", "discounts_paise": 0, "taxes_paise": 0, "shipping_charged_paise": 0,
                   "refunds_paise": 0, "customer_key": None, "city": None, "city_tier": "unknown", "pincode": None}
            self.orders[key] = cur
        for k, v in fields.items():
            if v is None:
                continue
            cur[k] = merge_status(cur.get("status"), v) if k == "status" else v
        return cur["id"]

    def upsert_items(self, org_id, order_id, items):
        for it in items:
            self.items[(order_id, it["sku"])] = {"org_id": org_id, "order_id": order_id, **it}

    def upsert_fee(self, org_id, order_id, fee_type, amount_paise, source):
        if source not in FEE_PRECEDENCE:
            raise LedgerError(f"unknown fee source {source!r}")
        rank = FEE_PRECEDENCE[source]
        others = [k for k in self.fees if k[0] == order_id and k[1] == fee_type and k[2] != source]
        if any(FEE_PRECEDENCE[k[2]] > rank for k in others):
            return "skipped_lower_precedence"
        for k in others:  # strictly lower precedence rows are superseded
            del self.fees[k]
        key = (order_id, fee_type, source)
        action = "updated" if key in self.fees else "inserted"
        self.fees[key] = {"org_id": org_id, "order_id": order_id, "type": fee_type, "amount_paise": amount_paise, "source": source}
        return action

    def upsert_return(self, org_id, order_id, rtype, cost_paise, returned_at):
        cur = self.returns.setdefault((order_id, rtype), {"org_id": org_id, "order_id": order_id, "type": rtype,
                                                          "cost_paise": 0, "returned_at": None})
        if cost_paise is not None:
            cur["cost_paise"] = cost_paise
        if returned_at is not None:
            cur["returned_at"] = returned_at

    def upsert_settlement_item(self, org_id, order_external_id, item, raw_ref):
        hkey = (org_id, item["channel"], item["settlement_ref"])
        order = self.orders.get((org_id, item["order_channel"], order_external_id))
        self.settlement_items[hkey + (order_external_id, item["line_type"])] = {
            "org_id": org_id, "settlement_channel": item["channel"], "settlement_ref": item["settlement_ref"],
            "order_external_id": order_external_id, "order_channel": item["order_channel"],
            "order_id": order["id"] if order else None, "line_type": item["line_type"], "gross_paise": item["gross_paise"],
            "fees_paise": item["fees_paise"], "net_paise": item["net_paise"], "amount_paise": item["net_paise"],
            "settled_at": item.get("settled_at"), "source": item["source"], "raw_ref": dict(raw_ref)}
        mine = [i for k, i in self.settlement_items.items() if k[:3] == hkey]
        h = self.settlements.setdefault(hkey, {"org_id": org_id, "channel": item["channel"], "external_id": item["settlement_ref"]})
        h.update(gross_paise=sum(i["gross_paise"] for i in mine), fees_paise=sum(i["fees_paise"] for i in mine),
                 net_paise=sum(i["net_paise"] for i in mine), source=item["source"],
                 settled_at=item.get("settled_at") or h.get("settled_at"))

    def upsert_shipment(self, org_id, shipment, order_id, raw_ref):
        key = (org_id, shipment.get("source", "shiprocket"), shipment["external_id"])
        cur = self.shipments.get(key)
        row = {"org_id": org_id, "source": key[1], "order_id": order_id, "raw_ref": dict(raw_ref), **shipment}
        if cur:
            for k, v in row.items():
                if v is None or k == "status":
                    row[k] = cur.get(k) if v is None else v
            if SHIPMENT_RANK[cur["status"]] > SHIPMENT_RANK[row["status"]]:
                row["status"] = cur["status"]
            if row["order_id"] is None:
                row["order_id"] = cur.get("order_id")
        self.shipments[key] = row

    def iter_raw_records(self, org_id, sources=None, since=None):
        rows = [r for r in self.raw.values() if r["org_id"] == org_id and (not sources or r["source"] in sources)
                and (since is None or r["ingested_at"] >= since)]
        return sorted(copy.deepcopy(rows), key=lambda r: (r["source"], r["external_id"]))

    def load_city_tiers(self):
        return dict(self.city_tiers)

    def fetch_snapshot(self, org_id, start, end):
        orders = []
        for o in self.orders.values():
            if o["org_id"] != org_id:
                continue
            d = ist_date(datetime.fromisoformat(o["ordered_at"]))
            if start <= d <= end:
                orders.append({**o, "ordered_date_ist": d})
        ids = {o["id"] for o in orders}
        settlements = []
        for s in self.settlement_items.values():
            if s["org_id"] != org_id:
                continue
            if s["order_id"] in ids:
                settlements.append(dict(s))
            elif s["order_id"] is None and s.get("settled_at") and start <= ist_date(datetime.fromisoformat(s["settled_at"])) <= end:
                settlements.append(dict(s))
        return {"orders": orders, "fees": [dict(f) for f in self.fees.values() if f["order_id"] in ids],
                "returns": [dict(r) for r in self.returns.values() if r["order_id"] in ids],
                "order_items": [dict(i) for i in self.items.values() if i["order_id"] in ids],
                "settlements": settlements}
