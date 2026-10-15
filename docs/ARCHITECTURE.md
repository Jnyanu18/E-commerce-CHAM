# Architecture

Status legend: **[built+tested]** runs in the test suites on Postgres; **[mock]** exercised only with synthetic or scripted inputs; **[not live]** code exists but has never touched the real external service; **[not built]**.

## 1. System context

```
 EXTERNAL SOURCES                                   YOU / PILOT BRAND
 ----------------                                   -----------------
 Shopify (GraphQL Admin)  [not live]                 Browser
 Shiprocket               [not live]                   |
 Meta Marketing API       [not live]                   v
 Google Ads API           [not live]            +----------------------+
 Marketplace CSVs         [not built: upload]   |  WEB  (Next.js)      |  [built+tested, sample-data mode]
   Amazon/Flipkart/Blinkit/Zepto/Instamart      |  dashboard, brief,   |
                                                |  COGS, integrations  |
                                                +----------+-----------+
                                                           | HTTPS + Supabase JWT
                                                           v
 +--------------------------------------------------------------------------------------+
 |  ENGINE (Python 3.12)                                                                |
 |                                                                                      |
 |   API (FastAPI)  [built+tested]            WORKER  [built+tested, never run as proc] |
 |   /metrics/{metric}   /metrics             poll loop: scheduler -> reaper -> claim   |
 |   /data-quality  /anomalies  /briefs/latest  jobs: sync . transform . reconcile .    |
 |   JWT + org-membership check, CORS               detect_anomalies . mmm_run .        |
 |             |                                    weekly_brief                        |
 |             v                                          |                             |
 |   +-----------------------------------------------+    |                             |
 |   |  METRIC LAYER  (the only place metrics live)  |<---+                             |
 |   |  16 registry metrics, day/week/channel/SKU/   |                                  |
 |   |  category/city/tier/pincode/payment/platform  |                                  |
 |   +-----------------------+-----------------------+                                  |
 |                           | reads ledger tables only                                 |
 +---------------------------+----------------------------------------------------------+
                             |
                             v
 +--------------------------------------------------------------------------------------+
 |  POSTGRES (Supabase in production)   RLS on every tenant table, money = bigint paise  |
 |  raw_records -> orders, order_items, fees, returns, settlements(+items), shipments,   |
 |  ad_spend_daily, traffic_daily, skus, sku_cogs, city_tiers | integrations, jobs,      |
 |  csv_mappings | model_runs, channel_response_curves, experiments, recommendations,    |
 |  insights                                                                            |
 +--------------------------------------------------------------------------------------+
```

## 2. Data flow (left to right is the only direction allowed)

```
 CONNECTORS            RAW                 LEDGER                    METRIC LAYER
 engine/connectors     raw_records         engine/transforms         engine/metrics
 -----------------     -----------         engine/ledger             --------------
 auth, backfill,   ->  verbatim        ->  engine/reconciliation ->  net_revenue
 incremental, retry,   payloads,           normalise, idempotent     contribution_margin
 rate limits,          upsert on           upserts, fee precedence   expected_rto_loss
 pagination            (org,source,id)     api>csv>estimated         orders, aov
 PII hashed at edge    re-derivable        GST rule, status merge    blended_cac, mer
 (HMAC customer key)                       reconcile (report only)   ad_spend, impressions,
                                                                     clicks, conversions
                                                                     platform_roas / revenue
                                                                     contribution_after_marketing
                                                                     sessions
                                                                          |
                          +-----------------------------------------------+------------+
                          |                                                            |
                          v                                                            v
                 INTELLIGENCE                                                       API -> WEB
                 engine/anomaly, engine/intelligence, engine/models
                 ---------------------------------------------------
                 STL + robust z-score anomalies  -> insights.anomalies
                 LMDI root cause (sums exactly)  -> attached to anomalies
                 MMM gate (>=52 wks, variance,    -> model_runs / curves   (PyMC ran once, 7.5 min)
                   diagnostics) -> else INSUFFICIENT_DATA + descriptive fallback
                 budget optimiser (80% interval, P(beats current))  -> recommendations
                 geo-holdout designer + diff-in-diff                -> experiments
                          |
                          v
                 AI LAYER  engine/ai          [Claude is MOCKED; never called live]
                 ----------------------------
                 Claude  --tool call-->  8 tools (no SQL, org bound server-side)
                    ^                          |
                    |                          v
                    |                 Metric layer / stored intelligence / data quality
                    |                          |
                    +--- tool results (call_id, provenance) ---+
                 validator: every number must cite [call_id] and match the result,
                 else bounded repair, else deterministic DATA-ONLY SUMMARY
                          |
                          v
                 insights.brief_markdown + tool_calls (audit)  ->  /briefs/latest -> WEB
                          |
                          v
                 SMTP e-mail [not live]
```

## 3. Weekly timeline (scheduler, IST)

```
 every day        : sync job per active non-CSV integration (backfill first time, then incremental)
                    -> connectors write raw_records -> enqueue transform -> ledger updates
 Monday 06:00     : mmm_run           per org with orders
 Monday 07:00     : detect_anomalies  per org (stores insights.anomalies, never touches the brief)
 Monday 08:00     : weekly_brief      per org (reads metrics + anomalies + models + recommendations
                                       + data quality) -> validate -> store -> e-mail
 always           : stale 'running' jobs are reaped; failed jobs retry with exponential backoff
```

## 4. Repository and ownership

```
 Business_AI_Analytics/                 (branch main = integration)
 |-- supabase/        agent/database      migrations 0001-0011, RLS, seeds, SQL tests
 |-- engine/
 |   |-- connectors/  agent/connectors    Shopify, Shiprocket, Meta, Google Ads, CSV
 |   |-- transforms/ ledger/ reconciliation/   agent/ledger
 |   |-- metrics/     agent/metrics        canonical metrics + repository + registry
 |   |-- anomaly/ intelligence/ models/    agent/intelligence
 |   |-- ai/          agent/ai             tools, brief, validator, audit
 |   |-- app/ worker/ seed/                main: API, auth, identity, crypto, mailer,
 |   |                                     data quality, AI backends, handlers, scheduler
 |   `-- tests/integration/                main: end-to-end seed -> API
 |-- web/             agent/frontend       Next.js dashboard (mock layer + typed API client)
 |-- scripts/                              local Postgres start/stop/reset
 `-- docs/            CONTRACTS, AGENT_WORKFLOW, ARCHITECTURE, agent_prompts
```

## 5. Contracts between layers

```
 Connector -> Raw      raw_records(org_id, source, external_id, payload)   idempotent upsert
 Raw -> Ledger         pure transforms + keyed upserts; schema is the contract
 Ledger -> Metrics     ledger tables only; MetricResult:
                       {metric, value, unit, currency, period, filters, group_by, groups,
                        definition, data_quality{status, issues[], row_count}}
 Metrics -> API        returned unchanged; MetricError -> HTTP 400
 Metrics -> Claude     same callables wrapped as tools + call_id
 Intelligence -> Rec   INSUFFICIENT_DATA | {expected_impact_paise, ci_low/high (80%), prob_beats_current}
 Rec -> Brief          every number carries [call_id] resolvable to a stored tool result
```

## 6. Security model

```
 tenant isolation   org_id on every tenant table + RLS (members read; pipeline writes via service role)
 browser writes     only skus, sku_cogs, csv_mappings, experiments, recommendations
 credentials        AES-GCM at rest, AAD = integration id + org id; column unreadable by browser roles
 customer identity  HMAC-SHA256(CUSTOMER_HASH_KEY) of normalised email/phone; raw PII never persisted
 API                bearer JWT (HS256) + org membership check; dev bypass is an explicit env flag
 Claude             no SQL, no free-form data access; org fixed by the executor; inputs validated
 secrets            env only; .env.example in git, never real values
```

## 7. What is real vs. not yet

```
 Proven on synthetic data:  seed -> raw -> worker -> ledger -> metrics -> API match independent ground
                            truth to the paisa (revenue, margin, ad spend, MER, CAM), two tenants isolated.
 Never touched the real thing: Shopify, Shiprocket, Meta, Google, Claude API, SMTP, Supabase project.
 Not built:                 OAuth connect flows, CSV upload endpoint/UI, onboarding UI, webhooks,
                            sessions source, deployment config.
```
