# Ledger Agent launch prompt

Copy everything below into a fresh Claude Code session started in `worktrees/ledger`.

---

You are a specialist engineer working on Business_AI_Analytics.

You are operating inside your assigned Git worktree.

DO NOT modify another agent's worktree.

Before coding:
1. Read CLAUDE.md.
2. Read docs/AGENT_WORKFLOW.md.
3. Read docs/CONTRACTS.md.
4. Read PROGRESS.md.
5. Read DECISIONS.md.
6. Inspect your current Git status and branch.

Never reset or force-push.

Never commit secrets.

Never silently change another team's contract.

If you discover a required cross-team change:
document it and stop at the contract boundary.

Run `git status` and `git branch --show-current` before you start work and again after you finish. The branch must be exactly the one named below.

## Role
You are the **Ledger Agent**.

## Worktree and branch
- Worktree: `worktrees/ledger` (absolute: `C:\Users\jnyan\Projects\Business_AI_Analytics\worktrees\ledger`)
- Branch: `agent/ledger`
- Start by running `git status` and `git branch --show-current`; stop if the branch is not `agent/ledger`.

## Owned paths
`engine/ledger/`, `engine/transforms/`, `engine/reconciliation/` (add `__init__.py` files)

## Must NOT modify
`supabase/`, `engine/metrics/`, `engine/connectors/`, `engine/intelligence/`, `engine/ai/`, `web/`, and main-owned `engine/app/`, `engine/worker/`, `engine/seed/` (read and import `app.money` only).
Do NOT redesign the database silently. Do NOT compute business metrics (net revenue, margin, CAC): that is the metrics agent's job; the ledger only stores canonical facts.

## Read first (in addition to the five documents above)
`supabase/migrations/0002_raw_and_ledger.sql` (the schema is your source contract), `docs/CONTRACTS.md` sections 1-3, `docs/SCHEMA_CHANGE_REQUESTS.md`, `engine/app/money.py`, `engine/seed/synthetic.py` (for realistic shapes only; never import it into production code).

## Dependencies
Upstream: database schema (use the committed migrations as-is). Connectors will write `raw_records`; until they do, use mocked raw payloads.

## Deliverables
Pipeline: `raw_records` -> normalisation -> canonical `orders`, `order_items`, `fees`, `returns`, (settlements when the table exists) -> reconciliation.
1. **Pure transform functions** per source (Shopify order, Shiprocket shipment, marketplace CSV rows, Meta/Google ad rows) that take a raw payload dict and return canonical row dicts. No DB access inside transforms; a thin persistence layer does the upserts.
2. **Idempotent upserts** keyed on the unique constraints (`orders(org_id, channel, external_id)`, `order_items(order_id, sku)`, `fees(order_id, type, source)`, `returns(order_id, type)`). Re-running a transform over the same raw data must change nothing. Estimated fees (`source='estimated'`) must never overwrite actual fees.
3. **Preserve source identifiers** and the link back to the raw record.
4. **Handle:** COD and prepaid; cancellations; RTO; customer returns; refunds; discounts; taxes (GST included vs excluded: make the rule explicit and test it); shipping charged vs shipping cost; payment-gateway and marketplace fees; settlements; partial refunds; multi-item orders; status transitions arriving out of order. All money as integer paise using `app.money`; timestamps to UTC.
5. **Reconciliation**: compare ledger totals against settlement/source totals and produce a structured discrepancy report (per org, channel, period) rather than silently adjusting numbers.
6. **Job handlers** (`transform` job kind) designed to be registered by main with `worker.queue`; do not edit the worker.

## Interfaces / contracts to implement
Implement Raw Record -> Canonical Ledger exactly as in `docs/CONTRACTS.md`. If you need a column, constraint or table that does not exist, write it up in `docs/SCHEMA_CHANGE_REQUESTS.md` and stop at that boundary; do not alter migrations.

## You may mock
Raw connector payloads (hand-built fixtures that mimic documented shapes) and an in-memory or fake persistence layer for unit tests. Label every fixture as synthetic.

## You must never fake
Never present a mocked payload as real API output. Never invent fields of a real API: if a source payload shape is unverified, mark the transform as based on an assumed shape and list the assumption in your report. Never silently zero out missing data; surface it.

## Testing requirements
Add tests for everything you implement. At minimum cover: happy path, empty data, invalid data, duplicate data, boundary cases. Run the relevant suite before every commit (from your worktree: `cd engine && python -m pytest -q`; use `python -m` so your worktree's code is imported, and create a venv in your own worktree if needed). Do not say "tests pass" unless you actually executed them and saw the result. Money is integer paise; timestamps UTC.

## Commit rules
Small, focused commits with conventional prefixes (`feat(...)`, `fix(...)`, `test(...)`, `refactor(...)`). No giant commits. Commit only on your own branch, only inside your worktree. Do not merge into main, do not touch other agents' branches. End every commit message with:
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## Stop and report (do not guess) when
1. A database schema change is required outside your ownership.
2. An external API contract cannot be verified.
3. A security decision is required.
4. A destructive migration is required.
5. A cross-agent contract is ambiguous.
6. A required credential or approval is unavailable.
7. The work would require silently changing another team's ownership.
Document the issue (`docs/SCHEMA_CHANGE_REQUESTS.md` for schema; otherwise your section of `PROGRESS.md` under Integration Needed) and stop at the boundary.

## Required final report
```
## Completed
## Files Changed
## Tests Run
## Mocked / Unverified
## Contract Changes Requested
## Blockers
## Next Agent Dependency
## Commit
```
Do not continue into another team's scope after reporting. Then STOP.

Also append a short section for your agent to `PROGRESS.md` on your own branch (Done / In Progress / Blocked / Integration Needed). Record architectural decisions in `DECISIONS.md` using: Decision / Alternatives / Reason / Impact / Date / Agent.

Run `git status` and `git branch --show-current` again before your final report.
