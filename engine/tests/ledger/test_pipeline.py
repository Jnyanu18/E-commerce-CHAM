"""End-to-end ledger pipeline over the in-memory repository (SYNTHETIC fixtures)."""
import copy
import itertools

import pytest

from ledger.jobs import make_handler, run_transform_job
from ledger.pipeline import run_transform
from ledger.repository import InMemoryRepository, LedgerError, merge_status
from tests.ledger import fixtures as fx

ORG = fx.ORG


def seeded(*pairs):
    repo = InMemoryRepository()
    for source, ext, payload in pairs:
        repo.add_raw_record(ORG, source, ext, payload)
    return repo


def state(repo):
    """Comparable snapshot minus generated uuids."""
    ids = {o["id"]: k for k, o in repo.orders.items()}
    norm = lambda d: {k: v for k, v in d.items() if k not in ("id",)}  # noqa: E731
    return {
        "orders": {k: norm(o) for k, o in repo.orders.items()},
        "items": {(ids[k[0]], k[1]): {a: b for a, b in v.items() if a != "order_id"} for k, v in repo.items.items()},
        "fees": {(ids[k[0]],) + k[1:]: {a: b for a, b in v.items() if a != "order_id"} for k, v in repo.fees.items()},
        "returns": {(ids[k[0]], k[1]): {a: b for a, b in v.items() if a != "order_id"} for k, v in repo.returns.items()},
        "settlements": {k: {a: b for a, b in v.items() if a != "order_id"} for k, v in repo.settlements.items()},
    }


def full_repo():
    repo = InMemoryRepository()
    for o in (fx.SHOPIFY_NORMAL, fx.SHOPIFY_COD, fx.SHOPIFY_RTO, fx.SHOPIFY_RETURN, fx.SHOPIFY_REFUND,
              fx.SHOPIFY_MISSING_SKU, fx.SHOPIFY_TAX_EXCLUSIVE, fx.SHOPIFY_CANCELLED):
        repo.add_raw_record(ORG, "shopify_order", str(o["id"]), o)
    for s in (fx.SHIPMENT_DELIVERED, fx.SHIPMENT_RTO, fx.SHIPMENT_RETURN, fx.SHIPMENT_FORWARD_1004):
        repo.add_raw_record(ORG, "shiprocket_shipment", str(s["id"]), s)
    for i, r in enumerate(fx.AMAZON_OK + fx.AMAZON_MISMATCH):
        repo.add_raw_record(ORG, "csv_amazon", f"row{i}", r)
    repo.add_raw_record(ORG, "csv_flipkart", "f0", fx.FLIPKART_UNSETTLED[0])
    repo.add_raw_record(ORG, "csv_blinkit", "b0", fx.BLINKIT_CANCELLED[0])
    return repo


def order(repo, channel, ext):
    return repo.orders[(ORG, channel, ext)]


def test_full_flow_canonical_rows():
    repo = full_repo()
    s = run_transform(repo, ORG)
    assert s.errors == [] and s.deferred == []
    assert len(repo.orders) == 12
    normal = order(repo, "shopify", "5001")
    assert normal["status"] == "shipped" and normal["payment_method"] == "prepaid"
    assert order(repo, "shopify", "5002")["status"] == "delivered"          # from shipment 9001
    rto = order(repo, "shopify", "5003")
    assert rto["status"] == "rto" and rto["payment_method"] == "cod"
    assert repo.returns[(rto["id"], "RTO")]["cost_paise"] == 7000
    ret = order(repo, "shopify", "5004")
    assert ret["status"] == "returned" and ret["refunds_paise"] == 30000    # partial refund
    assert repo.returns[(ret["id"], "customer_return")]["cost_paise"] == 8000
    assert repo.fees[(ret["id"], "shipping", "api")]["amount_paise"] == 5500
    cod = order(repo, "shopify", "5002")
    assert repo.fees[(cod["id"], "payment_gateway", "api")]["amount_paise"] == 2500
    assert (ORG, "shiprocket_cod", "REM-1") in repo.settlements
    h = repo.settlements[(ORG, "shiprocket_cod", "REM-1")]
    assert (h["gross_paise"], h["fees_paise"], h["net_paise"]) == (119900, 2500, 117400)   # net = gross - fees
    assert repo.settlement_items[(ORG, "shiprocket_cod", "REM-1", "5002", "sale")]["order_id"] == cod["id"]
    assert len(repo.shipments) == 4 and repo.shipments[(ORG, "shiprocket", "9002")]["status"] == "rto_delivered"
    assert order(repo, "shopify", "5008")["status"] == "cancelled"
    assert order(repo, "amazon", "AMZ-1")["gross_revenue_paise"] == 150000
    assert {k[1] for k in repo.items if k[0] == order(repo, "shopify", "5006")["id"]} == {"__MISSING_SKU__", "OIL-100"}
    assert any(i["issue"].startswith("missing_sku") for i in s.issues)


def test_idempotent_rerun_changes_nothing():
    repo = full_repo()
    run_transform(repo, ORG)
    before = state(repo)
    counts = {k: len(v) for k, v in before.items()}
    s2 = run_transform(repo, ORG)
    assert state(repo) == before
    assert {k: len(v) for k, v in state(repo).items()} == counts
    assert s2.errors == [] and s2.fees_inserted == 0


def test_duplicate_import_single_financial_record():
    """The same source record imported twice (raw upsert + re-transform) never yields two financial records."""
    repo = seeded(("shopify_order", "5001", fx.SHOPIFY_NORMAL), ("shopify_order", "5001", fx.SHOPIFY_NORMAL),
                  ("shiprocket_shipment", "9001", fx.SHIPMENT_DELIVERED))
    repo.add_raw_record(ORG, "shopify_order", "5002", fx.SHOPIFY_COD)
    for _ in range(3):
        run_transform(repo, ORG)
    assert len([k for k in repo.orders if k[2] == "5001"]) == 1
    assert len([k for k in repo.fees if k[1] == "shipping"]) == 1
    assert len(repo.settlements) == 1
    # even a DIFFERENT raw external_id carrying the same Shopify order resolves to the same order key
    repo.add_raw_record(ORG, "shopify_order", "dup-key", fx.SHOPIFY_NORMAL)
    run_transform(repo, ORG)
    assert len([k for k in repo.orders if k[2] == "5001"]) == 1 and len(repo.items) == 3


def test_out_of_order_updates_converge_in_every_arrival_order():
    """Shopify (placed/shipped), delivered shipment and RTO shipment arrive in all permutations: same end state."""
    deliver = {"id": 1, "channel_order_id": "5003", "status": "DELIVERED", "freight_charges": "70.00"}
    items = [("shopify_order", "5003", fx.SHOPIFY_RTO), ("shiprocket_shipment", "1", deliver),
             ("shiprocket_shipment", "9002", fx.SHIPMENT_RTO)]
    finals = set()
    for perm in itertools.permutations(items):
        repo = InMemoryRepository()
        for src, ext, p in perm:
            repo.add_raw_record(ORG, src, ext, p)
            run_transform(repo, ORG)  # incremental: each arrival processed on its own
        o = order(repo, "shopify", "5003")
        finals.add((o["status"], repo.returns[(o["id"], "RTO")]["cost_paise"]))
    assert finals == {("rto", 7000)}


def test_status_never_regresses():
    assert merge_status("delivered", "placed") == "delivered"
    assert merge_status("rto", "delivered") == "rto" and merge_status("rto", "cancelled") == "rto"
    assert merge_status(None, "placed") == "placed" and merge_status("placed", None) == "placed"
    repo = seeded(("shiprocket_shipment", "9001", fx.SHIPMENT_DELIVERED))
    s = run_transform(repo, ORG)
    assert len(s.deferred) == 1 and repo.orders == {}          # shipment before order: parked, nothing invented
    repo.add_raw_record(ORG, "shopify_order", "5002", fx.SHOPIFY_COD)
    run_transform(repo, ORG)
    assert order(repo, "shopify", "5002")["status"] == "delivered"
    repo.add_raw_record(ORG, "shopify_order", "5002", fx.SHOPIFY_COD)   # older 'shipped' snapshot re-imported
    run_transform(repo, ORG)
    assert order(repo, "shopify", "5002")["status"] == "delivered"


def test_estimated_fees_never_overwrite_or_coexist_with_actual():
    repo = seeded(("shopify_order", "5002", fx.SHOPIFY_COD), ("shiprocket_shipment", "9001", fx.SHIPMENT_DELIVERED))
    run_transform(repo, ORG, fee_rates={"shopify": {"payment_gateway": 200, "commission": 100}})
    oid = order(repo, "shopify", "5002")["id"]
    # actual COD charge (api) beat the estimated payment_gateway; commission has no actual so estimate stays
    assert (oid, "payment_gateway", "estimated") not in repo.fees
    assert repo.fees[(oid, "payment_gateway", "api")]["amount_paise"] == 2500
    assert repo.fees[(oid, "commission", "estimated")]["amount_paise"] == 1199
    run_transform(repo, ORG, fee_rates={"shopify": {"payment_gateway": 200, "commission": 100}})
    assert (oid, "payment_gateway", "estimated") not in repo.fees
    assert repo.fees[(oid, "payment_gateway", "api")]["amount_paise"] == 2500


def test_actual_fee_supersedes_estimate_and_precedence_api_over_csv():
    r = InMemoryRepository()
    oid = r.upsert_order(ORG, "amazon", "X", {"ordered_at": "2026-09-01T00:00:00+00:00", "gross_revenue_paise": 1, "payment_method": "prepaid"})
    assert r.upsert_fee(ORG, oid, "commission", 50, "estimated") == "inserted"
    assert r.upsert_fee(ORG, oid, "commission", 70, "csv") == "inserted"
    assert (oid, "commission", "estimated") not in r.fees
    assert r.upsert_fee(ORG, oid, "commission", 99, "estimated") == "skipped_lower_precedence"
    assert r.upsert_fee(ORG, oid, "commission", 75, "csv") == "updated"
    assert r.upsert_fee(ORG, oid, "commission", 80, "api") == "inserted" and (oid, "commission", "csv") not in r.fees
    assert r.upsert_fee(ORG, oid, "commission", 1, "csv") == "skipped_lower_precedence"
    with pytest.raises(LedgerError):
        r.upsert_fee(ORG, oid, "commission", 1, "magic")


def test_bad_records_are_collected_not_fatal_and_unknown_sources_reported():
    repo = seeded(("shopify_order", "5001", fx.SHOPIFY_NORMAL), ("shopify_order", "bad", {"id": "x"}),
                  ("shiprocket_shipment", "bad2", {"id": 3}), ("csv_amazon", "bad3", {"sku": "x"}),
                  ("tiktok_ads", "m1", {"spend": 1}))
    s = run_transform(repo, ORG)
    assert len(repo.orders) == 1 and len(s.errors) == 3
    assert s.skipped_sources == {"tiktok_ads": 1}


def test_ambiguous_and_unresolved_shipment_references():
    other = copy.deepcopy(fx.SHOPIFY_COD)
    other["id"], other["name"] = "gid://shopify/Order/7777", "#5001"   # name collides with id 5001
    repo = seeded(("shopify_order", "5001", fx.SHOPIFY_NORMAL), ("shopify_order", "7777", other),
                  ("shiprocket_shipment", "a", {"id": 1, "channel_order_id": "5001", "status": "DELIVERED"}),
                  ("shiprocket_shipment", "b", fx.SHIPMENT_ORPHAN))
    s = run_transform(repo, ORG)
    assert any("ambiguous" in e["error"] for e in s.errors)
    assert len(s.deferred) == 1 and s.deferred[0]["order_ref"] == "NOPE-1"
    assert order(repo, "shopify", "5001")["status"] == "shipped"       # ambiguous shipment did NOT guess


def test_tenant_isolation():
    repo = seeded(("shopify_order", "5001", fx.SHOPIFY_NORMAL))
    repo.add_raw_record(fx.ORG2, "shopify_order", "5001", fx.SHOPIFY_NORMAL)
    run_transform(repo, ORG)
    assert list(repo.orders) == [(ORG, "shopify", "5001")]


def test_customer_key_hashed_in_ledger():
    repo = seeded(("shopify_order", "5001", fx.SHOPIFY_NORMAL))
    run_transform(repo, ORG)
    o = order(repo, "shopify", "5001")
    assert len(o["customer_key"]) == 64 and "@" not in str(repo.orders)


def test_city_tier_uses_new_enum_values():
    repo = seeded(("shopify_order", "5001", fx.SHOPIFY_NORMAL), ("shopify_order", "5002", fx.SHOPIFY_COD))
    repo.city_tiers = {"mumbai": "metro", "jaipur": "tier2"}
    run_transform(repo, ORG)
    assert order(repo, "shopify", "5001")["city_tier"] == "metro"
    assert order(repo, "shopify", "5002")["city_tier"] == "tier_2"


def test_empty_org_and_since_filter():
    repo = InMemoryRepository()
    s = run_transform(repo, ORG)
    assert s.orders_applied == 0 and s.errors == []
    repo.add_raw_record(ORG, "shopify_order", "5001", fx.SHOPIFY_NORMAL, ingested_at="2026-01-01T00:00:00+00:00")
    assert run_transform(repo, ORG, since="2026-06-01T00:00:00+00:00").orders_applied == 0
    assert run_transform(repo, ORG, since="2025-06-01T00:00:00+00:00").orders_applied == 1


def test_repo_rejects_incomplete_new_order_and_unknown_fields():
    r = InMemoryRepository()
    with pytest.raises(LedgerError):
        r.upsert_order(ORG, "shopify", "1", {"status": "delivered"})
    with pytest.raises(LedgerError):
        r.upsert_order(ORG, "shopify", "1", {"bogus": 1})


def test_job_handler_signature_and_summary():
    repo = full_repo()
    handler = make_handler(lambda conn: repo)
    handler({"org_id": ORG, "payload": {}}, object())
    assert len(repo.orders) == 12
    out = run_transform_job({"org_id": ORG, "payload": {"sources": ["shopify_order"]}}, repo)
    assert out["errors"] == []
    with pytest.raises(ValueError):
        run_transform_job({"payload": {}}, repo)


def test_rto_orders_do_not_get_full_amount_in_refunds():
    """RTO status never implies refunds_paise; it stays whatever the source reported (metrics assumption)."""
    repo = seeded(("shopify_order", "5003", fx.SHOPIFY_RTO), ("shiprocket_shipment", "9002", fx.SHIPMENT_RTO))
    run_transform(repo, ORG)
    o = order(repo, "shopify", "5003")
    assert o["status"] == "rto" and o["refunds_paise"] == 0
