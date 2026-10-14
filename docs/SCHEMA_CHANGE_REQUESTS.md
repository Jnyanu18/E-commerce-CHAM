# Schema Change Requests

Agents needing a schema change add an entry here instead of editing `supabase/`. The database agent reviews and implements via a new numbered migration.

Format: Date / Requested by / Table / Change / Why / Status.

_None yet._

## 2026-10-14 / Ledger agent

### 1. `settlements` table (REQUIRED for settlement persistence and reconciliation) / Status: open
The ledger codes against this assumed interface (`ledger.pg_repository.PsycopgRepository.upsert_settlement`); the database agent owns the final design. Adjust names freely, but tell the ledger agent.
```
settlements(
  id uuid pk, org_id uuid not null -> orgs,
  channel text not null,                 -- order channel (shopify, amazon, ...)
  settlement_ref text not null,          -- payout / remittance / statement id from the source
  order_id uuid null -> orders(id),      -- null = orphan: settlement seen before / without its order
  order_external_id text not null,       -- natural-key link that survives a missing order
  source text not null,                  -- csv_<marketplace> | shiprocket_cod | ...
  amount_paise bigint not null,          -- net amount credited for this order (may be negative)
  settled_at timestamptz null,
  raw_source text, raw_external_id text, -- link back to raw_records
  unique (org_id, channel, settlement_ref, order_external_id)   -- idempotent upsert target
)
```
RLS like other pipeline tables (service-role writes, org members read). Index `(org_id, order_id)`.

### 2. `orders.city_tier` enum (already planned by main, docs/CONTRACTS.md section 8) / Status: waiting on database agent
Ledger now writes `metro | tier_1 | tier_2 | tier_3 | unknown`; the 0002 check constraint (`tier1..tier3`, no `unknown`) rejects these until the new migration lands. `city_tiers.tier` needs the same change; ledger normalises legacy `tier1..3` read from it.

### 3. Link from canonical rows to raw record (NICE TO HAVE) / Status: open
`orders` has no pointer to the `raw_records` row it came from. The source identifier is kept as `orders.external_id` (and `settlements.raw_*`), which resolves to `raw_records` only when the connector's raw `external_id` equals the order id. Suggest `orders.raw_record_id bigint references raw_records(id)` (nullable) or `orders.source_refs jsonb`. Not blocking.

### 4. Placeholder SKU (FYI, no change)
Order lines with no SKU are stored with `sku = '__MISSING_SKU__'` (order_items.sku is NOT NULL) and surfaced as a `missing_sku` issue/discrepancy. Metrics must treat it as "COGS unknown", never as zero.
