"""Synthetic Indian D2C brand: Shopify + Amazon + Blinkit, Meta + Google, mixed COD/prepaid.

Deterministic (seeded). `generate()` returns plain dicts keyed by table name, amounts in paise,
so tests and the DB loader share one source. RTO rates are deliberately higher for COD and tier-2/3.
"""
import random
from decimal import ROUND_HALF_UP, Decimal
from datetime import date, datetime, timedelta, timezone

from app import identity

CITIES = [  # (city, tier, weight)
    ("Mumbai", "metro", 14), ("Delhi", "metro", 14), ("Bengaluru", "metro", 12),
    ("Hyderabad", "metro", 8), ("Pune", "tier_1", 7), ("Jaipur", "tier_2", 5),
    ("Lucknow", "tier_2", 4), ("Indore", "tier_2", 4), ("Patna", "tier_3", 3), ("Guwahati", "tier_3", 2),
]
SKUS = [  # (sku, category, mrp_rupees, cogs_rupees)
    ("SER-30", "skincare", 799, 210), ("SER-50", "skincare", 1199, 300),
    ("MOI-50", "skincare", 599, 140), ("SHA-250", "haircare", 449, 110),
    ("OIL-100", "haircare", 349, 80), ("KIT-3", "bundle", 1799, 480),
]
PINCODES = {"Mumbai": "400001", "Delhi": "110001", "Bengaluru": "560001", "Hyderabad": "500001", "Pune": "411001",
            "Jaipur": "302001", "Lucknow": "226001", "Indore": "452001", "Patna": "800001", "Guwahati": "781001"}
CHANNELS = {"shopify": 0.55, "amazon": 0.30, "blinkit": 0.15}
FEE_PCT = {"shopify": 0.0, "amazon": 0.15, "blinkit": 0.22}   # commission
RTO_BASE = {"metro": 0.08, "tier_1": 0.12, "tier_2": 0.18, "tier_3": 0.25}
IST = timezone(timedelta(hours=5, minutes=30))


def _pct(amount: int, pct: int) -> int:
    return int((Decimal(amount) * pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def generate(start: date = date(2025, 4, 1), weeks: int = 78, seed: int = 7) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    out: dict[str, list[dict]] = {k: [] for k in ("orders", "order_items", "fees", "returns", "ad_spend_daily", "sku_cogs")}
    for sku, _, _, cogs in SKUS:
        out["sku_cogs"].append({"sku": sku, "effective_from": start, "cogs_paise": cogs * 100})
    city_pool = [c for c in CITIES for _ in range(c[2])]
    customers = [f"cust{i}" for i in range(4000)]
    seen: set[str] = set()
    n = 0
    for d in range((weeks * 7)):
        day = start + timedelta(days=d)
        season = 1 + 0.25 * (day.month in (10, 11)) + 0.1 * (day.weekday() >= 5)
        meta = int(rng.uniform(25000, 45000) * 100 * season)
        goog = int(rng.uniform(10000, 22000) * 100 * season)
        for plat, spend, cpc in (("meta", meta, 1200), ("google", goog, 1800)):
            clicks = spend // cpc
            conv = clicks * rng.uniform(0.012, 0.02)
            out["ad_spend_daily"].append({
                "platform": plat, "campaign_id": f"{plat}-main", "campaign_name": f"{plat} prospecting",
                "date": day, "spend_paise": spend, "impressions": clicks * 40, "clicks": clicks,
                "platform_reported_conversions": round(conv, 2),
                "platform_reported_revenue_paise": int(conv * 95000 * rng.uniform(1.2, 1.6)),  # platforms over-claim
            })
        orders_today = int(rng.gauss(55, 8) * season + (meta + goog) / 100 / 2500)
        for _ in range(max(orders_today, 0)):
            n += 1
            channel = rng.choices(list(CHANNELS), weights=CHANNELS.values())[0]
            city, tier, _w = rng.choice(city_pool)
            cod = channel == "shopify" and rng.random() < 0.55
            cust = rng.choice(customers)
            ordered = datetime.combine(day, datetime.min.time(), IST) + timedelta(seconds=rng.randint(0, 86399))
            oid = f"{channel[:3]}-{n}"
            gross = discount = 0
            items = []
            for sku, _cat, mrp, _c in rng.sample(SKUS, rng.choice([1, 1, 1, 2])):
                qty = rng.choice([1, 1, 2])
                price = mrp * 100
                items.append({"order_ext": oid, "sku": sku, "qty": qty, "unit_price_paise": price})
                gross += price * qty
            discount = int(gross * rng.choice([0, 0.1, 0.15, 0.2]))
            tax = int((gross - discount) * 18 / 118)   # GST included in price
            rto = rng.random() < RTO_BASE[tier] * (1.8 if cod else 0.4) and channel != "blinkit"
            returned = (not rto) and rng.random() < 0.04
            refund = (gross - discount) if returned else 0
            out["orders"].append({
                "channel": channel, "external_id": oid, "ordered_at": ordered, "customer_email": f"{cust}@seed.example.invalid", "customer_key": identity.hash_email(f"{cust}@seed.example.invalid"),
                "city": city, "city_tier": tier, "pincode": PINCODES[city], "payment_method": "cod" if cod else "prepaid",
                "status": "rto" if rto else ("returned" if returned else "delivered"),
                "gross_revenue_paise": gross, "discounts_paise": discount, "taxes_paise": tax,
                "shipping_charged_paise": 0 if channel != "shopify" else 4900 * (gross < 99900), "refunds_paise": refund,
            })
            out["order_items"].extend(items)
            if FEE_PCT[channel]:
                out["fees"].append({"order_ext": oid, "type": "commission", "amount_paise": int((gross - discount) * FEE_PCT[channel]), "source": "csv"})
            if channel != "blinkit":
                out["fees"].append({"order_ext": oid, "type": "shipping", "amount_paise": 7500 + 1500 * (tier in ("tier_2", "tier_3")), "source": "api"})
            if channel == "shopify" and not cod:
                out["fees"].append({"order_ext": oid, "type": "payment_gateway", "amount_paise": _pct(gross - discount + 4900 * (gross < 99900), 2), "source": "api"})
            if cod and channel == "shopify":
                out["fees"].append({"order_ext": oid, "type": "other", "amount_paise": 3000, "source": "api"})  # COD handling
            if rto:
                out["returns"].append({"order_ext": oid, "type": "RTO", "cost_paise": 7500 * 2, "returned_at": ordered + timedelta(days=9)})
            elif returned:
                out["returns"].append({"order_ext": oid, "type": "customer_return", "cost_paise": 7500, "returned_at": ordered + timedelta(days=10)})
            seen.add(cust)
    return out
