# Progress

## Done
- Repo skeleton, git init; schema migrations 0001-0004 approved by user
- Engine scaffold: config, AES-GCM credential crypto, paise helpers, Postgres job queue (worker/queue.py), FastAPI /health
- Synthetic Indian D2C seed generator (engine/seed/synthetic.py), deterministic
- docker-compose + local Supabase stub (untested: Docker not installed)
- 3 tests passing

## In progress
- Week 1: DB loader for seed, raw -> ledger transform, auth + Next.js scaffold, COGS UI, connectors

## Blocked / needs user
- Docker not installed (migrations unverified against a real Postgres)
- Shopify/Meta/Google Ads/Shiprocket credentials and approvals (Meta App Review, Google developer token)
- Product name
