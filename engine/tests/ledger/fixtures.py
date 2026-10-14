"""SYNTHETIC, HAND-BUILT FIXTURES. NOT real Shopify/Shiprocket/marketplace output.

Payload shapes are the ASSUMED shapes documented in transforms/*.py (unverified against live APIs). All values
are invented; amounts are rupees as the sources would send them. Deterministic (no randomness, no clock).
"""
ORG = "00000000-0000-0000-0000-000000000001"
ORG2 = "00000000-0000-0000-0000-000000000002"


def _shopify(id_, name, created, items, *, gateway, discounts="0.00", tax, shipping="0.00", total, **extra):
    p = {"id": id_, "name": name, "order_number": int(name.lstrip("#")), "created_at": created, "taxes_included": True,
         "payment_gateway_names": [gateway], "total_discounts": discounts, "total_tax": tax, "total_price": total,
         "total_shipping_price_set": {"shop_money": {"amount": shipping, "currency_code": "INR"}},
         "line_items": items, "refunds": [], "email": "Asha.Rao@Example.com ", "phone": "+91 98765 43210",
         "shipping_address": {"city": "Mumbai", "zip": "400001", "phone": None}, "fulfillment_status": "fulfilled",
         "cancelled_at": None}
    p.update(extra)
    return p


# Normal prepaid, multi-item, discount, shipping, GST-inclusive. gross 1997.00, paid 1946.00
SHOPIFY_NORMAL = _shopify(
    5001, "#1001", "2026-09-10T10:00:00+05:30",
    [{"sku": "SER-30", "quantity": 1, "price": "799.00"}, {"sku": "MOI-50", "quantity": 2, "price": "599.00"}],
    gateway="razorpay", discounts="100.00", tax="296.85", shipping="49.00", total="1946.00")

# COD, single item, free shipping. paid 1199.00
SHOPIFY_COD = _shopify(
    5002, "#1002", "2026-09-11T11:00:00+05:30", [{"sku": "SER-50", "quantity": 1, "price": "1199.00"}],
    gateway="Cash on Delivery (COD)", tax="182.89", total="1199.00",
    shipping_address={"city": "Jaipur", "zip": "302001"})

# COD order that will RTO (shipment fixture below)
SHOPIFY_RTO = _shopify(
    5003, "#1003", "2026-09-12T09:00:00+05:30", [{"sku": "SHA-250", "quantity": 1, "price": "449.00"}],
    gateway="cod", tax="68.49", shipping="50.00", total="499.00", shipping_address={"city": "Patna", "zip": "800001"})

# Prepaid, delivered, PARTIAL refund of 300.00 (customer return, see shipment). paid 799.00
SHOPIFY_RETURN = _shopify(
    5004, "#1004", "2026-09-05T12:00:00+05:30", [{"sku": "SER-30", "quantity": 1, "price": "799.00"}],
    gateway="razorpay", tax="121.88", total="799.00",
    refunds=[{"id": 1, "transactions": [{"kind": "refund", "status": "success", "amount": "300.00"}]}])

# Prepaid, fully refunded goodwill refund, no return shipment
SHOPIFY_REFUND = _shopify(
    5005, "#1005", "2026-09-06T12:00:00+05:30", [{"sku": "OIL-100", "quantity": 1, "price": "349.00"}],
    gateway="razorpay", tax="53.24", total="349.00",
    refunds=[{"id": 2, "transactions": [{"kind": "refund", "status": "success", "amount": "349.00"},
                                        {"kind": "refund", "status": "failure", "amount": "999.00"}]}])

# Missing SKU on one line
SHOPIFY_MISSING_SKU = _shopify(
    5006, "#1006", "2026-09-13T12:00:00+05:30",
    [{"sku": "", "quantity": 1, "price": "100.00", "title": "Custom gift"}, {"sku": "OIL-100", "quantity": 1, "price": "349.00"}],
    gateway="razorpay", tax="68.49", total="449.00")

# Prices EXCLUDE GST: 1000.00 + 180.00 tax = customer pays 1180.00
SHOPIFY_TAX_EXCLUSIVE = _shopify(
    5007, "#1007", "2026-09-14T12:00:00+05:30", [{"sku": "SER-30", "quantity": 1, "price": "1000.00"}],
    gateway="razorpay", tax="180.00", total="1180.00", taxes_included=False)

SHOPIFY_CANCELLED = _shopify(
    5008, "#1008", "2026-09-15T12:00:00+05:30", [{"sku": "SER-30", "quantity": 1, "price": "799.00"}],
    gateway="razorpay", tax="121.88", total="799.00", cancelled_at="2026-09-15T13:00:00+05:30", fulfillment_status=None)

SHIPMENT_DELIVERED = {"id": 9001, "awb": "AWB1", "channel_order_id": "1002", "status": "DELIVERED", "freight_charges": "60.00",
                      "cod_charges": "25.00", "cod": 1, "delivered_date": "2026-09-14T10:00:00+05:30",
                      "cod_remitted_amount": "1174.00", "cod_remittance_date": "2026-09-20T10:00:00+05:30",
                      "cod_remittance_ref": "REM-1"}
SHIPMENT_RTO = {"id": 9002, "awb": "AWB2", "channel_order_id": "#1003", "status": "RTO DELIVERED", "freight_charges": "70.00",
                "rto_charges": "70.00", "cod": 1, "updated_at": "2026-09-20T10:00:00+05:30"}
SHIPMENT_RETURN = {"id": 9003, "awb": "AWB3", "channel_order_id": "5004", "status": "DELIVERED", "is_return": True,
                   "freight_charges": "80.00", "updated_at": "2026-09-12T10:00:00+05:30"}
SHIPMENT_FORWARD_1004 = {"id": 9004, "awb": "AWB4", "channel_order_id": "5004", "status": "DELIVERED", "freight_charges": "55.00",
                         "delivered_date": "2026-09-08T10:00:00+05:30"}
SHIPMENT_ORPHAN = {"id": 9005, "awb": "AWB5", "channel_order_id": "NOPE-1", "status": "DELIVERED", "freight_charges": "10.00"}


def mkt_row(order_id, sku, price, *, qty=1, date="2026-09-10T10:00:00", status="delivered", **kw):
    r = {"order_id": order_id, "order_date": date, "sku": sku, "qty": qty, "item_price": price, "order_status": status,
         "payment_method": "prepaid", "city": "Bengaluru", "pincode": "560001"}
    r.update(kw)
    return r


# ASSUMED neutral schema rows (see transforms/marketplace.py). Amazon AMZ-1: 2 lines. paid 1500+... see tests.
# line1: 1 x 1000 (fees commission 150, fixed 20), line2: 2 x 250 (commission 75). paid = 1500.00
AMAZON_OK = [
    mkt_row("AMZ-1", "SER-30", "1000.00", line_id="AMZ-1-1", commission_fee="150.00", fixed_fee="20.00",
            tax="152.54", settlement_ref="S-100", settlement_amount="1255.00", settlement_date="2026-09-20T00:00:00"),
    mkt_row("AMZ-1", "MOI-50", "250.00", qty=2, line_id="AMZ-1-2", commission_fee="75.00", tax="76.27"),
]
# expected payout = 1500 - 245 = 1255.00 -> matches settlement above

# Settlement MISMATCH: paid 800, fees 120, expected 680.00, settlement says 675.00
AMAZON_MISMATCH = [mkt_row("AMZ-2", "SER-30", "800.00", commission_fee="120.00", settlement_ref="S-101",
                           settlement_amount="675.00", settlement_date="2026-09-21T00:00:00")]
# Delivered, old, no settlement at all
FLIPKART_UNSETTLED = [mkt_row("FK-1", "SHA-250", "449.00", commission_fee="60.00", date="2026-08-01T10:00:00")]
# Blinkit cancelled order
BLINKIT_CANCELLED = [mkt_row("BL-1", "OIL-100", "349.00", status="Cancelled by customer")]
