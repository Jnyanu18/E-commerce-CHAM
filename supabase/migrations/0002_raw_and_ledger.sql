-- 0002: raw landing tables + canonical commerce ledger
-- Conventions: money = bigint INR paise; timestamps = timestamptz (UTC), shown in IST.

-- Raw: one generic table keyed by source so re-derivation never needs a re-fetch.
create table raw_records (
  id bigserial primary key,
  org_id uuid not null references orgs(id) on delete cascade,
  source text not null,                             -- shopify_order, shiprocket_shipment, meta_insights, google_ads_row, csv_amazon, ...
  external_id text not null,
  payload jsonb not null,
  ingested_at timestamptz not null default now(),
  unique (org_id, source, external_id)              -- idempotent upsert target
);

create table skus (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  sku text not null,
  name text,
  category text,
  unique (org_id, sku)
);

create table sku_cogs (                             -- effective-dated; latest effective_from <= order date wins
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  sku text not null,
  effective_from date not null,
  cogs_paise bigint not null check (cogs_paise >= 0),
  unique (org_id, sku, effective_from)
);

create table orders (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  channel text not null,                            -- shopify, amazon, flipkart, blinkit, zepto, instamart
  external_id text not null,
  ordered_at timestamptz not null,
  ordered_date_ist date generated always as ((ordered_at at time zone 'Asia/Kolkata')::date) stored,
  customer_key text,                                -- sha256(normalised email|phone); null if unavailable. Drives new-customer / CAC.
  city text,
  city_tier text check (city_tier in ('metro','tier1','tier2','tier3')),
  pincode text,
  payment_method text not null check (payment_method in ('prepaid','cod')),
  status text not null default 'placed'
    check (status in ('placed','shipped','delivered','rto','cancelled','returned')),
  gross_revenue_paise bigint not null,
  discounts_paise bigint not null default 0,
  taxes_paise bigint not null default 0,            -- GST
  shipping_charged_paise bigint not null default 0,
  refunds_paise bigint not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, channel, external_id)
);
create index orders_org_date_idx on orders (org_id, ordered_date_ist);
create index orders_org_customer_idx on orders (org_id, customer_key);

create table order_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  sku text not null,
  qty int not null check (qty > 0),
  unit_price_paise bigint not null,
  unique (order_id, sku)
);
create index on order_items (org_id, sku);

create table fees (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  type text not null check (type in ('commission','shipping','payment_gateway','fulfilment','other')),
  amount_paise bigint not null,
  source text not null default 'api',               -- api | csv | estimated
  unique (order_id, type, source)
);
create index on fees (org_id, order_id);

create table returns (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  type text not null check (type in ('RTO','customer_return')),
  cost_paise bigint not null default 0,             -- logistics/handling loss (excl. refund of revenue)
  returned_at timestamptz,
  unique (order_id, type)
);
create index on returns (org_id, order_id);

-- Needed for the revenue = sessions x CVR x AOV decomposition (spec section 7, anomalies).
create table traffic_daily (
  org_id uuid not null references orgs(id) on delete cascade,
  channel text not null,
  date date not null,
  sessions bigint not null,
  primary key (org_id, channel, date)
);

create table ad_spend_daily (
  org_id uuid not null references orgs(id) on delete cascade,
  platform text not null check (platform in ('meta','google')),
  campaign_id text not null,
  campaign_name text,
  date date not null,
  spend_paise bigint not null,
  impressions bigint not null default 0,
  clicks bigint not null default 0,
  platform_reported_conversions numeric,
  platform_reported_revenue_paise bigint,
  primary key (org_id, platform, campaign_id, date)
);
create index on ad_spend_daily (org_id, date);

-- Reference: pincode/city -> tier, for RTO segmentation (category x city tier x payment method).
create table city_tiers (
  city text primary key,
  tier text not null check (tier in ('metro','tier1','tier2','tier3'))
);
