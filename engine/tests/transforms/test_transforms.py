"""Pure transform tests (no DB). Fixtures are SYNTHETIC (tests/ledger/fixtures.py)."""
import copy
import hashlib
from datetime import timezone

import pytest

from tests.ledger import fixtures as fx
from transforms.common import (MISSING_SKU, TransformError, city_tier, customer_key, merge_lines, normalise_pincode,
                               parse_ts, to_paise)
from transforms.fees import estimate_fees
from transforms.marketplace import map_order_status, transform_marketplace_rows
from transforms.shiprocket import map_status, transform_shiprocket_shipment
from transforms.shopify import transform_shopify_order

ORG = fx.ORG


# ---------------------------------------------------------------- common
def test_to_paise_handles_strings_floats_blank_and_garbage():
    assert to_paise("1,234.50") == 123450
    assert to_paise(0.1) == 10 and to_paise("0.29") == 29
    assert to_paise(None) == 0 and to_paise("") == 0 and to_paise(None, default=None) is None
    assert to_paise("-5.00") == -500
    with pytest.raises(TransformError):
        to_paise("abc")
    with pytest.raises(TransformError):
        to_paise(True)


def test_parse_ts_to_utc_and_naive_rules():
    assert parse_ts("2026-09-10T10:00:00+05:30").isoformat() == "2026-09-10T04:30:00+00:00"
    assert parse_ts("2026-09-10T10:00:00Z").tzinfo == timezone.utc
    assert parse_ts("2026-09-10T10:00:00").isoformat() == "2026-09-10T10:00:00+00:00"
    assert parse_ts(None) is None and parse_ts("") is None
    with pytest.raises(TransformError):
        parse_ts("10/09/2026 ten")


def test_customer_key_is_hash_never_raw_and_normalised():
    k = customer_key(" Asha.Rao@Example.com ", None)
    assert k == hashlib.sha256(b"asha.rao@example.com").hexdigest()
    assert "asha" not in k
    assert customer_key(None, "+91 98765-43210") == customer_key(None, "09876543210")
    assert customer_key(None, None) is None and customer_key("not-an-email", "123") is None


def test_pincode_and_city_tier():
    assert normalise_pincode("011001") == "011001" and normalise_pincode(110001) == "110001"
    assert normalise_pincode("1100") is None and normalise_pincode(None) is None
    assert city_tier("Mumbai", None) == "unknown"
    assert city_tier("Mumbai", {"mumbai": "metro"}) == "metro"
    assert city_tier("Pune", {"pune": "tier1"}) == "tier_1"      # legacy enum normalised
    assert city_tier("X", {"x": "bogus"}) == "unknown"


def test_merge_lines_sums_same_sku():
    issues = []
    out = merge_lines([{"sku": "A", "qty": 1, "unit_price_paise": 100}, {"sku": "A", "qty": 2, "unit_price_paise": 100}], issues)
    assert out == [{"sku": "A", "qty": 3, "unit_price_paise": 100}] and issues


# ---------------------------------------------------------------- shopify
def test_shopify_normal_multi_item_prepaid():
    b = transform_shopify_order(ORG, fx.SHOPIFY_NORMAL)
    o = b.order
    assert (b.channel, b.external_id) == ("shopify", "5001")
    assert o["gross_revenue_paise"] == 199700 and o["discounts_paise"] == 10000
    assert o["shipping_charged_paise"] == 4900 and o["taxes_paise"] == 29685
    assert o["payment_method"] == "prepaid" and o["status"] == "shipped"
    assert o["ordered_at"] == "2026-09-10T04:30:00+00:00"
    assert o["city_tier"] == "unknown" and o["pincode"] == "400001"
    assert {(i["sku"], i["qty"], i["unit_price_paise"]) for i in b.items} == {("SER-30", 1, 79900), ("MOI-50", 2, 59900)}
    assert b.fees == [] and b.raw_ref == {"source": "shopify_order", "external_id": "5001"}
    assert not [i for i in b.issues if i.startswith("total_mismatch")]
    assert "asha" not in str(o) and "98765" not in str(o)


def test_shopify_cod_detection():
    assert transform_shopify_order(ORG, fx.SHOPIFY_COD).order["payment_method"] == "cod"
    assert transform_shopify_order(ORG, fx.SHOPIFY_RTO).order["payment_method"] == "cod"


def test_shopify_gst_inclusive_rule():
    """Included: gross is the listed price; taxes_paise is contained in it; nothing is added."""
    o = transform_shopify_order(ORG, fx.SHOPIFY_COD).order
    assert o["gross_revenue_paise"] == 119900 and o["taxes_paise"] == 18289
    assert o["gross_revenue_paise"] - o["discounts_paise"] + o["shipping_charged_paise"] == 119900  # == total_price


def test_shopify_gst_exclusive_rule_uplifts_gross():
    """Excluded: tax is ADDED to gross so customer_paid == total_price; issue recorded."""
    b = transform_shopify_order(ORG, fx.SHOPIFY_TAX_EXCLUSIVE)
    assert b.order["gross_revenue_paise"] == 118000 and b.order["taxes_paise"] == 18000
    assert "tax_exclusive_prices_uplifted" in b.issues
    assert not [i for i in b.issues if i.startswith("total_mismatch")]


def test_shopify_gst_flag_absent_assumes_inclusive_and_says_so():
    p = copy.deepcopy(fx.SHOPIFY_COD)
    del p["taxes_included"]
    b = transform_shopify_order(ORG, p)
    assert b.order["gross_revenue_paise"] == 119900
    assert any(i.startswith("taxes_included_unknown") for i in b.issues)


def test_shopify_refunds_partial_full_and_failed_transactions_ignored():
    assert transform_shopify_order(ORG, fx.SHOPIFY_RETURN).order["refunds_paise"] == 30000
    b = transform_shopify_order(ORG, fx.SHOPIFY_REFUND)
    assert b.order["refunds_paise"] == 34900  # failed 999.00 transaction ignored


def test_shopify_missing_sku_is_surfaced_not_dropped():
    b = transform_shopify_order(ORG, fx.SHOPIFY_MISSING_SKU)
    assert MISSING_SKU in {i["sku"] for i in b.items}
    assert any(i.startswith("missing_sku") for i in b.issues)
    assert b.order["gross_revenue_paise"] == 44900


def test_shopify_cancelled_and_status_mapping():
    assert transform_shopify_order(ORG, fx.SHOPIFY_CANCELLED).order["status"] == "cancelled"
    p = copy.deepcopy(fx.SHOPIFY_NORMAL)
    p["fulfillment_status"] = None
    assert transform_shopify_order(ORG, p).order["status"] == "placed"


def test_shopify_invalid_and_boundary_inputs():
    with pytest.raises(TransformError):
        transform_shopify_order(ORG, {})
    with pytest.raises(TransformError):
        transform_shopify_order(ORG, {"id": 1})  # no created_at
    bad = copy.deepcopy(fx.SHOPIFY_NORMAL)
    bad["total_tax"] = "lots"
    with pytest.raises(TransformError):
        transform_shopify_order(ORG, bad)
    empty = copy.deepcopy(fx.SHOPIFY_NORMAL)
    empty["line_items"] = []
    b = transform_shopify_order(ORG, empty)
    assert b.items == [] and "no_line_items" in b.issues
    z = copy.deepcopy(fx.SHOPIFY_NORMAL)
    z["line_items"][0]["quantity"] = 0
    assert "zero_qty_line_skipped" in transform_shopify_order(ORG, z).issues


def test_shopify_total_mismatch_and_bad_pincode_are_flagged():
    p = copy.deepcopy(fx.SHOPIFY_NORMAL)
    p["total_price"] = "1000.00"
    p["shipping_address"]["zip"] = "40001"
    b = transform_shopify_order(ORG, p)
    assert any(i.startswith("total_mismatch") for i in b.issues)
    assert b.order["pincode"] is None and any(i.startswith("invalid_pincode") for i in b.issues)


def test_shopify_transform_is_pure():
    snap = copy.deepcopy(fx.SHOPIFY_NORMAL)
    a = transform_shopify_order(ORG, fx.SHOPIFY_NORMAL)
    assert fx.SHOPIFY_NORMAL == snap
    assert a.order == transform_shopify_order(ORG, fx.SHOPIFY_NORMAL).order


# ---------------------------------------------------------------- shiprocket
def test_shiprocket_status_mapping():
    assert map_status("RTO DELIVERED", False) == "rto" and map_status("RTO INITIATED", False) == "rto"
    assert map_status("Delivered", False) == "delivered" and map_status("UNDELIVERED", False) is None
    assert map_status("IN TRANSIT", False) == "shipped" and map_status("CANCELED", False) == "cancelled"
    assert map_status("DELIVERED", True) == "returned" and map_status("IN TRANSIT", True) is None
    assert map_status("", False) is None and map_status("WEIRD", False) is None


def test_shiprocket_delivered_cod_fees_and_remittance():
    b = transform_shiprocket_shipment(ORG, fx.SHIPMENT_DELIVERED)
    assert b.partial and b.channel_ref == "1002" and b.order == {"status": "delivered"}
    assert {(f["type"], f["amount_paise"], f["source"]) for f in b.fees} == {("shipping", 6000, "api"), ("payment_gateway", 2500, "api")}
    s = b.settlements[0]
    assert (s["channel"], s["net_paise"], s["fees_paise"], s["gross_paise"]) == ("shiprocket_cod", 117400, 2500, 119900)
    [sh] = b.shipments
    assert sh["status"] == "delivered" and sh["awb"] == "AWB1" and sh["freight_cost_paise"] == 6000 and sh["order_external_id"] == "1002"


def test_shiprocket_rto_cost_and_missing_cost_surfaced():
    b = transform_shiprocket_shipment(ORG, fx.SHIPMENT_RTO)
    assert b.channel_ref == "1003"  # '#' stripped
    assert b.returns == [{"type": "RTO", "cost_paise": 7000, "returned_at": "2026-09-20T04:30:00+00:00"}]
    p = dict(fx.SHIPMENT_RTO)
    del p["rto_charges"]
    b2 = transform_shiprocket_shipment(ORG, p)
    assert b2.returns[0]["cost_paise"] is None  # unknown, NOT silently zero
    assert any(i.startswith("rto_cost_missing") for i in b2.issues)


def test_shiprocket_customer_return_has_no_forward_shipping_fee():
    b = transform_shiprocket_shipment(ORG, fx.SHIPMENT_RETURN)
    assert b.fees == [] and b.order["status"] == "returned"
    assert b.returns[0]["type"] == "customer_return" and b.returns[0]["cost_paise"] == 8000


def test_shiprocket_invalid_inputs():
    with pytest.raises(TransformError):
        transform_shiprocket_shipment(ORG, {"id": 1})  # no order ref
    with pytest.raises(TransformError):
        transform_shiprocket_shipment(ORG, {"channel_order_id": "1"})  # no id
    b = transform_shiprocket_shipment(ORG, {"id": 1, "channel_order_id": "1", "status": "SOMETHING NEW"})
    assert b.order == {"status": None} and any(i.startswith("unmapped_shipment_status") for i in b.issues)


# ---------------------------------------------------------------- marketplace
def _rows(rows):
    return [(f"r{i}", r) for i, r in enumerate(rows)]


def test_marketplace_multi_line_order_fees_and_settlement():
    [b] = transform_marketplace_rows(ORG, "amazon", _rows(fx.AMAZON_OK))
    assert b.external_id == "AMZ-1" and b.order["gross_revenue_paise"] == 150000
    assert b.order["taxes_paise"] == 22881 and b.order["status"] == "delivered"
    assert b.order["ordered_at"] == "2026-09-10T04:30:00+00:00"  # naive date read as IST
    assert {(f["type"], f["amount_paise"]) for f in b.fees} == {("commission", 22500), ("fulfilment", 2000)}
    assert all(f["source"] == "csv" for f in b.fees)
    [s] = b.settlements
    assert (s["channel"], s["settlement_ref"], s["net_paise"], s["order_channel"]) == ("amazon", "S-100", 125500, "amazon")
    assert s["settled_at"] == "2026-09-19T18:30:00+00:00" and s["line_type"] == "sale"
    assert any(i.startswith("settlement_components_unknown") for i in b.issues)   # net-only: split not invented
    # explicit split is honoured and checked: net = gross - fees
    rows = [dict(fx.AMAZON_OK[0], settlement_gross="1500.00", settlement_fees="245.00")]
    [b2] = transform_marketplace_rows(ORG, "amazon", _rows(rows))
    assert (b2.settlements[0]["gross_paise"], b2.settlements[0]["fees_paise"]) == (150000, 24500)
    assert not any(i.startswith("settlement_components") for i in b2.issues)
    rows = [dict(fx.AMAZON_OK[0], settlement_gross="1500.00", settlement_fees="100.00")]
    assert any(i.startswith("settlement_components_inconsistent") for i in transform_marketplace_rows(ORG, "amazon", _rows(rows))[0].issues)


def test_marketplace_duplicate_line_id_not_double_counted():
    [b] = transform_marketplace_rows(ORG, "amazon", _rows(fx.AMAZON_OK + [fx.AMAZON_OK[0]]))
    assert b.order["gross_revenue_paise"] == 150000
    assert any(i.startswith("duplicate_line_skipped") for i in b.issues)


def test_marketplace_groups_orders_and_validates():
    out = transform_marketplace_rows(ORG, "amazon", _rows(fx.AMAZON_OK + fx.AMAZON_MISMATCH))
    assert [b.external_id for b in out] == ["AMZ-1", "AMZ-2"]
    assert transform_marketplace_rows(ORG, "amazon", []) == []
    with pytest.raises(TransformError):
        transform_marketplace_rows(ORG, "amazon", _rows([{"sku": "x"}]))
    with pytest.raises(TransformError):
        transform_marketplace_rows(ORG, "ebay", _rows(fx.AMAZON_OK))
    with pytest.raises(TransformError):
        transform_marketplace_rows(ORG, "amazon", _rows([fx.mkt_row("X", "S", "abc")]))
    with pytest.raises(TransformError):
        transform_marketplace_rows(ORG, "amazon", _rows([fx.mkt_row("X", "S", "1.00", date="")]))


def test_marketplace_status_cancel_missing_sku_unmapped():
    [b] = transform_marketplace_rows(ORG, "blinkit", _rows(fx.BLINKIT_CANCELLED))
    assert b.order["status"] == "cancelled"
    [m] = transform_marketplace_rows(ORG, "zepto", _rows([fx.mkt_row("Z1", "", "10.00", status="???")]))
    assert any(i.startswith("missing_sku") for i in m.issues) and any(i.startswith("unmapped_order_status") for i in m.issues)
    assert map_order_status("RTO initiated") == "rto" and map_order_status("Return completed") == "returned"


def test_marketplace_return_without_cost_is_surfaced():
    [b] = transform_marketplace_rows(ORG, "flipkart", _rows([fx.mkt_row("F1", "A", "10.00", status="returned")]))
    assert b.returns == [{"type": "customer_return", "cost_paise": None, "returned_at": None}]
    assert any(i.startswith("return_cost_missing") for i in b.issues)


def test_estimate_fees():
    o = {"gross_revenue_paise": 100000, "discounts_paise": 0, "shipping_charged_paise": 0}
    assert estimate_fees(o, {}) == []
    assert estimate_fees(o, {"payment_gateway": 200}) == [{"type": "payment_gateway", "amount_paise": 2000, "source": "estimated"}]


def test_shipment_status_enum_mapping():
    from transforms.shiprocket import shipment_status
    assert shipment_status("RTO DELIVERED", False) == "rto_delivered" and shipment_status("RTO INITIATED", False) == "rto_initiated"
    assert shipment_status("Delivered", False) == "delivered" and shipment_status("IN TRANSIT", False) == "in_transit"
    assert shipment_status("OUT FOR DELIVERY", False) == "out_for_delivery" and shipment_status("CANCELED", False) == "cancelled"
    assert shipment_status("???", False) == "unknown" and shipment_status(None, False) == "unknown"
