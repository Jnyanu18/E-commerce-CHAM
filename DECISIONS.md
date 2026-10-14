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

## Database agent round 1 (2026-10-14, agent: database)
- **Decision:** City tier stays `text` + CHECK (`metro|tier_1|tier_2|tier_3|unknown`), `orders.city_tier` NOT NULL default `'unknown'` (a trigger turns explicit NULL into `'unknown'`). **Alternatives:** native Postgres ENUM; nullable column. **Reason:** extending a CHECK is one statement while `ALTER TYPE` is awkward inside transactions; non-null avoids a NULL-vs-unknown group-by split in metrics. **Impact:** ledger/seed must emit the new values (`engine/seed/synthetic.py` still emits `tier1..3`). **Date:** 2026-10-14 **Agent:** database
- **Decision:** Settlements as three tables: `settlements` (header; unique `(org_id, channel, external_id)`), `settlement_items` (per-order lines; unique `(settlement_id, order_external_id, line_type)`, nullable `order_id` for unmatched lines) plus a `settlement_reconciliation` view (security_invoker), and a separate `shipments` table (unique `(org_id, source, external_id)`, partial unique `(org_id, awb)`). **Alternatives:** one flat settlements table with an order reference; shipment fields on `orders`/`returns`. **Reason:** a payout covers many orders and an order can appear in several payouts (sale then refund), so lines need their own table; freight cost and AWB/carrier lifecycle are courier facts needed to reconcile Shiprocket and do not belong in the order row. Composite FKs `(org_id, order_id)` make cross-tenant links impossible. **Impact:** additive; `orders` gains `unique (org_id, id)`. COD remittances can be settlements with channel `shiprocket_cod`. Shipment status vocabulary is our own coarse set (Shiprocket's real status strings were NOT verified; the connector/ledger must map). **Date:** 2026-10-14 **Agent:** database
- **Decision:** `claim_job` now returns `SETOF jobs`; added `reap_stale_jobs(interval)` and `jobs.reaped_count`; both functions are EXECUTE-revoked from public/anon/authenticated. **Alternatives:** keep composite return; add a worker-id parameter. **Reason:** zero rows is the natural empty result and `queue.py` already handles `fetchone() is None`; an extra parameter (even defaulted) would make 1-arg calls ambiguous. **Impact:** no change needed in queue.py; someone (main) must call `reap_stale_jobs()` periodically. **Date:** 2026-10-14 **Agent:** database
- **Decision:** Revoke all table privileges from `anon` and non-SELECT privileges from `authenticated` on everything except the five user-editable tables (0011). **Alternatives:** rely on RLS alone. **Reason:** Supabase default grants give ALL; privilege errors fail loudly and TRUNCATE bypasses RLS. **Impact:** none for service role / direct connections. **Date:** 2026-10-14 **Agent:** database
- **Decision:** No client insert path for `orgs`/`org_members`; onboarding stays service-role. **Reason:** MVP pilots are onboarded by us; avoids a self-join-any-org hole. **Date:** 2026-10-14 **Agent:** database
- **Decision:** City tier seed is a migration (0010) with an explicit non-authoritative source note. **Reason:** must exist in every environment; plain data, upserted by city. **Date:** 2026-10-14 **Agent:** database
