# AI Agent launch prompt

Copy everything below into a fresh Claude Code session started in `worktrees/ai`.

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
You are the **AI Agent**.

## Worktree and branch
- Worktree: `worktrees/ai` (absolute: `C:\Users\jnyan\Projects\Business_AI_Analytics\worktrees\ai`)
- Branch: `agent/ai`
- Start by running `git status` and `git branch --show-current`; stop if the branch is not `agent/ai`.

## Owned paths
`engine/ai/` (add `__init__.py` files and tests)

## Must NOT modify
`engine/metrics/`, `supabase/`, `web/`, `engine/intelligence/`, `engine/connectors/`, `engine/ledger/`, main-owned `engine/app/`, `engine/worker/`, `engine/seed/`, without an explicit contract change.
Do not edit the metric layer, intelligence code, schema or UI. Email delivery and scheduling are main's integration work; expose a clean `generate_brief(org_id, week_start)` function for it to call.

## Read first (in addition to the five documents above)
`docs/CONTRACTS.md` sections 3, 5, 6, 7, `supabase/migrations/0003_intelligence.sql` (`insights`, `recommendations`), `CLAUDE.md` (brief rules). Consult the claude-api skill/current Anthropic docs for tool-use and model details before coding.

## Dependencies
Upstream: metrics (tool wrappers call the metric layer) and intelligence (reads recommendations and diagnostics). Use deterministic mocks until they merge.

## Deliverables
1. **Tool definitions**: one tool per canonical metric (same parameters as the metric functions: date range, filters, group_by) plus read-only tools for recommendations, experiments and model diagnostics. **There is no SQL tool and no free-form data tool.** Tool inputs are validated (schema, allowed enums, date bounds, org scoping fixed server-side so the model cannot choose another org).
2. **Tool executor** that calls the metric layer (behind an interface you can mock), returns `MetricResult` plus a `call_id`, and records every call (inputs, outputs, timestamp) for the audit trail stored in `insights.tool_calls`.
3. **Weekly brief generation** with the Claude API: what happened, why (from the anomaly decomposition), top 3 actions with expected rupee impact and confidence, experiment proposals. Output markdown.
4. **Traceability enforcement**: every number in the brief carries a citation `[call_id]` that resolves to a stored tool result containing that value (metric, date range, filters/grouping). Implement a validator that parses the brief and rejects: uncited numbers, numbers not found in the cited result, intervals/confidence not present in a result, claims about missing data not flagged. On failure, retry with feedback a bounded number of times, then fall back to a clearly marked data-only summary. Weak or INSUFFICIENT_DATA model output must be stated plainly, never dressed up.
5. **Recommendation explanations** in plain language from stored recommendation payloads only.
6. Model ids and prompt text in config, not scattered; prompts versioned; no secrets in code (`ANTHROPIC_API_KEY` from env).

## Interfaces / contracts to implement
Claude -> Tool -> Metric Layer -> Validated result -> Claude. Brief rules from `docs/CONTRACTS.md` section 7.

## You may mock
The Claude API client (fake client returning scripted tool-use turns) and the metric layer (deterministic fixture tools). Test the validator against both good and adversarial briefs (invented numbers, wrong citations, fake confidence intervals).

## You must never fake
Never claim the live Claude integration is validated without a real API test run. Never let the model see raw data outside tool results. Never invent numbers, intervals or confidence.

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
