-- 0007: settlements (marketplace / gateway payouts), settlement lines, and shipments (courier side).
-- Money = bigint paise. Sign convention: gross/fees are positive magnitudes, net = gross - fees; adjustments and
-- refunds are carried as their own settlement_items lines. Writes are service role only; members read their org.

-- Composite-FK target so a child row can never point at another tenant's order.
alter table orders add constraint orders_org_id_id_key unique (org_id, id);

create table settlements (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  channel text not null,                            -- amazon, flipkart, shopify_payments, razorpay, shiprocket_cod, ...
  external_id text not null,                        -- settlement / payout / remittance id from the source
  period_start date not null,
  period_end date not null,
  settled_at timestamptz,                           -- when money landed; null if unknown
  gross_paise bigint not null,
  fees_paise bigint not null default 0,
  net_paise bigint not null,
  source text not null default 'csv' check (source in ('api','csv','estimated')),
  raw_record_id bigint references raw_records(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, channel, external_id),            -- idempotent upsert target
  unique (org_id, id),
  check (period_end >= period_start)
);
create index settlements_org_period_idx on settlements (org_id, period_end);

create table settlement_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  settlement_id uuid not null,
  line_type text not null default 'sale' check (line_type in ('sale','refund','fee','adjustment','other')),
  order_external_id text not null,                  -- order reference exactly as the settlement reports it
  order_id uuid,                                    -- resolved ledger order; null until matched (unreconciled)
  gross_paise bigint not null default 0,
  fees_paise bigint not null default 0,
  net_paise bigint not null,
  created_at timestamptz not null default now(),
  foreign key (org_id, settlement_id) references settlements (org_id, id) on delete cascade,
  foreign key (org_id, order_id) references orders (org_id, id) on delete set null (order_id),
  unique (settlement_id, order_external_id, line_type)  -- idempotent upsert target
);
create index settlement_items_org_order_idx on settlement_items (org_id, order_id);

create table shipments (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  source text not null default 'shiprocket',
  external_id text not null,                        -- courier-aggregator shipment id
  order_id uuid,
  order_external_id text,                           -- channel order reference, for late matching
  carrier text,
  awb text,
  status text not null default 'unknown' check (status in (
    'created','in_transit','out_for_delivery','delivered','rto_initiated','rto_delivered','cancelled','lost','unknown')),
  shipped_at timestamptz,
  delivered_at timestamptz,
  rto_at timestamptz,
  freight_cost_paise bigint not null default 0 check (freight_cost_paise >= 0),
  cod_amount_paise bigint not null default 0 check (cod_amount_paise >= 0),
  raw_record_id bigint references raw_records(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (org_id, order_id) references orders (org_id, id) on delete set null (order_id),
  unique (org_id, source, external_id)
);
create index shipments_org_order_idx on shipments (org_id, order_id);
create unique index shipments_org_awb_key on shipments (org_id, awb) where awb is not null;

-- Header vs lines, for reconciliation. security_invoker => the caller's RLS applies.
create view settlement_reconciliation with (security_invoker = true) as
select s.id as settlement_id, s.org_id, s.channel, s.external_id,
       s.gross_paise, s.fees_paise, s.net_paise,
       count(i.id)                                           as item_count,
       count(i.id) filter (where i.order_id is null)         as unmatched_item_count,
       coalesce(sum(i.net_paise), 0)::bigint                 as items_net_paise,
       (s.net_paise - coalesce(sum(i.net_paise), 0))::bigint as net_diff_paise
from settlements s
left join settlement_items i on i.settlement_id = s.id
group by s.id;

-- RLS: members read; no client write policies exist, and write privileges are revoked as defence in depth.
do $$
declare t text;
begin
  foreach t in array array['settlements','settlement_items','shipments'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select using (is_org_member(org_id))', t||'_read', t);
    execute format('revoke all on %I from anon', t);
    execute format('revoke insert, update, delete, truncate on %I from authenticated', t);
  end loop;
end $$;
revoke all on settlement_reconciliation from anon;
revoke insert, update, delete, truncate on settlement_reconciliation from authenticated;
