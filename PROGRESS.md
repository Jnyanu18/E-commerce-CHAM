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
