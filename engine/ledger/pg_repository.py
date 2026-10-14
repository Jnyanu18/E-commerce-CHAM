"""Postgres implementation of ``Repository`` (psycopg 3).

NOT EXECUTED AGAINST A REAL DATABASE by the ledger agent (no Postgres available in this environment); covered
only by a recording-fake-connection test that checks the SQL shape and parameters. Verify with a real DB.

Dependencies on schema that does not exist yet (see docs/SCHEMA_CHANGE_REQUESTS.md):
* ``settlements`` table (database agent) with the columns used in ``upsert_settlement``.
* ``orders.city_tier`` accepting ``tier_1..tier_3, unknown`` (database agent's new migration). Until it lands,
  writing those values violates the 0002 check constraint.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from .repository import FEE_PRECEDENCE, ORDER_FIELDS, ORDER_REQUIRED, LedgerError, Repository

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
        if self.find_order(org_id, channel, external_id) is None:
            missing = [k for k in ORDER_REQUIRED if k not in f]
            if missing:
                raise LedgerError(f"cannot create order {channel}:{external_id}: missing {missing}")
        cols = ["org_id", "channel", "external_id", *f]
        vals = (org_id, channel, external_id, *f.values())
        sets = []
        for k in f:
            if k == "status":
                sets.append(f"status = case when {_RANK_SQL.format(col='excluded.status')} >= {_RANK_SQL.format(col='orders.status')} "
                            "then excluded.status else orders.status end")
            else:
                sets.append(f"{k} = excluded.{k}")
        sets.append("updated_at = now()")
        sql = (f"insert into orders ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
               f"on conflict (org_id, channel, external_id) do update set {', '.join(sets)} returning id")
        return str(self._q(sql, vals)[0]["id"])

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

    def upsert_settlement(self, org_id, channel, order_id, order_external_id, settlement, raw_ref):
        # ASSUMED table: settlements(org_id, channel, settlement_ref, order_id null, order_external_id, source,
        # amount_paise, settled_at, raw_source, raw_external_id, unique(org_id, channel, settlement_ref, order_external_id))
        self._q("insert into settlements (org_id, channel, settlement_ref, order_id, order_external_id, source, amount_paise, "
                "settled_at, raw_source, raw_external_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "on conflict (org_id, channel, settlement_ref, order_external_id) do update set "
                "order_id=coalesce(excluded.order_id, settlements.order_id), source=excluded.source, "
                "amount_paise=excluded.amount_paise, settled_at=excluded.settled_at",
                (org_id, channel, settlement["settlement_ref"], order_id, order_external_id, settlement["source"],
                 settlement["amount_paise"], settlement.get("settled_at"), raw_ref.get("source"), raw_ref.get("external_id")))

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
        settlements = self._q("select * from settlements where org_id=%s and (order_id = any(%s) or (order_id is null "
                              "and (settled_at at time zone 'Asia/Kolkata')::date between %s and %s))", (org_id, ids, start, end))
        return {"orders": orders, "fees": by("fees"), "returns": by("returns"), "order_items": by("order_items"),
                "settlements": settlements}
