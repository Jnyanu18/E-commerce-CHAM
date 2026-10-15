# Decisions

| # | Decision | Alternatives | Reason |
|---|----------|--------------|--------|
| 1 | Single generic `raw_records(source, external_id, payload)` instead of one `raw_*` table per source | Per-source tables | Same re-derivability, one idempotent upsert path, fewer migrations. Cheap to split later. |
| 2 | `sku_cogs` effective-dated table separate from `skus` | `cogs` column on `skus` | Spec requires effective dates; keeps history for accurate past-period margin. |
| 3 | `org_id` denormalised onto `order_items`, `fees`, `returns` | Join via `orders` in RLS | Simple, fast RLS policies. |
| 4 | Added `customer_key` (hashed) on `orders` | Store raw email/phone | Needed for `blended_cac` (new customers) without holding PII in the ledger. |
| 5 | Added `traffic_daily` | Skip | Required for sessions x CVR x AOV anomaly decomposition. |
| 6 | Added `city_tiers` reference + `orders.city_tier` | Derive on the fly | RTO rate segment is category x city tier x payment method. |
| 7 | Added `refunds_paise` on `orders`, `refund`-free `returns.cost_paise` | Single field | `net_revenue` subtracts refunds; return cost is the separate logistics loss. Avoids double counting. |
| 8 | `jobs` + `claim_job()` (SKIP LOCKED); no Redis | Redis/Celery | Per spec; one less service. |
| 9 | Credentials: app-level AES-GCM into `credentials_enc bytea`; column not selectable by browser roles | pgcrypto | Key stays out of the DB. |
| 10 | Browser can write only skus, sku_cogs, csv_mappings, experiments, recommendations; all pipeline tables service-role only | Broader writes | Smallest attack surface. |

## Multi-agent workspace (2026-10-14, agent: main)
- **Decision:** Keep the existing repo directory as the primary worktree (branch `main`); create 7 `agent/*` branches as real worktrees in `./worktrees/`, excluded via `.git/info/exclude`.
- **Alternatives:** Restructure into `Business_AI_Analytics/main/` + `worktrees/`; sibling directory for worktrees.
- **Reason:** Moving the primary checkout means relocating the live directory; the nested layout gives the same `worktrees/<name>` paths with no move. Nesting is safe because the folder is excluded from git.
- **Impact:** Primary path unchanged. Can still be restructured later with `git worktree move`.
- **Also:** Initial history created (3 commits) from the untracked work; default branch renamed `master` -> `main`.

## Round 1 shared conventions (2026-10-14, agent: main)
- **Decision:** Canonical `MetricResult` uses `value/unit/currency/period/filters/group_by/data_quality`; city tier enum is `metro|tier_1|tier_2|tier_3|unknown`; pincode is text. Recorded in `docs/CONTRACTS.md` sections 3 and 8.
- **Alternatives:** keep the earlier `value_paise/date_range/caveats` shape and `tier1..tier3` enum.
- **Reason:** The Round 1 build briefs specify these; settling them in the shared contract before eight agents start prevents divergence.
- **Impact:** Database agent must migrate the tier enum; ledger and metrics use the new values. Earlier launch prompts reference the old names only in passing; the contract wins.
- **Date:** 2026-10-14 **Agent:** main

## Metric layer semantics (2026-10-14, agent: metrics)
- **Decision:** (1) Cancelled orders are excluded everywhere; orders with status `rto` contribute no gross/discounts/taxes/refunds/COGS (sale never realised) but still carry fees, shipping and RTO return cost. (2) Shipping charged to customers is not part of net_revenue. (3) `shipping cost` = fees of type `shipping`; `fees` = all other fee types; per (order, type) the source priority is api > csv > estimated and estimated use is flagged. (4) New customer = customer_key whose earliest non-cancelled order (ordered_at, id) across all channels falls in the period. (5) RTO rate = rto / (delivered+returned+rto) over the trailing 180 days, ladder category x tier x payment -> tier x payment -> payment, min 30 resolved orders; order value = gross - discounts; order category = highest-value line. (6) Order-level amounts are split across SKU/category groups by line value with largest-remainder rounding so groups sum exactly. (7) Ad-spend metrics are group-able by week only (ROAS also platform). (8) Empty/undefined results are `value: null`, status `insufficient`.
- **Alternatives:** Count rto revenue and rely on refunds_paise; allocate ad spend to channels pro rata; ratios as floats.
- **Reason:** Avoids overstating revenue for undelivered COD, double counting refunds, and fake attribution of spend.
- **Impact:** Ledger must set status/refunds consistently; API/AI inherit these definitions via the registry.
- **Date:** 2026-10-14 **Agent:** metrics
## Database agent round 1 (2026-10-14, agent: database)
- **Decision:** City tier stays `text` + CHECK (`metro|tier_1|tier_2|tier_3|unknown`), `orders.city_tier` NOT NULL default `'unknown'` (a trigger turns explicit NULL into `'unknown'`). **Alternatives:** native Postgres ENUM; nullable column. **Reason:** extending a CHECK is one statement while `ALTER TYPE` is awkward inside transactions; non-null avoids a NULL-vs-unknown group-by split in metrics. **Impact:** ledger/seed must emit the new values (`engine/seed/synthetic.py` still emits `tier1..3`). **Date:** 2026-10-14 **Agent:** database
- **Decision:** Settlements as three tables: `settlements` (header; unique `(org_id, channel, external_id)`), `settlement_items` (per-order lines; unique `(settlement_id, order_external_id, line_type)`, nullable `order_id` for unmatched lines) plus a `settlement_reconciliation` view (security_invoker), and a separate `shipments` table (unique `(org_id, source, external_id)`, partial unique `(org_id, awb)`). **Alternatives:** one flat settlements table with an order reference; shipment fields on `orders`/`returns`. **Reason:** a payout covers many orders and an order can appear in several payouts (sale then refund), so lines need their own table; freight cost and AWB/carrier lifecycle are courier facts needed to reconcile Shiprocket and do not belong in the order row. Composite FKs `(org_id, order_id)` make cross-tenant links impossible. **Impact:** additive; `orders` gains `unique (org_id, id)`. COD remittances can be settlements with channel `shiprocket_cod`. Shipment status vocabulary is our own coarse set (Shiprocket's real status strings were NOT verified; the connector/ledger must map). **Date:** 2026-10-14 **Agent:** database
- **Decision:** `claim_job` now returns `SETOF jobs`; added `reap_stale_jobs(interval)` and `jobs.reaped_count`; both functions are EXECUTE-revoked from public/anon/authenticated. **Alternatives:** keep composite return; add a worker-id parameter. **Reason:** zero rows is the natural empty result and `queue.py` already handles `fetchone() is None`; an extra parameter (even defaulted) would make 1-arg calls ambiguous. **Impact:** no change needed in queue.py; someone (main) must call `reap_stale_jobs()` periodically. **Date:** 2026-10-14 **Agent:** database
- **Decision:** Revoke all table privileges from `anon` and non-SELECT privileges from `authenticated` on everything except the five user-editable tables (0011). **Alternatives:** rely on RLS alone. **Reason:** Supabase default grants give ALL; privilege errors fail loudly and TRUNCATE bypasses RLS. **Impact:** none for service role / direct connections. **Date:** 2026-10-14 **Agent:** database
- **Decision:** No client insert path for `orgs`/`org_members`; onboarding stays service-role. **Reason:** MVP pilots are onboarded by us; avoids a self-join-any-org hole. **Date:** 2026-10-14 **Agent:** database
- **Decision:** City tier seed is a migration (0010) with an explicit non-authoritative source note. **Reason:** must exist in every environment; plain data, upserted by city. **Date:** 2026-10-14 **Agent:** database
## Ledger merge and GST rules (2026-10-14, agent: ledger)
- **Decision:** (1) `gross_revenue_paise` is GST-inclusive item value before discounts; `taxes_paise` is the GST contained in (gross - discounts + shipping); tax-exclusive sources have tax added to gross. (2) Order status merges by fixed precedence placed<shipped<delivered<cancelled<returned<rto, so any arrival order converges. (3) Fee precedence per (order, type): api > csv > estimated; actuals delete lower rows, estimates are skipped when an actual exists. (4) Shipments are partial patches that never create orders; unlinked ones are deferred, never guessed. (5) Missing SKU -> `__MISSING_SKU__` placeholder plus surfaced issue. (6) Reconciliation never adjusts; it reports.
- **Alternatives:** timestamp-based last-write-wins status; coexisting estimated and actual fee rows with metrics choosing; dropping SKU-less lines; ex-GST gross.
- **Reason:** order-independence and idempotency without extra columns; avoid double-counted fees; no silent data loss.
- **Impact:** metrics must read fees as stored (one row per type) and treat the placeholder SKU as unknown COGS. Status precedence means a later cancellation of a delivered order is not reflected until revisited.
- **Date:** 2026-10-14 **Agent:** ledger
## Connectors (2026-10-14, agent: connectors)
- **Decision:** Incremental watermark is derived from stored raw data (`max(payload->updatedAt / date_start / segments.date)` minus an overlap window) instead of a cursor column. **Alternatives:** add `integrations.sync_state jsonb`; keep the cursor inside the encrypted credentials blob. **Reason:** no schema change is needed, re-fetch is idempotent, and the state cannot drift from the data. **Impact:** crashed runs resume safely; a schema column can replace this later. **Date:** 2026-10-14 **Agent:** connectors
- **Decision:** Shopify email/phone are fetched only to compute sha256 of the normalised value and are dropped from the stored payload (added as `customerIdentity`); names/street addresses are never requested; city, provinceCode, zip, country are kept (needed for city tier/pincode). **Alternatives:** store verbatim; store nothing. **Reason:** data minimisation (Shopify protected-customer-data Level 1 principle 1) while keeping `customer_key` for blended CAC. **Impact:** deviates from "verbatim payload" for two fields; contract note in PROGRESS.md. Unsalted hashes are weak for phones (see Integration Needed). **Date:** 2026-10-14 **Agent:** connectors
- **Decision:** Pull ad-platform data with a trailing 14-day re-pull; raw rows are upserted so revisions overwrite. **Alternatives:** append snapshots. **Reason:** attribution/cost revisions after the fact; `(org_id, source, external_id)` uniqueness. **Impact:** raw keeps only the latest version of each day row. **Date:** 2026-10-14 **Agent:** connectors
- **Decision:** CSV import is all-or-nothing by default (`allow_partial=False`). **Alternatives:** import valid rows always. **Reason:** avoids silent partial books; re-upload of a corrected file is idempotent. **Impact:** UI should show the row-level report. **Date:** 2026-10-14 **Agent:** connectors

## Connectors: customer hashing resolved (2026-10-15, agent: connectors)
- **Decision:** Shopify uses `app.identity.hash_email/hash_phone` (HMAC-SHA256, mandatory `CUSTOMER_HASH_KEY`); payload keys renamed `emailHash/phoneHash`; connector-local sha256/pepper code deleted. **Alternatives:** keep unsalted sha256 (rejected by main: brute-forceable phones; also my phone normalisation did not join the ledger's). **Reason:** security decision resolved by main; one canonical implementation. **Impact:** `CUSTOMER_HASH_KEY` must be set wherever a Shopify sync runs; key rotation changes every customer_key. **Date:** 2026-10-15 **Agent:** connectors
## Ledger customer_key delegates to app.identity (2026-10-15, agent: ledger)
- **Decision:** `transforms.common.customer_key` performs no hashing. Precomputed `customerIdentity{emailHash, phoneHash}` -> `app.identity.customer_key_from_hashes`; raw email/phone (CSV) -> `app.identity.customer_key`. Legacy `emailSha256/phoneSha256` keys are treated as absent.
- **Alternatives:** keep ledger-local sha256 with last-10-digit phone logic.
- **Reason:** one canonical keyed HMAC so ledger keys join with connector keys; plain sha256 of phones is brute-forceable.
- **Impact:** requires `CUSTOMER_HASH_KEY` when raw email/phone are hashed (tests get it from `engine/tests/conftest.py`). Changing the key changes every customer_key.
- **Date:** 2026-10-15 **Agent:** ledger
