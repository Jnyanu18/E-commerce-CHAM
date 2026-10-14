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
- Docker unavailable: migrations require real PostgreSQL/Supabase verification (unverified, e.g. generated column `ordered_date_ist`, column-level grants, `claim_job()`)
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
- **Ledger:** payload shapes are provider-verbatim except (a) Shopify `email`/`phone` replaced by `customerIdentity{emailSha256,phoneSha256,hmac}`; (b) CSV rows are `{row, mapped, _import}`; money in Shopify/Meta payloads is decimal strings, Google `costMicros` is a string of micros. Convert to paise in ledger.
- No schema change needed. Optional: `integrations.sync_state jsonb` would replace the watermark derived from raw data (not requested).
