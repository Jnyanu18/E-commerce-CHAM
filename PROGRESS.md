# Progress

## Done
### Main / workspace
- Seven Git worktrees and specialist branches created (all at the same commit, clean)
- Existing implementation preserved (schema 0001-0004, engine scaffold, seed generator, compose)
- Agent ownership established (`CLAUDE.md`, `docs/AGENT_WORKFLOW.md`)
- Shared contracts established (`docs/CONTRACTS.md`)
- `.gitattributes` added; `CLAUDE.md` created
- Engine tests: 3 passed (re-verified on main after worktree setup)

## In Progress
- Parallel development preparation

## Blocked
- Docker unavailable (Postgres verified without it; see below). Real Supabase (GoTrue, JWT claims, PostgREST) is still unverified.
- External API credentials/approvals pending (Shopify app, Meta App Review, Google Ads developer token, Shiprocket)

## Integration Needed
- Specialist branches will require integration into main after review

## Integration review findings (main, 2026-10-14)
1. **Queue code untested.** `engine/worker/queue.py` and `claim_job()` need a real Postgres. `run_one` sets `conn.row_factory` permanently on the caller's connection; use a cursor with `row_factory` instead. Fix when the worker is picked up.
2. **Seed output isn't loadable yet.** `seed/synthetic.py` keys child rows by `order_ext` (external id); a loader must map to `orders.id`, add `org_id`, and fill `pincode` (currently null). `seen` set is unused. `seed/` has no `__init__.py` (works as a namespace package).
3. **Generic top-level package names** (`app`, `metrics`, `worker`, `seed`, `ai`, `models`) in one flat `engine/` dir can collide with third-party or stdlib names (e.g. `models`, `ai`). Left as is to avoid renaming owned paths; consider a single `engine` package later.
4. **Packaging.** `pyproject.toml` listed only 5 packages; changed to `packages.find` with namespaces covering every ownership dir, so new agent directories install without edits.
5. **Venv and worktrees.** `.venv` is untracked and exists only in main's `engine/`, with an editable install pointing at main's code. In a worktree, run `python -m pytest` from that worktree's `engine/` (cwd wins on `sys.path`) or create a venv per worktree.
6. **Cross-ownership dependency.** `app.money` and `app.crypto` (main-owned) will be imported by ledger, connectors and metrics. Treat as stable shared utilities; changes go through main.
7. **Metric duplication risk.** The seed generator encodes RTO/fee logic for synthetic data only; metrics and ledger code must never import it. Metrics need their own hand-calculated fixtures.
8. **Schema gaps to confirm.** No insert policy on `orgs`/`org_members` (pilots onboarded via service role; fine for MVP). No settlements table although the ledger scope mentions settlements/reconciliation, so a schema change request is likely. `city_tiers` is empty and needs seeding.
9. **`web/` is a placeholder** (only `.gitkeep`); frontend agent must scaffold Next.js. `engine/.gitkeep` is redundant.
10. **Product name unset.** CRLF warnings are addressed by `.gitattributes`.

## Database verification (main, 2026-10-14)
Executed on a throwaway **PostgreSQL 18.1** instance (port 54399, installed binaries, separate data dir; not Supabase). Migrations 0001-0004 applied cleanly on top of `local_stub.sql` plus emulated Supabase default privileges.
Verified by execution:
- Generated column `ordered_date_ist` is valid and converts correctly (20:00Z -> next IST day).
- Unique natural keys reject duplicates; CHECK constraints reject negative COGS, bad payment method, and `tier_1` (the old enum, as expected until the database agent migrates it).
- RLS: member sees only their org's orders/integrations/orgs; cross-tenant insert blocked; member cannot write pipeline tables, create orgs, or add themselves to another org; anon sees nothing.
- Credentials: `select credentials_enc` and `select *` on `integrations` are denied for `authenticated`; safe columns are readable.
- All money columns are bigint (arrays bigint[]); pincode is text; RLS is enabled on every public table except reference table `city_tiers`... (it also has RLS; only `orgs`/`city_tiers` lack `org_id` by design).
- `claim_job()`: 6 concurrent workers claimed 6 distinct jobs; empty queue returns an all-null row. `worker/queue.py` retry/backoff verified against it (success -> done; failure -> requeued with backoff).
Not verified / gaps found: Supabase-specific behaviour (real `auth.uid()` from JWT, PostgREST, service_role); no recovery for jobs stuck in `running` after a worker crash (needs a reaper on `locked_at`); `claim_job()` returns a null composite instead of zero rows (works, awkward); no org-creation path for users (service role only); `tier_1/unknown` migration and settlements table still pending (database agent).
## Metrics agent (agent/metrics, 2026-10-14)
### Done
- `engine/metrics/`: `net_revenue`, `contribution_margin`, `expected_rto_loss`, `blended_cac`, `mer`, `platform_roas`, `contribution_after_marketing` as pure functions `metric(org_id, date_range, filters, group_by, *, repo) -> MetricResult` (contract section 3, `to_dict()` is JSON-safe).
- Repository Protocol (`repository.py`) with `InMemoryRepository` (tests) and `PostgresRepository` (plain SELECTs, no formulas in SQL; maps legacy `tier1..3` to `tier_1..3`).
- Registry `metrics.METRICS: dict[str, MetricSpec]` (+ `run_metric`) so API and AI tools wrap the same callables; `MetricError` (ValueError) for bad filters/group_by (map to HTTP 400).
- Group-bys: channel, sku, category, city, city_tier, pincode, payment_method, week (Monday-start IST). Period helpers: `week_start`, `fy_range`, `fy_range_for`, `fy_label`.
- 59 tests with hand-calculated fixtures, all passing.
### In Progress
- None.
### Blocked
- `PostgresRepository` not exercised against a live Postgres (no Docker); only a stubbed-cursor test.
### Integration Needed
- Main: construct a repository per request (`PostgresRepository(conn)`) and call `METRICS[name].callable(..., repo=repo)`; map `MetricError` to 400. Ratio values are `Decimal` in the dataclass and floats (4 dp) in `to_dict()`.
- Ledger: must populate `orders.status` ('rto' for undelivered returns-to-origin), `refunds_paise`, `customer_key`, `city_tier`, `fees.source`; metrics treat `rto` orders as unrealised (no revenue/COGS), see DECISIONS.
- Database: `orders.city_tier` check constraint still uses `tier1..tier3` (contract wants `tier_1..` + `unknown`).
- Ad spend has no channel/SKU/city attribution, so `mer`, `blended_cac`, `contribution_after_marketing` support only `group_by=week` (platform_roas also `platform`). Proposed, not a contract change.

## Database agent (2026-10-14)
### Done
- Migrations 0005-0011 (city tier enum, pincode CHECK, settlements/settlement_items/shipments + reconciliation view, indexes, queue hardening with reaper, city tier seed + `lookup_city_tier()`, privilege hardening). 0001-0004 untouched.
- `supabase/tests/`: `pre.sql` (Supabase default-privilege emulation + `service_role`), `helpers.sql`, tests 01-07, `run.sh` (recreates `db_agent`, applies stub + pre + all migrations, runs tests; 8-session `claim_job` concurrency test).
- Executed on PostgreSQL 18.1 (localhost:54399): `bash supabase/tests/run.sh` -> all 7 test groups PASS. Also executed: upgrade path (0001-0004 with pre-existing `tier2`/NULL rows, then 0005-0011) migrates values correctly.
### Not verified (real Supabase)
JWT -> `auth.uid()` via PostgREST, `service_role` as shipped by Supabase (emulated here), PostgREST rpc exposure of functions, Supabase PG version (`ON DELETE SET NULL (col)` and `security_invoker` views need PG15+).
### Integration Needed
- **main (`engine/worker`)**: call `select reap_stale_jobs()` periodically (e.g. each poll loop or every few minutes); `queue.py` needs no change for `claim_job`. Still open from earlier review: `run_one` mutates `conn.row_factory`.
- **main (`engine/seed/synthetic.py`)**: emits `tier1/tier2/tier3` in `CITIES`/`RTO_BASE`; must emit `tier_1/tier_2/tier_3` (DB now rejects the old values). Seed also leaves `pincode` null (allowed) .
- **ledger**: upsert targets `settlements (org_id, channel, external_id)`, `settlement_items (settlement_id, order_external_id, line_type)`, `shipments (org_id, source, external_id)`; set `order_id` when the order is known (composite FK requires same org). `orders.city_tier` may be omitted (defaults to `unknown`); `lookup_city_tier(city)` is available.
- **Contracts proposal (for main)**: add settlements/shipments to CONTRACTS section 2 upsert list; sign convention gross/fees positive, net = gross - fees, refunds/adjustments as separate item lines.
## Ledger agent (agent/ledger, 2026-10-14)
### Done
- Pure transforms (`engine/transforms/`): Shopify order, Shiprocket shipment (partial/patch bundle), neutral marketplace CSV schema (Amazon, Flipkart, Blinkit, Zepto, Instamart), estimated-fee helper.
- Persistence (`engine/ledger/`): abstract `Repository`, `InMemoryRepository`, `PsycopgRepository`; idempotent pipeline `run_transform`; job handlers `handle_transform` / `handle_reconcile` (signature `(job, conn) -> None`).
- Reconciliation (`engine/reconciliation/report.py`): structured per org/channel/period discrepancy report, read-only.
- 58 ledger tests added (61 total in engine suite passing).
### In Progress
- Nothing.
### Blocked
- Settlement persistence needs the `settlements` table (docs/SCHEMA_CHANGE_REQUESTS.md #1). `PsycopgRepository` never run against a real Postgres.
- Writing `city_tier` values `tier_1..` needs the database agent's enum migration.
### Integration Needed
- Main: register `{"transform": ledger.jobs.handle_transform, "reconcile": ledger.jobs.handle_reconcile}` in the worker; `engine/seed` loader unaffected.
- Connectors: ALL source payload shapes are ASSUMED (see module docstrings of `transforms/shopify.py`, `shiprocket.py`, `marketplace.py`). Confirm Shopify/Shiprocket fields; map each marketplace CSV onto the neutral row schema; Shiprocket `channel_order_id` is assumed to be the Shopify id, name or order_number. Raw `external_id` for CSV rows should be unique per line.
- Metrics: GST rule in `transforms/common.py` docstring (gross is GST-inclusive, taxes_paise contained within); `__MISSING_SKU__` means COGS unknown; estimated fees are superseded by actuals (api > csv > estimated per order+type).

### Ledger follow-up (2026-10-14, after merge of main 7d18b5d)
- Adapted to migration 0007: `PsycopgRepository` writes `settlements` / `settlement_items` / `shipments`; Shiprocket data lands in `shipments` even before its order exists; COD remittance is a settlement on channel `shiprocket_cod` (net = remitted, fees = COD charge, gross = net + fees: ASSUMED). Marketplace settlement lines are net-only unless the neutral row carries `settlement_gross` / `settlement_fees` (a `settlement_components_unknown` issue is raised otherwise). Settlement items are not written until the order reference resolves, to avoid duplicate items under an unresolved reference.
- Ran against real PostgreSQL 18 (scratch db `ledger_agent`, migrations 0001-0011). `engine/tests/ledger/test_pg_integration.py` runs when `DATABASE_URL` is set (e.g. `postgresql://postgres@localhost:54399/ledger_agent`), else skips. Bug found and fixed: partial order patches (shipment status) failed on `INSERT .. ON CONFLICT` NOT NULL checks; now a plain UPDATE.
- **Answer to metrics (RTO and refunds):** NO. An order with status `rto` does not get the full amount in `refunds_paise`. `refunds_paise` is only what the source reports (Shopify successful refund transactions, marketplace `refund_amount`); an RTO COD order has 0, a prepaid RTO has 0 until the source reports a refund (reconciliation flags `rto_prepaid_not_refunded`). Tested in memory and on Postgres.
## Connectors agent (branch agent/connectors, 2026-10-14)
### Done
- `engine/connectors/`: lifecycle base (`base.py`), typed errors, retrying HTTP client (exponential backoff, full jitter, Retry-After), credentials via `app.crypto` (never logged), in-memory + psycopg stores, `transform` job enqueue, registry.
- Shopify GraphQL (orders, line items, refunds, hashed customer identity, windowed backfill, updated_at incremental, cost-based throttle handling), Shiprocket (shipments), Meta insights, Google Ads (campaign daily), generic CSV engine (mapping, preview, row-level report, dedupe, idempotent import, saved mappings).
- `engine/connectors/NOTES.md`: per-provider doc URLs, 2026-10-14 check, versions, scopes, limits, unverified items.
- 63 tests in `engine/tests/connectors/` (all mocked; **nothing live-tested**).
### In Progress
- Nothing.
### Blocked (credentials / approvals / lead times; lead times are estimates, not published SLAs unless stated)
| Provider | Needed | Lead time |
|---|---|---|
| Shopify | Partner app (public, review) or custom app per merchant; `read_orders`, **`read_all_orders` (Shopify approval; without it only last 60 days)**; protected customer data Level 2 access for email/phone/zip (review for public apps); expiring offline tokens required for public apps by 2027-01-01 | Custom app: days. Public app + scope approvals: weeks (estimate) |
| Shiprocket | A dedicated **API user** per merchant (Settings > API, email not already on Shiprocket); no app review documented | Same day (estimate) |
| Meta | Meta developer app, `ads_read`; Standard access automatic, **Advanced access via App Review** + **Business Verification** for other businesses' ad accounts; token strategy (system user / long-lived) unverified | Weeks (estimate) |
| Google Ads | Cloud project + Ads API enabled, **developer token** from an MCC, OAuth client + consent screen, access level Explorer -> Basic (brand verification) -> Standard (manual audit; ~10 business days documented for Standard) | 1-4 weeks (estimate); Explorer is limited to 2,880 production ops/day |
| CSV marketplaces | Real sample exports (see NOTES.md) | Depends on user |
- Docker/Postgres unavailable: `PsycopgStore` SQL (upsert with `xmax = 0`, `#>>` max, csv_mappings upsert, `worker.queue.enqueue`) is UNEXECUTED; needs a run against real Postgres.
- Shiprocket list endpoint/pagination/login-token key come from secondary sources; first live call must confirm.
### Integration Needed
- **Main (web/API):** Shopify OAuth install + token refresh, Meta/Google OAuth connect flows, Shiprocket credential form, CSV upload endpoint (call `connectors.csv_import.preview` / `import_csv`), a worker handler `sync_<provider>` calling `registry.build_connector(...).run_sync()` and scheduling daily incremental runs; decide the AAD used with `app.crypto.encrypt/decrypt` (connectors accept any `aad`, default `b""`); webhook receivers (Shiprocket, Shopify) are out of scope.
- **Security decision for main:** customer hash mode. Implemented plain sha256 of normalised email/phone (as specified); HMAC with a pepper is supported (`identity_pepper`) but must then be used identically by every producer of `customer_key`. Unsalted phone hashes are brute-forceable.
- **Ledger:** payload shapes are provider-verbatim except (a) Shopify `email`/`phone` replaced by `customerIdentity{emailHash,phoneHash}`; (b) CSV rows are `{row, mapped, _import}`; money in Shopify/Meta payloads is decimal strings, Google `costMicros` is a string of micros. Convert to paise in ledger.
- No schema change needed. Optional: `integrations.sync_state jsonb` would replace the watermark derived from raw data (not requested).
### Metrics agent follow-up (2026-10-14): Postgres verification
- `PostgresRepository` verified against PostgreSQL 18.1 (db `metrics_agent`, migrations 0001-0011): every metric x group_by returns results identical to `InMemoryRepository` on the same hand-calculated fixtures (IST week boundaries via timestamptz, effective-dated COGS, api>estimated fees, tier values, tenant isolation). Tests in `engine/tests/metrics/test_postgres_integration.py`, auto-skipped without `DATABASE_URL`.
- `__MISSING_SKU__` items are treated as missing COGS (flagged, never zero silently).
- Perf (50k orders, one quarter, group_by channel): ~15s on this machine (about 4.5s SQL fetch of ids/items/fees, rest Python row assembly). Acceptable for MVP; if it matters, push date-range-scoped fetches for child tables and vectorise in the metric layer. No new index needed (orders (org_id, channel, ordered_date_ist), fees/items by order_id already exist).
- Legacy tier1..3 normalisation in the repository is kept (harmless).

### Connectors follow-up (2026-10-15)
- **Rename for ledger:** Shopify payloads now carry `customerIdentity{emailHash, phoneHash}` (HMAC-SHA256 from `app.identity`, key env `CUSTOMER_HASH_KEY`), replacing `emailSha256/phoneSha256/hmac`. Connectors no longer have their own hashing (`connectors/identity.py` removed). Raw email/phone are still never persisted. Use `app.identity.customer_key_from_hashes(emailHash, phoneHash)`.

### Connectors real-Postgres verification (2026-10-15)
- `engine/tests/connectors/test_pg_integration.py` (auto-skipped without `DATABASE_URL`) ran against real PostgreSQL 18.1 with migrations 0001-0011: first insert, identical re-sync (unchanged, no new job), changed record (updated + job), `integrations` status/last_synced_at/last_error/backfill_done, JSON-path watermark, csv_mappings save/load/upsert, CSV double import, two-org isolation, mixed-batch upsert counts. **All passed first time; the `xmax = 0` insert/update trick and `#>>` max work; no store bug found.** (Mocked HTTP; nothing live-tested.)
- Ledger shape check (`ledger.run_transform` over the connector's Shopify raw rows): **0 orders applied**, error `shopify order ...: created_at missing`.
#### Integration Needed (ledger adapter `transforms/shopify.py` vs real GraphQL payload; I did not edit ledger code)
The ledger adapter assumes the REST `Order` shape. Our verified GraphQL payload differs as follows:
1. `created_at` -> `createdAt`; `cancelled_at` -> `cancelledAt`; `fulfillment_status` ("fulfilled") -> `displayFulfillmentStatus` (enum, e.g. `FULFILLED`); `financial_status` -> `displayFinancialStatus` (`PAID`, `PARTIALLY_REFUNDED`...); `payment_gateway_names` -> `paymentGatewayNames`; `currency` -> `currencyCode`.
2. `line_items[]` -> `lineItems.nodes[]` (connection; `lineItems.pageInfo.hasNextPage` may be true); line `price` -> `originalUnitPriceSet.shopMoney.amount` (or `discountedUnitPriceSet`, after line discounts); `sku`, `quantity`, `title`, `name` unchanged.
3. Money is `<x>Set.shopMoney.amount` decimal strings: `total_price` -> `totalPriceSet`, `total_discounts` -> `totalDiscountsSet`, `total_tax` -> `totalTaxSet`, `total_shipping_price_set.shop_money.amount` -> `totalShippingPriceSet.shopMoney.amount`, plus `subtotalPriceSet`, `totalRefundedSet`.
4. Refunds: ledger reads `refunds[].transactions[] {kind,status,amount}`; GraphQL payload has `refunds[].totalRefundedSet.shopMoney.amount` (+ `refundLineItems.nodes`) and order-level `transactions[] {kind,status,amountSet}`. Use `totalRefundedSet` (order-level) or refund `totalRefundedSet`.
5. `taxes_included` is not in the payload (not requested; field not verified), so prices are treated as GST-inclusive with an `taxes_included_unknown` issue.
6. Identity: `email`/`phone`/`customer.email` are NOT persisted; use `customerIdentity.emailHash/phoneHash` (`app.identity.customer_key_from_hashes`). Ledger is already switching.
7. IDs: `id` is a global id `gid://shopify/Order/<n>` (orders.external_id will be the gid); `order_number` is absent (only `name`, e.g. `#1001`). `shopify_ref_keys` must also index the numeric tail of the gid so Shiprocket `channel_order_id` can link.
8. Timestamps are ISO-8601 UTC (`Z`); `shippingAddress` carries only `city, provinceCode, zip, countryCodeV2` (no phone, no names).
9. **Shiprocket:** the ledger needs `channel_order_id`, `freight_charges`, `cod_charges`, `rto_charges`, `cod_remitted_amount`, `courier_name`, `awb`, `is_return`, `delivered_date`. None of these field names is confirmed in Shiprocket's official docs (list-endpoint item shape unverified; Go SDK shows `id, order_id, channel_id, awb, status, courier, cod_charges, cost, ...`, i.e. `order_id` and `channel_id`, not `channel_order_id`). Needs a real sample response before either side commits to names.
## Frontend (agent/frontend, 2026-10-15)
### Done
- `web/` Next.js 16 (App Router, TS), Tailwind 4, shadcn-style primitives, Recharts, Supabase client. Pages: Overview, Profitability (channel/SKU/category/city/city tier/COD vs prepaid), Marketing (platform figures labelled "Platform-reported, not causal"), Anomalies, AI brief (incl. INSUFFICIENT_DATA state), COGS editor, Integrations, Login.
- `web/lib/api/` typed client + runtime MetricResult validation; `web/lib/mocks/` separate mock layer via `NEXT_PUBLIC_USE_MOCKS` (default true); mocks labelled "Sample data" in banner and header.
- Verified: vitest 33 passed, tsc clean, eslint clean, `npm run build` ok, all 8 routes load in headless browser with 0 console errors (mock mode).
### In Progress / Unverified
- Live mode (real FastAPI + Supabase) untested: no backend, no Supabase credentials. COGS upsert and integrations select unverified against a real RLS database.
### Integration Needed (contract change requests)
1. Metrics `ad_spend` and `platform_reported_revenue` (not in the canonical list); group keys = ad platform (`meta`, `google_ads`).
2. `group_by=city_tier` (enum `metro|tier_1|tier_2|tier_3|unknown`) alongside `city`.
3. New endpoints: `GET /anomalies?org_id` (id, detected_at, severity low|medium|high|critical, metric, unit, observed, expected, summary, drivers[{dimension,key,detail,share 0..1}], evidence[{call_id,description}]) and `GET /briefs/latest?org_id` (CONTRACTS s6/s7 shape or `{status:"INSUFFICIENT_DATA",reason,fallback}`); shapes in `web/lib/api/types.ts`.
4. `filters` query param assumed JSON-encoded object `{"channel":["shopify"]}`; confirm.
5. API CORS must allow the web origin; WoW % is computed in `web/lib/presentation.ts` from two API values (ask for an API field if preferred).

### Ledger: Shopify GraphQL adapter (2026-10-15)
- `transforms/shopify.py` now reads the connectors' GraphQL Order node (createdAt, lineItems.nodes, MoneyBag `shopMoney.amount`, order-level `totalRefundedSet` with refunds[]/transactions[] fallbacks, `paymentGatewayNames`, `displayFulfillmentStatus`, gid -> numeric-tail `external_id`, `customerIdentity` hashes). The REST path was removed. `shopify_ref_keys` indexes gid tail, `name` (no '#') and `order_number` so Shiprocket `channel_order_id` links.
- Assumptions surfaced as issues (not invented): `taxes_included_unknown` (GST-inclusive assumed; `taxesIncluded` honoured if ever present), `line_items_truncated` / `refunds_truncated`, `field_missing: <MoneyBag>`, `total_mismatch`, `customer_key_missing`. Non-INR currency raises.
- Shiprocket adapter still UNCONFIRMED field names; absent cost fields raise `shiprocket_cost_fields_absent`; a payload with `order_id`/`channel_id` but no `channel_order_id` errors with a hint.
- `tests/ledger/test_connector_shape.py` feeds the connectors' Shopify fixtures (via `sanitize_order`) through `run_transform` in memory and on real Postgres: orders, items, refunds, COD vs prepaid, customer_key (== app.identity). Full suite with DATABASE_URL: 249 passed, 1 xpassed. **The connectors' `test_ledger_run_transform_consumes_shopify_raw` now XPASSES; main can remove its xfail.**
