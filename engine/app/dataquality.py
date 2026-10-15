"""System-level data-quality checks (main-owned). Surfaces problems; never repairs or hides them.

`run_checks(conn, org_id)` returns {"org_id", "generated_at", "status", "checks": [...]}. Each check:
{code, severity: ok|info|warn|error, count, message, examples: [...] (capped)}. `status` is the worst severity.
Used by GET /data-quality and, via an adapter, by the AI `get_data_quality` tool. Read-only SQL on the ledger
tables (service-role connection, always scoped by org_id).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from ledger.pg_repository import PsycopgRepository
from reconciliation.report import reconcile_snapshot

SEVERITY_RANK = {"ok": 0, "info": 1, "warn": 2, "error": 3}
EXAMPLES = 5
MISSING_SKU = "__MISSING_SKU__"


def _check(code: str, count: int, severity_if_any: str, message: str, examples: list | None = None) -> dict:
    return {"code": code, "severity": severity_if_any if count else "ok", "count": int(count),
            "message": message if count else "ok", "examples": (examples or [])[:EXAMPLES]}


def _q(conn, sql: str, params: tuple) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def run_checks(conn: Any, org_id: str, *, now: datetime | None = None, stale_hours: int = 36) -> dict:
    now = now or datetime.now(timezone.utc)
    checks: list[dict] = []
    p = (org_id,)

    rng = _q(conn, "select min(ordered_date_ist), max(ordered_date_ist), count(*) from orders where org_id = %s", p)[0]
    first, last, n_orders = rng
    if not n_orders:
        checks.append(_check("no_orders", 1, "warn", "no orders in the ledger yet"))

    # duplicate orders: same customer, instant and amount under different external ids
    dup = _q(conn, """select channel, customer_key, ordered_at, gross_revenue_paise, count(*), array_agg(external_id)
                      from orders where org_id = %s and customer_key is not null
                      group by 1,2,3,4 having count(*) > 1""", p)
    checks.append(_check("possible_duplicate_orders", len(dup), "warn",
                         "orders with identical channel/customer/time/amount but different external ids",
                         [{"channel": r[0], "external_ids": r[5][:4]} for r in dup]))

    # SKU problems
    miss = _q(conn, "select count(*), array_agg(distinct order_id::text) from order_items where org_id = %s and sku = %s", (org_id, MISSING_SKU))[0]
    checks.append(_check("missing_sku_lines", miss[0], "error",
                         f"order lines with no SKU (placeholder {MISSING_SKU}); COGS is unknown for them", (miss[1] or [])))
    unk = _q(conn, """select distinct i.sku from order_items i left join skus s on s.org_id = i.org_id and s.sku = i.sku
                      where i.org_id = %s and i.sku <> %s and s.sku is null""", (org_id, MISSING_SKU))
    checks.append(_check("skus_not_in_catalogue", len(unk), "warn", "SKUs on orders that are not in the skus table", [r[0] for r in unk]))

    # COGS coverage: SKUs sold with no COGS row, or sold before their earliest effective date
    nocogs = _q(conn, """select i.sku, count(*) from order_items i join orders o on o.id = i.order_id
                         where i.org_id = %s and i.sku <> %s and not exists (
                           select 1 from sku_cogs c where c.org_id = i.org_id and c.sku = i.sku and c.effective_from <= o.ordered_date_ist)
                         group by 1 order by 2 desc""", (org_id, MISSING_SKU))
    checks.append(_check("missing_cogs", len(nocogs), "error",
                         "SKUs sold with no COGS effective on the order date (contribution margin is overstated)",
                         [{"sku": r[0], "lines": r[1]} for r in nocogs]))

    # negative / impossible financial values
    neg = _q(conn, """select external_id from orders where org_id = %s and (gross_revenue_paise < 0 or discounts_paise < 0
                      or taxes_paise < 0 or refunds_paise < 0 or shipping_charged_paise < 0
                      or refunds_paise > gross_revenue_paise)""", p)
    checks.append(_check("negative_or_impossible_order_amounts", len(neg), "error",
                         "orders with negative amounts or refunds above gross", [r[0] for r in neg]))
    negfee = _q(conn, "select order_id::text from fees where org_id = %s and amount_paise < 0", p)
    checks.append(_check("negative_fees", len(negfee), "warn", "fee rows with negative amounts (credits?)", [r[0] for r in negfee]))

    # missing dates: calendar days with zero orders inside the active window
    if first and last and last > first:
        gaps = _q(conn, """select d::date from generate_series(%s::date, %s::date, interval '1 day') d
                           where not exists (select 1 from orders where org_id = %s and ordered_date_ist = d::date)""",
                  (first, last, org_id))
        checks.append(_check("days_without_orders", len(gaps), "info", "calendar days with no orders between first and last order",
                             [g[0].isoformat() for g in gaps]))

    # geography
    badpin = _q(conn, "select count(*) from orders where org_id = %s and pincode is null", p)[0][0]
    checks.append(_check("orders_without_pincode", badpin, "info", "orders with no (valid) pincode; city/pincode analysis is incomplete"))
    unknown_tier = _q(conn, "select count(*) from orders where org_id = %s and city_tier = 'unknown'", p)[0][0]
    checks.append(_check("orders_with_unknown_city_tier", unknown_tier, "warn",
                         "orders whose city is not in the city tier table (RTO segmentation falls back to coarser segments)"))

    # duplicate imports: CSV rows landed more than once under different ids
    dimp = _q(conn, """select source, count(*) from (select source, md5(payload::text) h, count(*) c from raw_records
                       where org_id = %s and source like 'csv\\_%%' group by 1,2 having count(*) > 1) t group by 1""", p)
    checks.append(_check("duplicate_csv_imports", sum(r[1] for r in dimp), "warn",
                         "identical CSV rows stored under different raw ids", [{"source": r[0], "groups": r[1]} for r in dimp]))

    # ad spend gaps per platform inside the order window
    adgaps = []
    for platform, pfirst, plast, ndays in _q(conn, "select platform, min(date), max(date), count(distinct date) from ad_spend_daily where org_id = %s group by 1", p):
        expected = (plast - pfirst).days + 1
        if expected > ndays:
            adgaps.append({"platform": platform, "missing_days": expected - ndays})
    checks.append(_check("ad_spend_gaps", sum(g["missing_days"] for g in adgaps), "warn",
                         "days without ad spend rows between a platform's first and last day (zero-spend days may be legitimate)", adgaps))
    if n_orders and not _q(conn, "select 1 from ad_spend_daily where org_id = %s limit 1", p):
        checks.append(_check("no_ad_spend_data", 1, "warn", "orders exist but no ad spend is connected: MER, CAC and contribution after marketing are unavailable"))

    # integrations
    stale_before = now - timedelta(hours=stale_hours)
    integ = _q(conn, "select provider, status, last_synced_at, last_error from integrations where org_id = %s", p)
    stale = [{"provider": r[0], "status": r[1], "last_synced_at": r[2].isoformat() if r[2] else None, "last_error": r[3]}
             for r in integ if r[1] == "error" or (r[1] == "active" and (r[2] is None or r[2] < stale_before))]
    checks.append(_check("stale_or_failing_integrations", len(stale), "error", f"integrations in error or not synced for {stale_hours}h", stale))

    # unreconciled settlements: delegate to the ledger's reconciliation report (read-only)
    if first:
        rep = reconcile_snapshot(org_id, first, last, PsycopgRepository(conn).fetch_snapshot(org_id, first, last), as_of=now.date())
        unexplained = [{"channel": c["channel"], "status": c["status"], "unexplained_abs_paise": c["totals"]["unexplained_abs_paise"],
                        "by_code": c.get("summary_by_code", {})} for c in rep["channels"] if c["totals"]["unexplained_abs_paise"]]
        checks.append(_check("unreconciled_settlements", len(unexplained), "warn",
                             "channels with unexplained differences between orders, fees, returns and settlements", unexplained))

    worst = max((c["severity"] for c in checks), key=SEVERITY_RANK.get, default="ok")
    return {"org_id": org_id, "generated_at": now.isoformat(), "status": worst,
            "checks": sorted(checks, key=lambda c: (-SEVERITY_RANK[c["severity"]], c["code"]))}
