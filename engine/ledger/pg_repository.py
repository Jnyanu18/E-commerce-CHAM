"""Postgres implementation of ``Repository`` (psycopg 3).

NOT EXECUTED AGAINST A REAL DATABASE by the ledger agent (no Postgres available in this environment); covered
only by a recording-fake-connection test that checks the SQL shape and parameters. Verify with a real DB.

Schema: migrations 0001-0011 (settlements / settlement_items / shipments from 0007). Orders may omit city_tier
(DB default 'unknown'); we pass it explicitly anyway.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from .repository import FEE_PRECEDENCE, ORDER_FIELDS, ORDER_REQUIRED, LedgerError, Repository, merge_status

_RANK_SQL = "case {col} when 'placed' then 0 when 'shipped' then 1 when 'delivered' then 2 when 'cancelled' then 3 when 'returned' then 4 when 'rto' then 5 end"


class PsycopgRepository(Repository):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def find_order(self, org_id, channel, external_id):
        rows = self._q("select * from orders where org_id=%s and channel=%s and external_id=%s", (org_id, channel, external_id))
        return rows[0] if rows else None

    def upsert_order(self, org_id, channel, external_id, fields):
        unknown = set(fields) - set(ORDER_FIELDS)
        if unknown:
            raise LedgerError(f"unknown order fields: {sorted(unknown)}")
        f = {k: v for k, v in fields.items() if v is not None}
        existing = self.find_order(org_id, channel, external_id)
        if existing is None:
            missing = [k for k in ORDER_REQUIRED if k not in f]
            if missing:
                raise LedgerError(f"cannot create order {channel}:{external_id}: missing {missing}")
        elif f:
            # A partial patch must be an UPDATE: INSERT .. ON CONFLICT checks NOT NULL on the proposed row first.
            if "status" in f:  # same precedence merge as the insert path, done here to keep one bind per column
                f["status"] = merge_status(existing["status"], f["status"])
            sets = [f"{k} = %s" for k in f] + ["updated_at = now()"]
            self._q(f"update orders set {', '.join(sets)} where id = %s", (*f.values(), existing["id"]))
            return str(existing["id"])
        else:
            return str(existing["id"])
        cols = ["org_id", "channel", "external_id", *f]
        vals = (org_id, channel, external_id, *f.values())
        sets = [self._set_clause(k, excluded=True) for k in f]
        sets.append("updated_at = now()")
        sql = (f"insert into orders ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
               f"on conflict (org_id, channel, external_id) do update set {', '.join(sets)} returning id")
        return str(self._q(sql, vals)[0]["id"])

    @staticmethod
    def _set_clause(k: str, excluded: bool = True) -> str:
        if k == "status":
            return (f"status = case when {_RANK_SQL.format(col='excluded.status')} >= {_RANK_SQL.format(col='orders.status')} "
                    "then excluded.status else orders.status end")
        return f"{k} = excluded.{k}"

    def upsert_items(self, org_id, order_id, items):
        for it in items:
            self._q("insert into order_items (org_id, order_id, sku, qty, unit_price_paise) values (%s,%s,%s,%s,%s) "
                    "on conflict (order_id, sku) do update set qty=excluded.qty, unit_price_paise=excluded.unit_price_paise",
                    (org_id, order_id, it["sku"], it["qty"], it["unit_price_paise"]))

    def upsert_fee(self, org_id, order_id, fee_type, amount_paise, source):
        if source not in FEE_PRECEDENCE:
            raise LedgerError(f"unknown fee source {source!r}")
        rank = FEE_PRECEDENCE[source]
        existing = [r for r in self._q("select source from fees where order_id=%s and type=%s", (order_id, fee_type))]
        others = [r["source"] for r in existing if r["source"] != source]
        if any(FEE_PRECEDENCE.get(s, 0) > rank for s in others):
            return "skipped_lower_precedence"
        for s in others:
            self._q("delete from fees where order_id=%s and type=%s and source=%s", (order_id, fee_type, s))
        action = "updated" if any(r["source"] == source for r in existing) else "inserted"
        self._q("insert into fees (org_id, order_id, type, amount_paise, source) values (%s,%s,%s,%s,%s) "
                "on conflict (order_id, type, source) do update set amount_paise=excluded.amount_paise",
                (org_id, order_id, fee_type, amount_paise, source))
        return action

    def upsert_return(self, org_id, order_id, rtype, cost_paise, returned_at):
        self._q("insert into returns (org_id, order_id, type, cost_paise, returned_at) values (%s,%s,%s,coalesce(%s,0),%s) "
                "on conflict (order_id, type) do update set "
                "cost_paise = coalesce(%s, returns.cost_paise), returned_at = coalesce(excluded.returned_at, returns.returned_at)",
                (org_id, order_id, rtype, cost_paise, returned_at, cost_paise))

    def upsert_settlement_item(self, org_id, order_external_id, item, raw_ref):
        day = "coalesce((%s::timestamptz at time zone 'Asia/Kolkata')::date, current_date)"
        sid = self._q(
            "insert into settlements (org_id, channel, external_id, period_start, period_end, settled_at, gross_paise, "
            "fees_paise, net_paise, source, raw_record_id) values (%s,%s,%s," + day + "," + day + ",%s,0,0,0,%s,"
            "(select id from raw_records where org_id=%s and source=%s and external_id=%s)) "
            "on conflict (org_id, channel, external_id) do update set "
            "period_start = least(settlements.period_start, excluded.period_start), "
            "period_end = greatest(settlements.period_end, excluded.period_end), "
            "settled_at = coalesce(excluded.settled_at, settlements.settled_at), source = excluded.source, "
            "raw_record_id = coalesce(excluded.raw_record_id, settlements.raw_record_id), updated_at = now() returning id",
            (org_id, item["channel"], item["settlement_ref"], item.get("settled_at"), item.get("settled_at"),
             item.get("settled_at"), item["source"], org_id, raw_ref.get("source"), raw_ref.get("external_id")))[0]["id"]
        self._q("insert into settlement_items (org_id, settlement_id, line_type, order_external_id, order_id, gross_paise, "
                "fees_paise, net_paise) values (%s,%s,%s,%s,(select id from orders where org_id=%s and channel=%s and external_id=%s),%s,%s,%s) "
                "on conflict (settlement_id, order_external_id, line_type) do update set "
                "order_id = coalesce(excluded.order_id, settlement_items.order_id), gross_paise=excluded.gross_paise, "
                "fees_paise=excluded.fees_paise, net_paise=excluded.net_paise",
                (org_id, sid, item["line_type"], order_external_id, org_id, item["order_channel"], order_external_id,
                 item["gross_paise"], item["fees_paise"], item["net_paise"]))
        self._q("update settlements s set gross_paise=t.g, fees_paise=t.f, net_paise=t.n from "
                "(select coalesce(sum(gross_paise),0) g, coalesce(sum(fees_paise),0) f, coalesce(sum(net_paise),0) n "
                "from settlement_items where settlement_id=%s) t where s.id=%s", (sid, sid))

    def upsert_shipment(self, org_id, shipment, order_id, raw_ref):
        rank = ("case {c} when 'unknown' then 0 when 'created' then 1 when 'in_transit' then 2 when 'out_for_delivery' then 3 "
                "when 'delivered' then 4 when 'cancelled' then 4 when 'lost' then 4 when 'rto_initiated' then 5 "
                "when 'rto_delivered' then 6 end")
        self._q("insert into shipments (org_id, source, external_id, order_id, order_external_id, carrier, awb, status, "
                "delivered_at, rto_at, freight_cost_paise, cod_amount_paise, raw_record_id) values "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,(select id from raw_records where org_id=%s and source=%s and external_id=%s)) "
                "on conflict (org_id, source, external_id) do update set "
                "order_id = coalesce(excluded.order_id, shipments.order_id), "
                "order_external_id = coalesce(excluded.order_external_id, shipments.order_external_id), "
                "carrier = coalesce(excluded.carrier, shipments.carrier), awb = coalesce(excluded.awb, shipments.awb), "
                "status = case when " + rank.format(c="excluded.status") + " >= " + rank.format(c="shipments.status")
                + " then excluded.status else shipments.status end, "
                "delivered_at = coalesce(excluded.delivered_at, shipments.delivered_at), "
                "rto_at = coalesce(excluded.rto_at, shipments.rto_at), freight_cost_paise = excluded.freight_cost_paise, "
                "cod_amount_paise = excluded.cod_amount_paise, "
                "raw_record_id = coalesce(excluded.raw_record_id, shipments.raw_record_id), updated_at = now()",
                (org_id, shipment.get("source", "shiprocket"), shipment["external_id"], order_id,
                 shipment.get("order_external_id"), shipment.get("carrier"), shipment.get("awb"), shipment["status"],
                 shipment.get("delivered_at"), shipment.get("rto_at"), shipment["freight_cost_paise"],
                 shipment["cod_amount_paise"], org_id, raw_ref.get("source"), raw_ref.get("external_id")))

    def iter_raw_records(self, org_id, sources=None, since=None):
        sql, params = "select source, external_id, payload, ingested_at from raw_records where org_id=%s", [org_id]
        if sources:
            sql += " and source = any(%s)"
            params.append(list(sources))
        if since:
            sql += " and ingested_at >= %s"
            params.append(since)
        rows = self._q(sql + " order by source, external_id", tuple(params))
        for r in rows:
            if isinstance(r["payload"], str):
                r["payload"] = json.loads(r["payload"])
        return rows

    def load_city_tiers(self):
        try:
            return {r["city"].strip().lower(): r["tier"] for r in self._q("select city, tier from city_tiers")}
        except Exception:  # table empty/absent must not break the pipeline: tiers become 'unknown'
            return {}

    def fetch_snapshot(self, org_id, start: date, end: date):
        orders = self._q("select * from orders where org_id=%s and ordered_date_ist between %s and %s", (org_id, start, end))
        ids = [o["id"] for o in orders]
        by = lambda t: self._q(f"select * from {t} where org_id=%s and order_id = any(%s)", (org_id, ids)) if ids else []  # noqa: E731
        settlements = self._q(
            "select i.order_external_id, i.order_id, i.line_type, i.gross_paise, i.fees_paise, i.net_paise, "
            "i.net_paise as amount_paise, s.external_id as settlement_ref, s.channel as settlement_channel, s.source, s.settled_at "
            "from settlement_items i join settlements s on s.id = i.settlement_id where i.org_id=%s and "
            "(i.order_id = any(%s) or (i.order_id is null and s.period_end between %s and %s))", (org_id, ids, start, end))
        return {"orders": orders, "fees": by("fees"), "returns": by("returns"), "order_items": by("order_items"),
                "settlements": settlements}
