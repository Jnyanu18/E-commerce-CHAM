# Contracts

Interfaces between agents. Change by proposal to main, not unilaterally. Money is always integer paise; timestamps are UTC ISO-8601.

## 1. Connector -> Raw Record
Connectors write only to `raw_records` (idempotent upsert on `(org_id, source, external_id)`), never to the ledger.
```json
{"org_id": "uuid", "source": "shopify_order", "external_id": "gid or id", "payload": {"...": "verbatim API/CSV row"}}
```
Plus: update `integrations.status / last_synced_at / last_error`, then enqueue a `transform` job for the org. Sources: `shopify_order`, `shiprocket_shipment`, `meta_insights`, `google_ads_row`, `csv_<marketplace>`.

## 2. Raw Record -> Canonical Ledger
Ledger transforms are pure and re-runnable from `raw_records`. They upsert `orders`, `order_items`, `fees`, `returns`, `ad_spend_daily`, `traffic_daily` keyed on the unique constraints in `supabase/migrations`. The schema is the contract; changes go through `docs/SCHEMA_CHANGE_REQUESTS.md`. Fees carry `source` (`api|csv|estimated`) so estimates never overwrite actuals.

## 3. Ledger -> Metric Layer
Metrics read only ledger tables. Every metric function signature:
`metric(org_id, date_range, filters, group_by) -> MetricResult`
```json
{"metric": "contribution_margin", "value": 720000, "unit": "paise", "currency": "INR",
 "period": {"start": "2026-09-01", "end": "2026-09-30"},
 "filters": {"channel": ["shopify"]}, "group_by": "channel",
 "groups": [{"key": "shopify", "value": 500000}],
 "definition": "net_revenue - COGS - fees - shipping - RTO/return cost",
 "data_quality": {"status": "ok | degraded | insufficient", "issues": ["cogs_missing_for_skus: [...]"], "row_count": 1234}}
```
`value` is integer paise for money metrics; ratio metrics (MER, ROAS) use `unit: "ratio"` and a decimal value; counts use `unit: "count"`. `period` dates are IST calendar dates (`orders.ordered_date_ist`). Missing inputs (e.g. no COGS) appear in `data_quality.issues`, never silently zero; undefined results (division by zero, insufficient RTO history) return `value: null` with `status: "insufficient"`. (Supersedes the earlier draft shape `value_paise` / `date_range` / `caveats`.)

## 4. Metric Layer -> API
FastAPI endpoints return `MetricResult` unchanged: `GET /metrics/{metric}?org_id&start&end&group_by&filters`. The frontend renders these and does no business math. Auth: Supabase JWT, org membership checked server-side.

## 5. Metric Layer -> Claude Tools
Each metric is exposed as a tool with the same parameters and returns the same `MetricResult` plus a `call_id`. No SQL tool exists. The AI agent records every call in `insights.tool_calls`.

## 6. Intelligence -> Recommendation
Writes `model_runs`, `channel_response_curves`, `recommendations`, `experiments`. Outputs are one of:
```json
{"status": "INSUFFICIENT_DATA", "reason": "47 weeks < 52", "fallback": "descriptive"}
```
or a result with `expected_impact_paise`, `ci_low_paise`, `ci_high_paise` (80% interval), `prob_beats_current`, diagnostics (R-hat, PPC). Revenue, profit, CAC, ROAS and margin come only from the metric layer.

## 7. Recommendation -> AI Brief
The AI agent reads `recommendations`, `experiments` and `model_runs.diagnostics` through tools. Every number in `insights.brief_markdown` must carry a citation `[call_id]` that resolves to a stored tool result; uncited numbers fail validation. Missing data is stated; intervals are quoted only from stored results.

## 8. Shared conventions (set by main, 2026-10-14)
- **City tier enum** (canonical, everywhere): `metro | tier_1 | tier_2 | tier_3 | unknown`. The initial migration used `tier1..tier3` without `unknown`; the database agent changes it in a new migration (`orders.city_tier`, `city_tiers.tier`), and ledger/metrics code uses the new values.
- **Pincode** is `text` (6 digits, leading zeros possible), nullable.
- **Money** is `bigint` paise end to end. Ratios are never stored as money.
- **Natural keys** for idempotency: `(org_id, source, external_id)` for raw records, `(org_id, channel, external_id)` for orders.
- **Responsibility split for CSV:** connectors parse/validate an upload and write `raw_records` (`csv_<marketplace>`); ledger normalises those rows into the canonical ledger.
- **Settlements:** the database agent owns the table design; ledger consumes it. Until it merges, ledger works against a documented interface.
- **Data-quality surface:** metrics report per-query `data_quality`; system-level checks (duplicates, missing COGS, unreconciled settlements, stale integrations) are assembled by main and exposed to the API and the AI `get_data_quality` tool.
