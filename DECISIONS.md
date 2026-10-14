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

## Connectors (2026-10-14, agent: connectors)
- **Decision:** Incremental watermark is derived from stored raw data (`max(payload->updatedAt / date_start / segments.date)` minus an overlap window) instead of a cursor column. **Alternatives:** add `integrations.sync_state jsonb`; keep the cursor inside the encrypted credentials blob. **Reason:** no schema change is needed, re-fetch is idempotent, and the state cannot drift from the data. **Impact:** crashed runs resume safely; a schema column can replace this later. **Date:** 2026-10-14 **Agent:** connectors
- **Decision:** Shopify email/phone are fetched only to compute sha256 of the normalised value and are dropped from the stored payload (added as `customerIdentity`); names/street addresses are never requested; city, provinceCode, zip, country are kept (needed for city tier/pincode). **Alternatives:** store verbatim; store nothing. **Reason:** data minimisation (Shopify protected-customer-data Level 1 principle 1) while keeping `customer_key` for blended CAC. **Impact:** deviates from "verbatim payload" for two fields; contract note in PROGRESS.md. Unsalted hashes are weak for phones (see Integration Needed). **Date:** 2026-10-14 **Agent:** connectors
- **Decision:** Pull ad-platform data with a trailing 14-day re-pull; raw rows are upserted so revisions overwrite. **Alternatives:** append snapshots. **Reason:** attribution/cost revisions after the fact; `(org_id, source, external_id)` uniqueness. **Impact:** raw keeps only the latest version of each day row. **Date:** 2026-10-14 **Agent:** connectors
- **Decision:** CSV import is all-or-nothing by default (`allow_partial=False`). **Alternatives:** import valid rows always. **Reason:** avoids silent partial books; re-upload of a corrected file is idempotent. **Impact:** UI should show the row-level report. **Date:** 2026-10-14 **Agent:** connectors
