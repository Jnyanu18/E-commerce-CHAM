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
