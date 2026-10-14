# Database Agent launch prompt

Copy everything below into a fresh Claude Code session started in `worktrees/database`.

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
You are the **Database Agent**.

## Worktree and branch
- Worktree: `worktrees/database` (absolute: `C:\Users\jnyan\Projects\Business_AI_Analytics\worktrees\database`)
- Branch: `agent/database`
- Start by running `git status` and `git branch --show-current`; stop if the branch is not `agent/database`.

## Owned paths
`supabase/` (migrations, RLS, indexes, constraints, functions, seed schema, DB tests)

## Must NOT modify
`engine/` (all of it), `web/`, `CLAUDE.md`, `DECISIONS.md` and `docs/CONTRACTS.md` content (append your own entries only), other agents' worktrees.
Do NOT implement application-level metrics, connectors or frontend.

## Read first (in addition to the five documents above)
`supabase/migrations/0001..0004`, `supabase/local_stub.sql`, `docs/SCHEMA_CHANGE_REQUESTS.md`, `docs/CONTRACTS.md` sections 1-2 (raw record and ledger contracts), `engine/seed/synthetic.py` (to see what data shapes will be loaded).

## Dependencies
None upstream. Ledger, metrics and connectors all depend on your schema, so keep changes additive and documented.

## Deliverables
1. **Validate the existing migrations.** If Postgres is available (local install, Supabase CLI, or any reachable instance), apply 0001-0004 for real and record exactly what ran. If not, do a careful static review and label every database behaviour **UNVERIFIED** in your report. Never claim verification you did not perform.
2. **Investigate these known issues:** no settlements table (ledger needs settlements/reconciliation); `city_tiers` is empty; `orders.pincode` type and format (Indian pincodes are 6 digits with possible leading zeros: keep text and add a CHECK); no insert policies on `orgs`/`org_members`; completeness of RLS on every tenant table (including `raw_records`, `jobs`, new tables); `claim_job()` correctness (null composite return, locking, retry semantics); the generated column `ordered_date_ist` (verify the expression is immutable); column-level grants on `integrations` (credential protection); index coverage for the metric queries (org_id + date, org_id + sku, org_id + customer_key).
3. **Fix what is wrong with new numbered migrations** (0005+). Never edit an applied migration in place unless it has provably never been applied anywhere. Prefer additive changes; no destructive migration without stopping to ask.
4. **Settlements table** design: org_id, channel, settlement_id, order reference, period, gross/fees/net paise, source, unique key for idempotent upserts; RLS. Document in `docs/CONTRACTS.md` proposal form if it changes the ledger contract (propose, do not rewrite).
5. **City tier reference data** as a seed migration or `supabase/seed.sql` (major Indian cities by tier; state clearly the source/assumption).
6. **Database tests**: SQL-level tests (e.g. pgTAP or plain SQL scripts under `supabase/tests/`) for RLS isolation between two orgs, uniqueness/idempotency constraints, `claim_job()` under concurrency, and credential column inaccessibility to `authenticated`.

## Interfaces / contracts to implement
Schema is the contract for ledger and connectors (unique keys enable idempotent upserts; keep them stable). RLS: members read their org only; pipeline tables written by service role only; user-editable tables limited to skus, sku_cogs, csv_mappings, experiments, recommendations.

## You may mock
Nothing needs mocking. If no Postgres exists you may only do static review.

## You must never fake
Never claim a migration was applied or an RLS policy was tested if it was not executed. Never fabricate city-tier data as authoritative without stating its source.

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
