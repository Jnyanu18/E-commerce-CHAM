-- Settlement / shipment relationships, idempotent upserts, cross-tenant linkage protection. Rolls back.
begin;
insert into auth.users (id, email) values ('aaaaaaaa-0000-0000-0000-00000000000a','a@x.test');
insert into orgs (id, name) values ('a0000000-0000-0000-0000-00000000000a','OrgA'),('b0000000-0000-0000-0000-00000000000b','OrgB');
insert into org_members values ('a0000000-0000-0000-0000-00000000000a','aaaaaaaa-0000-0000-0000-00000000000a');
insert into orders (id, org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise) values
  ('11111111-0000-0000-0000-000000000001','a0000000-0000-0000-0000-00000000000a','amazon','O1','2026-09-01','prepaid',100000),
  ('11111111-0000-0000-0000-000000000002','a0000000-0000-0000-0000-00000000000a','amazon','O2','2026-09-01','prepaid',50000),
  ('22222222-0000-0000-0000-000000000001','b0000000-0000-0000-0000-00000000000b','amazon','O1','2026-09-01','prepaid',777);

insert into settlements (id, org_id, channel, external_id, period_start, period_end, gross_paise, fees_paise, net_paise)
  values ('55555555-0000-0000-0000-000000000001','a0000000-0000-0000-0000-00000000000a','amazon','SET-1','2026-09-01','2026-09-07',150000,30000,120000);
insert into settlement_items (org_id, settlement_id, order_external_id, order_id, gross_paise, fees_paise, net_paise) values
  ('a0000000-0000-0000-0000-00000000000a','55555555-0000-0000-0000-000000000001','O1','11111111-0000-0000-0000-000000000001',100000,20000,80000),
  ('a0000000-0000-0000-0000-00000000000a','55555555-0000-0000-0000-000000000001','O2','11111111-0000-0000-0000-000000000002',50000,10000,40000),
  ('a0000000-0000-0000-0000-00000000000a','55555555-0000-0000-0000-000000000001','O3-unmatched',null,0,0,0);

-- reconciliation view: lines add up to header, one unmatched line
select test_h.ok((select net_diff_paise from settlement_reconciliation) = 0, 'header net equals sum of lines');
select test_h.ok((select item_count from settlement_reconciliation) = 3 and (select unmatched_item_count from settlement_reconciliation) = 1, 'item and unmatched counts');
-- a partial settlement shows a non-zero diff
update settlements set net_paise = 125000;
select test_h.ok((select net_diff_paise from settlement_reconciliation) = 5000, 'diff surfaced in paise');
update settlements set net_paise = 120000;

-- idempotent upsert on header and lines
insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, fees_paise, net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','SET-1','2026-09-01','2026-09-07',150000,31000,119000)
  on conflict (org_id, channel, external_id) do update set fees_paise=excluded.fees_paise, net_paise=excluded.net_paise;
select test_h.ok((select count(*) from settlements) = 1 and (select net_paise from settlements) = 119000, 'settlement upsert is idempotent');
select test_h.throws($$insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','SET-1','2026-09-01','2026-09-07',1,1)$$, 'duplicate settlement id rejected', '23505');
select test_h.throws($$insert into settlement_items (org_id, settlement_id, order_external_id, net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','55555555-0000-0000-0000-000000000001','O1',1)$$, 'duplicate sale line rejected', '23505');
insert into settlement_items (org_id, settlement_id, order_external_id, line_type, net_paise)   -- refund line for same order is distinct
  values ('a0000000-0000-0000-0000-00000000000a','55555555-0000-0000-0000-000000000001','O1','refund',-5000);
-- boundaries / invalid
select test_h.throws($$insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','BAD','2026-09-08','2026-09-07',1,1)$$, 'period_end before period_start rejected', '23514');
insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, net_paise)   -- one-day period ok
  values ('a0000000-0000-0000-0000-00000000000a','amazon','ONEDAY','2026-09-07','2026-09-07',0,0);
select test_h.throws($$insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, net_paise, source)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','SRC','2026-09-07','2026-09-07',0,0,'guess')$$, 'bad source rejected', '23514');

-- cross-tenant linkage is impossible: OrgA line cannot reference OrgB order or settlement
select test_h.throws($$insert into settlement_items (org_id, settlement_id, order_external_id, order_id, net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','55555555-0000-0000-0000-000000000001','X','22222222-0000-0000-0000-000000000001',1)$$,
  'item cannot link to another org order', '23503');
select test_h.throws($$insert into settlement_items (org_id, settlement_id, order_external_id, net_paise)
  values ('b0000000-0000-0000-0000-00000000000b','55555555-0000-0000-0000-000000000001','X',1)$$, 'item cannot attach to another org settlement', '23503');
select test_h.throws($$insert into shipments (org_id, external_id, order_id)
  values ('a0000000-0000-0000-0000-00000000000a','SH','22222222-0000-0000-0000-000000000001')$$, 'shipment cannot link to another org order', '23503');

-- shipments
insert into shipments (org_id, external_id, order_id, carrier, awb, status, freight_cost_paise, cod_amount_paise)
  values ('a0000000-0000-0000-0000-00000000000a','SH1','11111111-0000-0000-0000-000000000001','Delhivery','AWB1','delivered',7550,0);
select test_h.throws($$insert into shipments (org_id, external_id, awb) values ('a0000000-0000-0000-0000-00000000000a','SH2','AWB1')$$, 'duplicate AWB per org rejected', '23505');
select test_h.throws($$insert into shipments (org_id, external_id) values ('a0000000-0000-0000-0000-00000000000a','SH1')$$, 'duplicate shipment id rejected', '23505');
select test_h.throws($$insert into shipments (org_id, external_id, freight_cost_paise) values ('a0000000-0000-0000-0000-00000000000a','SH3',-1)$$, 'negative freight rejected', '23514');
insert into shipments (org_id, external_id) values ('a0000000-0000-0000-0000-00000000000a','SH-noawb1'),('a0000000-0000-0000-0000-00000000000a','SH-noawb2'); -- null AWB not unique
insert into shipments (org_id, external_id, awb) values ('b0000000-0000-0000-0000-00000000000b','SH1','AWB1');   -- same ids in other org ok

-- deleting an order unlinks (keeps org_id) rather than deleting settlement data
delete from orders where id = '11111111-0000-0000-0000-000000000002';
select test_h.ok((select org_id from settlement_items where order_external_id='O2') = 'a0000000-0000-0000-0000-00000000000a'
  and (select order_id from settlement_items where order_external_id='O2') is null, 'order delete nulls link only');
-- deleting a settlement cascades to its lines
delete from settlements where external_id = 'SET-1';
select test_h.ok((select count(*) from settlement_items) = 0, 'items cascade with settlement');

-- member sees the reconciliation view through RLS
insert into settlements (org_id, channel, external_id, period_start, period_end, gross_paise, net_paise)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','V1','2026-09-01','2026-09-01',1,1),('b0000000-0000-0000-0000-00000000000b','amazon','V2','2026-09-01','2026-09-01',1,1);
select test_h.login('aaaaaaaa-0000-0000-0000-00000000000a');
select test_h.ok(test_h.cnt('select 1 from settlement_reconciliation') = 2 /* V1 + ONEDAY */, 'view is tenant-filtered (security_invoker)');
select test_h.ok(test_h.cnt($$select 1 from settlement_reconciliation where external_id='V2'$$) = 0, 'other org settlement hidden in view');
select test_h.logout();
rollback;
select 'PASS 03_settlements' as result;
