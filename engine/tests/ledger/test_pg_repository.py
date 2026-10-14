"""PsycopgRepository checked against a RECORDING FAKE connection only (no real Postgres here)."""
import pytest

from ledger.pg_repository import PsycopgRepository
from ledger.repository import LedgerError
from transforms.common import make_settlement_item


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.conn.calls.append((" ".join(sql.split()), params))
        q = sql.lower()
        self.rows = []
        if q.startswith("select source from fees"):
            self.rows = [{"source": s} for s in self.conn.fee_sources]
        elif q.startswith("select * from orders"):
            self.rows = self.conn.order_rows
        elif "returning id" in q:
            self.rows = [{"id": "oid-1"}]
        self.description = [1] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, fee_sources=(), order_rows=()):
        self.calls, self.fee_sources, self.order_rows = [], list(fee_sources), list(order_rows)

    def cursor(self, row_factory=None):
        return FakeCursor(self)


FULL = {"ordered_at": "2026-09-01T00:00:00+00:00", "gross_revenue_paise": 100, "payment_method": "cod", "status": "delivered"}


def test_upsert_order_sql_is_keyed_and_status_is_rank_merged():
    c = FakeConn()
    oid = PsycopgRepository(c).upsert_order("org", "shopify", "1", FULL | {"city": None})
    assert oid == "oid-1"
    sql, params = c.calls[-1]
    assert "on conflict (org_id, channel, external_id) do update" in sql
    assert "city" not in sql                                    # None fields are never written
    assert "case excluded.status" in sql and "else orders.status end" in sql
    assert params[:3] == ("org", "shopify", "1")


def test_upsert_order_refuses_incomplete_create_and_unknown_fields():
    with pytest.raises(LedgerError):
        PsycopgRepository(FakeConn()).upsert_order("o", "shopify", "1", {"status": "rto"})
    with pytest.raises(LedgerError):
        PsycopgRepository(FakeConn()).upsert_order("o", "shopify", "1", {"nope": 1})
    # partial patch on an existing order is fine
    PsycopgRepository(FakeConn(order_rows=[{"id": "x", "status": "placed"}])).upsert_order("o", "shopify", "1", {"status": "rto"})


def test_fee_precedence_skip_and_supersede():
    c = FakeConn(fee_sources=["api"])
    assert PsycopgRepository(c).upsert_fee("o", "ord", "commission", 5, "estimated") == "skipped_lower_precedence"
    assert not any(s.startswith("insert into fees") for s, _ in c.calls)
    c = FakeConn(fee_sources=["estimated"])
    assert PsycopgRepository(c).upsert_fee("o", "ord", "commission", 5, "csv") == "inserted"
    assert any(s.startswith("delete from fees") for s, _ in c.calls)
    assert any("on conflict (order_id, type, source)" in s for s, _ in c.calls)


def test_return_item_settlement_sql_keys():
    c = FakeConn()
    r = PsycopgRepository(c)
    r.upsert_return("o", "ord", "RTO", None, None)
    r.upsert_items("o", "ord", [{"sku": "A", "qty": 1, "unit_price_paise": 5}])
    item = make_settlement_item(channel="amazon", ref="S", source="csv", order_channel="amazon", settled_at=None,
                                net_paise=1, gross_paise=1, fees_paise=0, issues=[])
    r.upsert_settlement_item("o", "X", item, {"source": "csv_amazon", "external_id": "1"})
    r.upsert_shipment("o", {"external_id": "9", "status": "delivered", "freight_cost_paise": 1, "cod_amount_paise": 0}, None, {})
    sqls = " | ".join(s for s, _ in c.calls)
    assert "on conflict (org_id, channel, external_id)" in sqls and "on conflict (org_id, source, external_id)" in sqls
    assert "on conflict (settlement_id, order_external_id, line_type)" in sqls
    assert "on conflict (order_id, type)" in sqls and "on conflict (order_id, sku)" in sqls
