-- 0004: Row Level Security. Every tenant table gets RLS; reads limited to org members.
-- The engine uses the service role (bypasses RLS) for all pipeline writes.
-- Browser clients may write only user-editable tables (see second block).

alter table orgs enable row level security;
create policy orgs_read on orgs for select using (is_org_member(id));

alter table org_members enable row level security;
create policy members_read on org_members for select using (user_id = auth.uid() or is_org_member(org_id));

-- Read-only (for members) tenant tables
do $$
declare t text;
begin
  foreach t in array array[
    'integrations','jobs','raw_records','orders','order_items','fees','returns',
    'traffic_daily','ad_spend_daily','model_runs','channel_response_curves','insights'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select using (is_org_member(org_id))', t||'_read', t);
  end loop;
end $$;

-- User-editable tenant tables: members can read and write within their org
do $$
declare t text;
begin
  foreach t in array array['skus','sku_cogs','csv_mappings','experiments','recommendations'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select using (is_org_member(org_id))', t||'_read', t);
    execute format('create policy %I on %I for insert with check (is_org_member(org_id))', t||'_ins', t);
    execute format('create policy %I on %I for update using (is_org_member(org_id)) with check (is_org_member(org_id))', t||'_upd', t);
    execute format('create policy %I on %I for delete using (is_org_member(org_id))', t||'_del', t);
  end loop;
end $$;

-- Reference data: readable by any signed-in user, no client writes
alter table city_tiers enable row level security;
create policy city_tiers_read on city_tiers for select using (auth.uid() is not null);

-- Credentials must never reach the browser, even for members.
-- (A column revoke is a no-op under a table-level grant, so revoke the table and re-grant columns.)
revoke select on integrations from authenticated, anon;
grant select (id, org_id, provider, account_ref, status, last_synced_at, last_error, backfill_done, created_at)
  on integrations to authenticated;
