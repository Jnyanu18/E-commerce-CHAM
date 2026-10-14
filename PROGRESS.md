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
