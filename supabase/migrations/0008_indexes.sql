-- 0008: index coverage for metric queries (org+date, org+sku, org+customer already exist from 0002).
create index if not exists orders_org_channel_date_idx on orders (org_id, channel, ordered_date_ist);
create index if not exists orders_org_tier_date_idx    on orders (org_id, city_tier, ordered_date_ist);
create index if not exists orders_org_pincode_idx      on orders (org_id, pincode) where pincode is not null;
create index if not exists channel_response_curves_run_idx on channel_response_curves (model_run_id);
create index if not exists recommendations_org_created_idx on recommendations (org_id, created_at desc);
