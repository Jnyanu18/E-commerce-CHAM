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

## Ledger merge and GST rules (2026-10-14, agent: ledger)
- **Decision:** (1) `gross_revenue_paise` is GST-inclusive item value before discounts; `taxes_paise` is the GST contained in (gross - discounts + shipping); tax-exclusive sources have tax added to gross. (2) Order status merges by fixed precedence placed<shipped<delivered<cancelled<returned<rto, so any arrival order converges. (3) Fee precedence per (order, type): api > csv > estimated; actuals delete lower rows, estimates are skipped when an actual exists. (4) Shipments are partial patches that never create orders; unlinked ones are deferred, never guessed. (5) Missing SKU -> `__MISSING_SKU__` placeholder plus surfaced issue. (6) Reconciliation never adjusts; it reports.
- **Alternatives:** timestamp-based last-write-wins status; coexisting estimated and actual fee rows with metrics choosing; dropping SKU-less lines; ex-GST gross.
- **Reason:** order-independence and idempotency without extra columns; avoid double-counted fees; no silent data loss.
- **Impact:** metrics must read fees as stored (one row per type) and treat the placeholder SKU as unknown COGS. Status precedence means a later cancellation of a delivered order is not reflected until revisited.
- **Date:** 2026-10-14 **Agent:** ledger
