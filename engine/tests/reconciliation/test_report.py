"""Reconciliation tests over the pipeline output (SYNTHETIC fixtures, hand-calculated expectations)."""
from datetime import date

from ledger.pipeline import run_transform
from ledger.repository import InMemoryRepository
from reconciliation.report import reconcile_snapshot
from tests.ledger import fixtures as fx
from tests.ledger.test_pipeline import full_repo

ORG = fx.ORG
START, END = date(2026, 8, 1), date(2026, 9, 30)
AS_OF = date(2026, 10, 14)


def report(repo, as_of=AS_OF, **kw):
    run_transform(repo, ORG)
    return reconcile_snapshot(ORG, START, END, repo.fetch_snapshot(ORG, START, END), as_of=as_of, **kw)


def find(rep, channel, code, ext=None):
    ch = next(c for c in rep["channels"] if c["channel"] == channel)
    return [d for d in ch["discrepancies"] if d["code"] == code and (ext is None or d["order_external_id"] == ext)]


def test_matching_settlements_produce_no_amount_discrepancy():
    rep = report(full_repo())
    # AMZ-1: paid 1500.00 - commission 225 - fixed 20 = 1255.00 == settlement  -> no mismatch
    assert find(rep, "amazon", "settlement_amount_mismatch", "AMZ-1") == []
    # Shopify COD 5002: paid 1199 - COD charge 25 = 1174 == remittance
    assert find(rep, "shopify", "settlement_amount_mismatch", "5002") == []


def test_settlement_mismatch_is_reported_not_adjusted():
    rep = report(full_repo())
    [d] = find(rep, "amazon", "settlement_amount_mismatch", "AMZ-2")
    assert (d["expected_paise"], d["actual_paise"], d["diff_paise"]) == (68000, 67500, -500)
    assert d["severity"] == "error" and d["detail"]["basis"] == "actual_fees"
    amz = next(c for c in rep["channels"] if c["channel"] == "amazon")
    assert amz["status"] == "discrepancies"
    assert amz["totals"]["unexplained_abs_paise"] == 500 and amz["totals"]["unexplained_net_paise"] == -500


def test_tolerance_suppresses_small_diff_only_when_asked():
    rep = report(full_repo(), tolerance_paise=500)
    assert find(rep, "amazon", "settlement_amount_mismatch") == []
    assert rep["tolerance_paise"] == 500


def test_unsettled_old_delivered_order_flagged_missing_but_recent_one_is_not():
    rep = report(full_repo())
    [d] = find(rep, "flipkart", "settlement_missing", "FK-1")
    assert d["expected_paise"] == 38900 and d["actual_paise"] == 0
    # same order but ordered inside the grace window
    repo = InMemoryRepository()
    repo.add_raw_record(ORG, "csv_flipkart", "r", fx.mkt_row("FK-2", "A", "100.00", date="2026-09-25T10:00:00"))
    assert find(report(repo, as_of=date(2026, 10, 1)), "flipkart", "settlement_missing") == []
    assert find(report(repo), "flipkart", "settlement_missing")  # same order, later as_of: now overdue


def test_prepaid_shopify_without_settlement_is_not_a_discrepancy():
    rep = report(full_repo())
    assert find(rep, "shopify", "settlement_missing", "5001") == []
    assert find(rep, "shopify", "settlement_missing", "5004") == []


def test_returns_and_refund_checks():
    rep = report(full_repo())
    assert find(rep, "shopify", "return_without_refund", "5003") == []        # RTO, not a customer return
    assert find(rep, "shopify", "refund_without_return_record", "5005")       # goodwill refund
    assert find(rep, "shopify", "refund_without_return_record", "5004") == []  # has customer_return row
    assert find(rep, "shopify", "rto_prepaid_not_refunded", "5003") == []      # RTO order is COD


def test_prepaid_rto_not_refunded_and_return_without_refund():
    repo = InMemoryRepository()
    p = dict(fx.SHOPIFY_NORMAL)
    repo.add_raw_record(ORG, "shopify_order", "5001", p)
    repo.add_raw_record(ORG, "shiprocket_shipment", "1", {"id": 1, "channel_order_id": "5001", "status": "RTO DELIVERED", "rto_charges": "50"})
    repo.add_raw_record(ORG, "shopify_order", "5004", fx.SHOPIFY_RETURN | {"refunds": []})
    repo.add_raw_record(ORG, "shiprocket_shipment", "2", fx.SHIPMENT_RETURN)
    rep = report(repo)
    assert find(rep, "shopify", "rto_prepaid_not_refunded", "5001")[0]["expected_paise"] == 194600
    assert find(rep, "shopify", "return_without_refund", "5004")


def test_cancelled_and_rto_orders_with_money_in_settlements():
    repo = InMemoryRepository()
    rows = [fx.mkt_row("C1", "A", "100.00", status="cancelled", settlement_ref="S1", settlement_amount="90.00"),
            fx.mkt_row("C2", "A", "100.00", payment_method="cod", status="rto", settlement_ref="S2", settlement_amount="10.00")]
    for i, r in enumerate(rows):
        repo.add_raw_record(ORG, "csv_amazon", str(i), r)
    rep = report(repo)
    assert find(rep, "amazon", "settlement_for_cancelled_order", "C1")[0]["diff_paise"] == 9000
    assert find(rep, "amazon", "settlement_for_rto_order", "C2")[0]["diff_paise"] == 1000


def test_orphan_settlement_surfaced():
    repo = full_repo()
    run_transform(repo, ORG)
    repo.upsert_settlement(ORG, "amazon", None, "GHOST", {"settlement_ref": "S-9", "source": "csv_amazon", "amount_paise": 4200,
                                                           "settled_at": "2026-09-25T00:00:00+00:00"}, {})
    rep = reconcile_snapshot(ORG, START, END, repo.fetch_snapshot(ORG, START, END), as_of=AS_OF)
    [d] = find(rep, "amazon", "settlement_without_order", "GHOST")
    assert d["actual_paise"] == 4200


def test_duplicate_settlement_and_missing_sku_and_estimated_only_fees():
    repo = InMemoryRepository()
    r = fx.mkt_row("D1", "", "100.00", commission_fee="10.00")
    repo.add_raw_record(ORG, "csv_amazon", "0", r | {"settlement_ref": "A", "settlement_amount": "90.00"})
    run_transform(repo, ORG)
    oid = repo.orders[(ORG, "amazon", "D1")]["id"]
    repo.upsert_settlement(ORG, "amazon", oid, "D1", {"settlement_ref": "B", "source": "csv_amazon", "amount_paise": 9000, "settled_at": None}, {})
    rep = reconcile_snapshot(ORG, START, END, repo.fetch_snapshot(ORG, START, END), as_of=AS_OF)
    assert find(rep, "amazon", "possible_duplicate_settlement", "D1")
    assert find(rep, "amazon", "missing_sku", "D1")
    # estimated-only: drop the actual fee, keep an estimate
    repo.fees.clear()
    repo.upsert_fee(ORG, oid, "commission", 1000, "estimated")
    rep = reconcile_snapshot(ORG, START, END, repo.fetch_snapshot(ORG, START, END), as_of=AS_OF)
    assert find(rep, "amazon", "no_actual_fees", "D1")[0]["detail"]["estimated_only"] is True


def test_report_shape_empty_period_and_determinism():
    repo = InMemoryRepository()
    rep = reconcile_snapshot(ORG, START, END, repo.fetch_snapshot(ORG, START, END), as_of=AS_OF)
    assert rep["channels"] == [] and rep["period"] == {"start": "2026-08-01", "end": "2026-09-30"}
    full = full_repo()
    a, b = report(full), report(full)
    assert a == b
    assert set(a) >= {"org_id", "period", "as_of", "tolerance_paise", "assumptions", "channels"}
    # period filter: only September-or-later orders
    narrow = reconcile_snapshot(ORG, date(2026, 9, 11), date(2026, 9, 11), full.fetch_snapshot(ORG, date(2026, 9, 11), date(2026, 9, 11)), as_of=AS_OF)
    assert sum(c["totals"]["orders"] for c in narrow["channels"]) >= 1
    assert all(c["channel"] != "flipkart" for c in narrow["channels"])


def test_refund_exceeding_paid_is_error():
    repo = InMemoryRepository()
    repo.add_raw_record(ORG, "csv_amazon", "0", fx.mkt_row("R1", "A", "100.00", refund_amount="150.00"))
    assert find(report(repo), "amazon", "refund_exceeds_paid", "R1")[0]["severity"] == "error"
