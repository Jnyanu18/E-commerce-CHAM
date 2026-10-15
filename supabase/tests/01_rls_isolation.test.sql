-- RLS isolation across two orgs, plus write restrictions. Everything rolls back.
begin;
insert into auth.users (id, email) values
  ('aaaaaaaa-0000-0000-0000-00000000000a','a@x.test'),('bbbbbbbb-0000-0000-0000-00000000000b','b@x.test'),
  ('cccccccc-0000-0000-0000-00000000000c','nomember@x.test');
insert into orgs (id, name) values
  ('a0000000-0000-0000-0000-00000000000a','OrgA'),('b0000000-0000-0000-0000-00000000000b','OrgB');
insert into org_members (org_id, user_id) values
  ('a0000000-0000-0000-0000-00000000000a','aaaaaaaa-0000-0000-0000-00000000000a'),
  ('b0000000-0000-0000-0000-00000000000b','bbbbbbbb-0000-0000-0000-00000000000b');

-- one row in every tenant table, per org
do $$
declare o uuid; oid uuid; rid bigint; sid uuid; mr uuid;
begin
  foreach o in array array['a0000000-0000-0000-0000-00000000000a','b0000000-0000-0000-0000-00000000000b']::uuid[] loop
    insert into integrations (org_id, provider, account_ref) values (o,'shopify','shop-'||o);
    insert into jobs (org_id, kind) values (o,'transform');
    insert into raw_records (org_id, source, external_id, payload) values (o,'shopify_order','1','{}') returning id into rid;
    insert into skus (org_id, sku) values (o,'S1');
    insert into sku_cogs (org_id, sku, effective_from, cogs_paise) values (o,'S1','2026-04-01',100);
    insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)
      values (o,'shopify','1','2026-09-01T10:00:00Z','cod',10000) returning id into oid;
    insert into order_items (org_id, order_id, sku, qty, unit_price_paise) values (o,oid,'S1',1,10000);
    insert into fees (org_id, order_id, type, amount_paise) values (o,oid,'shipping',500);
    insert into returns (org_id, order_id, type) values (o,oid,'RTO');
    insert into traffic_daily values (o,'shopify','2026-09-01',100);
    insert into ad_spend_daily (org_id, platform, campaign_id, date, spend_paise) values (o,'meta','c1','2026-09-01',1);
    insert into model_runs (org_id, model_type) values (o,'mmm_v0') returning id into mr;
    insert into channel_response_curves (org_id, model_run_id, channel, spend_grid_paise, expected_revenue_paise, lower_paise, upper_paise)
      values (o, mr, 'meta', '{1}','{1}','{1}','{1}');
    insert into experiments (org_id, hypothesis) values (o,'h');
    insert into recommendations (org_id, type, payload) values (o,'budget_shift','{}');
    insert into insights (org_id, week_start) values (o,'2026-09-07');
    insert into csv_mappings (org_id, source, report_type, mapping) values (o,'amazon','orders','{}');
    insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, net_paise)
      values (o,'amazon','S-1','2026-09-01','2026-09-07',10000,9000) returning id into sid;
    insert into settlement_items (org_id, settlement_id, order_external_id, order_id, net_paise) values (o,sid,'1',oid,9000);
    insert into shipments (org_id, external_id, order_id) values (o,'SH-1',oid);
  end loop;
end $$;

-- Every org-scoped table: user A sees exactly org A's rows, user B exactly org B's, a non-member nothing.
do $$
declare t text; a uuid := 'a0000000-0000-0000-0000-00000000000a';
begin
  for t in select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
           join pg_attribute at on at.attrelid=c.oid and at.attname='org_id' and not at.attisdropped
           where n.nspname='public' and c.relkind in ('r','v') and c.relname <> 'org_members' order by 1 loop
    perform test_h.login('aaaaaaaa-0000-0000-0000-00000000000a');
    perform test_h.ok(test_h.cnt(format('select 1 from %I', t)) >= 1, t||': member A sees own rows');
    perform test_h.ok(test_h.cnt(format('select 1 from %I where org_id <> %L', t, a)) = 0, t||': A sees no foreign rows');
    perform test_h.login('cccccccc-0000-0000-0000-00000000000c');
    perform test_h.ok(test_h.cnt(format('select 1 from %I', t)) = 0, t||': non-member sees nothing');
    perform test_h.logout();
  end loop;
end $$;

-- every public base table has RLS enabled (catches future tables added without it)
select test_h.ok(not exists (select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r' and not c.relrowsecurity), 'RLS enabled on every public table');

select test_h.login('aaaaaaaa-0000-0000-0000-00000000000a');
select test_h.ok(test_h.cnt('select 1 from orgs') = 1, 'A sees only OrgA in orgs');
select test_h.ok(test_h.cnt('select 1 from org_members') = 1, 'A sees only own org membership');
-- cross-tenant writes are blocked
select test_h.throws($$insert into skus (org_id, sku) values ('b0000000-0000-0000-0000-00000000000b','X')$$,
  'A cannot insert a sku into OrgB', '42501');
select test_h.ok(test_h.affected('update skus set name=''hack'' where org_id=''b0000000-0000-0000-0000-00000000000b''') = 0,
  'A cannot update OrgB skus');
select test_h.ok(test_h.affected('delete from skus where org_id=''b0000000-0000-0000-0000-00000000000b''') = 0,
  'A cannot delete OrgB skus');
-- user-editable tables: A can write own org
insert into skus (org_id, sku) values ('a0000000-0000-0000-0000-00000000000a','S2');
insert into experiments (org_id, hypothesis) values ('a0000000-0000-0000-0000-00000000000a','h2');
-- pipeline tables: no browser writes at all, even within own org
select test_h.throws($$insert into orders (org_id,channel,external_id,ordered_at,payment_method,gross_revenue_paise)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','9','2026-09-01','cod',1)$$, 'A cannot insert orders', '42501');
select test_h.throws($$insert into raw_records (org_id,source,external_id,payload) values ('a0000000-0000-0000-0000-00000000000a','x','1','{}')$$,
  'A cannot insert raw_records', '42501');
select test_h.throws($$insert into jobs (org_id,kind) values ('a0000000-0000-0000-0000-00000000000a','x')$$, 'A cannot insert jobs');
select test_h.throws($$insert into settlements (org_id,channel,external_id,period_start,period_end,gross_paise,net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','S-2','2026-09-01','2026-09-02',1,1)$$, 'A cannot insert settlements', '42501');
select test_h.throws($$update settlements set net_paise = 0$$, 'A cannot update settlements', '42501');
select test_h.throws($$insert into shipments (org_id, external_id) values ('a0000000-0000-0000-0000-00000000000a','Z')$$, 'A cannot insert shipments', '42501');
-- tenancy tables: users cannot create orgs or add themselves anywhere
select test_h.throws($$insert into orgs (name) values ('mine')$$, 'user cannot create org');
select test_h.throws($$insert into org_members (org_id,user_id) values ('b0000000-0000-0000-0000-00000000000b','aaaaaaaa-0000-0000-0000-00000000000a')$$,
  'A cannot add self to OrgB');
select test_h.logout();

-- anon sees nothing and cannot write
set local role anon;
select test_h.throws('select count(*) from orders', 'anon has no table privileges on orders', '42501');
select test_h.throws('select count(*) from settlements', 'anon has no privileges on settlements', '42501');
reset role;
rollback;
select 'PASS 01_rls_isolation' as result;
