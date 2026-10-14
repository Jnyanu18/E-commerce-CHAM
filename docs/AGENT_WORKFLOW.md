# Agent Workflow

`main` is the integration branch. Each specialist works only in its own worktree on its own branch.

| Agent | Branch | Worktree | Owns |
|---|---|---|---|
| database | agent/database | worktrees/database | `supabase/` (migrations, RLS, indexes, constraints, functions, seed schema) |
| ledger | agent/ledger | worktrees/ledger | `engine/ledger/`, `engine/transforms/`, `engine/reconciliation/` |
| metrics | agent/metrics | worktrees/metrics | `engine/metrics/` + metric tests |
| frontend | agent/frontend | worktrees/frontend | `web/` |
| connectors | agent/connectors | worktrees/connectors | `engine/connectors/` |
| intelligence | agent/intelligence | worktrees/intelligence | `engine/intelligence/`, `engine/models/`, `engine/anomaly/` |
| ai | agent/ai | worktrees/ai | `engine/ai/` |

Pre-existing scaffold (`engine/app/`, `engine/worker/`, `engine/seed/`, `docker-compose.yml`) is owned by main until assigned.

## Rules
- Never edit another agent's worktree or owned paths. Need a change there? Write it up (schema: `docs/SCHEMA_CHANGE_REQUESTS.md`; anything else: `PROGRESS.md` > Integration Needed).
- Shared files (`CLAUDE.md`, `DECISIONS.md`, `PROGRESS.md`, `docs/CONTRACTS.md`) are not rewritten casually. Append to your own section; architectural changes go to main for resolution.
- Do not block on upstream work: code against `docs/CONTRACTS.md` with mocks.
- Dependency order: database -> ledger -> metrics -> intelligence -> ai. Frontend and connectors run in parallel against contracts.

## Hard invariants
Orders are the source of truth. One canonical implementation per metric (metrics agent). No business math in React. Claude never gets raw SQL. Weak data returns `INSUFFICIENT_DATA`, not fake confidence. RLS on every tenant table. Money = integer paise; timestamps UTC, shown IST.

## Commits
Small and focused, conventional prefixes: `feat(db):`, `feat(ledger):`, `feat(metrics):`, `feat(ui):`, `feat(connectors):`, `feat(intelligence):`, `feat(ai):`, `test(metrics):`. End each message with the Co-Authored-By trailer.

## Merging into main
Not by specialists unless told. Before merging: `git diff main...agent/X`, `git log main..agent/X`, run relevant tests. Never merge failing work. Pull main into your branch with `git merge main` in your own worktree.

## Done checklist
implementation -> unit tests -> integration tests where applicable -> lint/type check -> commit.

## DECISIONS.md entry format
Decision / Alternatives / Reason / Impact / Date / Agent.

## PROGRESS.md format
Sections: Done, In Progress, Blocked, Integration Needed; one sub-heading per agent, meaningful items only.
