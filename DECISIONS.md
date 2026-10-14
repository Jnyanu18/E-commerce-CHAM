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
