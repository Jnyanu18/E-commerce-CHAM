# Business AI Analytics (CLAUDE.md)

Product name is still `[PRODUCT_NAME]` (unset). This file is the authoritative brief and loads every session. Workflow detail: `docs/AGENT_WORKFLOW.md`. Interfaces: `docs/CONTRACTS.md`.

## Product
AI analytics platform for Indian D2C brands (Rs 1-30 Cr revenue) selling on Shopify, Amazon, Flipkart and quick-commerce (Blinkit, Zepto, Instamart), advertising on Meta and Google. Existing tools are dollar-priced, US-oriented and rely on pixel attribution. We answer weekly:
1. Where am I actually making money? True net profit by channel/SKU/city after COGS, marketplace fees, shipping, payment fees and COD/RTO losses.
2. What changed and why? Anomalies with root-cause breakdown.
3. Where should my next rupee go? Budget recommendations with expected impact, confidence range, and a suggested experiment when the model is unsure.

## Principles (non-negotiable)
- Orders are the source of truth, not ad clicks. Measurement is causal-first (MMM + experiments); platform attribution is a tactical signal only.
- Every business metric has ONE canonical implementation in `engine/metrics/`. No ad hoc metric math anywhere else (including React).
- Money is integer paise (INR). Timestamps stored UTC, displayed IST. Indian FY (Apr-Mar) is a reporting period.
- Multi-tenant by `org_id`; RLS on every tenant table.
- Raw source data is retained (`raw_records`) so everything can be re-derived.
- Claude never gets raw SQL. Claude -> tool -> metric layer -> validated data. Every number in an AI brief comes from a tool call and is cited; missing data is stated; never invent figures.
- Causal claims need sufficient evidence. Weak data returns `INSUFFICIENT_DATA`, never fabricated confidence or intervals. MMM needs >=52 weeks and enough spend variation, else fall back to descriptive analysis.
- External API fields, scopes, endpoints and rate limits must be verified against current official docs. Never invent them. Flag app reviews/developer-token needs in `PROGRESS.md`.
- Never put secrets in Git. OAuth tokens are encrypted at rest (app-level AES-GCM).

## Architecture (build order)
Connector -> Raw Record -> Canonical Ledger -> Metric Layer -> API / Intelligence -> Claude Tools -> AI Brief.
1. Commerce ledger: one order-level table across channels with net margin per order.
2. Semantic metric layer: `net_revenue`, `contribution_margin`, `expected_rto_loss`, `blended_cac`, `mer`, `platform_roas` (labelled "platform-reported, not causal"), `contribution_after_marketing`; group-by channel, SKU, category, city, pincode, payment method, week. Pure, tested with hand-calculated fixtures.
3. Causal engine: Bayesian MMM per brand (PyMC-Marketing), calibrated by experiments.
4. Decision engine + AI brief: budget optimiser over response curves; STL + robust z-score anomalies with driver decomposition; weekly Claude brief (Mondays 08:00 IST).
5. Experiment loop: every recommendation/experiment logged as a hypothesis with predicted vs actual outcome.

## Stack
Next.js (App Router, TS), Tailwind, shadcn/ui, Recharts | Supabase (Postgres, Auth, RLS) | Python 3.12, FastAPI + worker, Postgres `jobs` queue (SKIP LOCKED, no Redis) | pandas/polars, PyMC-Marketing, statsmodels | Claude API with tool use | Docker Compose locally; Vercel + Railway/Render/Fly. Layout: `/web`, `/engine`, `/supabase`, `/docs`.

## MVP scope
In: Shopify, Meta, Google Ads, Shiprocket connectors (backfill + daily incremental); CSV import for marketplaces/quick-commerce (ask for sample files, never guess formats); dashboard; anomalies; MMM v0; budget recommendations (80% interval, probability of beating current split); geo-holdout experiment designer + diff-in-diff measurement; weekly brief by email.
Out: autonomous budget changes, real-time data, self-serve billing, polished onboarding, marketplace APIs, cross-brand pooling, mobile.
Done when a pilot brand sees return-adjusted profit by channel/SKU/city matching their books within 2% and gets a traceable weekly brief.

## Agent ownership (one branch + worktree each; `main` integrates)
| Agent | Branch | Owns |
|---|---|---|
| Database | agent/database | `supabase/`: migrations, RLS, indexes, constraints, functions. No casual app-code edits. |
| Ledger | agent/ledger | `engine/ledger/`, `engine/transforms/`, `engine/reconciliation/`: raw -> normalisation -> canonical ledger -> reconciliation. |
| Metrics | agent/metrics | `engine/metrics/`: all canonical metrics. |
| Frontend | agent/frontend | `web/`: consumes API contracts, no business logic. |
| Connectors | agent/connectors | `engine/connectors/`: auth, pagination, incremental sync, backfill, retries, rate limits, idempotency, raw persistence, sync status, errors. |
| Intelligence | agent/intelligence | `engine/intelligence/`, `engine/models/`, `engine/anomaly/`: anomalies, MMM, curves, optimiser, experiment design. |
| AI | agent/ai | `engine/ai/`: Claude tools, brief, recommendations, traceability, audit trail. |
| Main (integration) | main | `engine/app/`, `engine/worker/`, `engine/seed/`, integration tests, cross-cutting config, shared bootstrapping. |

Rules: work only in your own worktree and owned paths; request changes elsewhere via `docs/SCHEMA_CHANGE_REQUESTS.md` (schema) or `PROGRESS.md` > Integration Needed; shared files (`CLAUDE.md`, `DECISIONS.md`, `PROGRESS.md`, `docs/CONTRACTS.md`) are not rewritten casually; specialists do not merge to `main`; small focused commits; never merge failing tests. Code against contracts with mocks rather than waiting on upstream.

## Working agreements
- Log decisions in `DECISIONS.md` (Decision / Alternatives / Reason / Impact / Date / Agent); status in `PROGRESS.md`.
- Seed data: `engine/seed/synthetic.py` (synthetic Indian D2C brand) keeps everything demoable without a client.
- Run tests before saying anything is done: `cd engine && python -m pytest -q` (use `python -m` so your worktree's code is imported).
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
