-- Uniqueness / idempotency, city tier, pincode, money precision, empty and boundary data. Rolls back.
begin;
insert into orgs (id, name) values ('a0000000-0000-0000-0000-00000000000a','OrgA'),('b0000000-0000-0000-0000-00000000000b','OrgB');

-- raw_records idempotent upsert; duplicate external ids rejected; same id in another org allowed
insert into raw_records (org_id, source, external_id, payload) values ('a0000000-0000-0000-0000-00000000000a','shopify_order','1','{"v":1}');
select test_h.throws($$insert into raw_records (org_id, source, external_id, payload) values ('a0000000-0000-0000-0000-00000000000a','shopify_order','1','{}')$$,
  'duplicate raw record rejected', '23505');
insert into raw_records (org_id, source, external_id, payload) values ('a0000000-0000-0000-0000-00000000000a','shopify_order','1','{"v":2}')
  on conflict (org_id, source, external_id) do update set payload = excluded.payload;
select test_h.ok((select count(*) from raw_records) = 1 and (select payload->>'v' from raw_records) = '2', 'raw upsert updated in place');
insert into raw_records (org_id, source, external_id, payload) values ('b0000000-0000-0000-0000-00000000000b','shopify_order','1','{}');
select test_h.ok((select count(*) from raw_records) = 2, 'same external id allowed in a different org');

-- orders: natural key (org, channel, external_id)
insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','1','2026-09-01T20:00:00Z','cod',100);
select test_h.throws($$insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','1','2026-09-01','cod',100)$$, 'duplicate order rejected', '23505');
insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)
  values ('a0000000-0000-0000-0000-00000000000a','amazon','1','2026-09-01','prepaid',100);   -- other channel ok
-- generated IST date: 20:00Z is 01:30 IST next day
select test_h.ok((select ordered_date_ist from orders where channel='shopify') = date '2026-09-02', 'ordered_date_ist converts to IST');
-- boundary: 18:29:59Z is still same IST day, 18:30:00Z rolls over
insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise) values
  ('a0000000-0000-0000-0000-00000000000a','shopify','b1','2026-09-01T18:29:59Z','cod',1),
  ('a0000000-0000-0000-0000-00000000000a','shopify','b2','2026-09-01T18:30:00Z','cod',1);
select test_h.ok((select ordered_date_ist from orders where external_id='b1') = date '2026-09-01', 'IST boundary before midnight');
select test_h.ok((select ordered_date_ist from orders where external_id='b2') = date '2026-09-02', 'IST boundary at midnight');
select test_h.throws($$insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','bad','2026-09-01','upi',1)$$, 'invalid payment_method rejected', '23514');

-- city tier values
insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise, city_tier) values
  ('a0000000-0000-0000-0000-00000000000a','shopify','t1','2026-09-01','cod',1,'tier_1'),
  ('a0000000-0000-0000-0000-00000000000a','shopify','t2','2026-09-01','cod',1,null);
select test_h.ok((select city_tier from orders where external_id='t2') = 'unknown', 'explicit NULL city_tier becomes unknown');
select test_h.ok((select city_tier from orders where external_id='1' and channel='shopify') = 'unknown', 'omitted city_tier defaults to unknown');
select test_h.throws($$insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise, city_tier)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','t3','2026-09-01','cod',1,'tier1')$$, 'old tier1 value rejected', '23514');
select test_h.throws($$insert into city_tiers (city, tier) values ('Zzz','tier3')$$, 'old tier3 rejected in city_tiers', '23514');

-- pincode: text, 6 digits, leading zero kept
insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise, pincode) values
  ('a0000000-0000-0000-0000-00000000000a','shopify','p1','2026-09-01','cod',1,'011001');
select test_h.ok((select pincode from orders where external_id='p1') = '011001', 'leading zero pincode preserved');
select test_h.ok((select data_type from information_schema.columns where table_name='orders' and column_name='pincode') = 'text', 'pincode is text');
select test_h.throws($$update orders set pincode='12345' where external_id='p1'$$, '5-digit pincode rejected', '23514');
select test_h.throws($$update orders set pincode='1234567' where external_id='p1'$$, '7-digit pincode rejected', '23514');
select test_h.throws($$update orders set pincode='12A456' where external_id='p1'$$, 'non-digit pincode rejected', '23514');
select test_h.throws($$update orders set pincode='' where external_id='p1'$$, 'empty-string pincode rejected', '23514');
update orders set pincode = null where external_id='p1';  -- null allowed

-- money precision: bigint beyond 2^53 and beyond int4, exact arithmetic
select test_h.ok(not exists (select 1 from information_schema.columns where table_schema='public'
  and (column_name like '%\_paise' escape '\') and data_type not in ('bigint','ARRAY')), 'all *_paise columns are bigint');
select test_h.ok(not exists (select 1 from information_schema.columns where table_schema='public'
  and column_name like '%\_paise' escape '\' and data_type='ARRAY' and udt_name <> '_int8'), 'paise arrays are bigint[]');
insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise) values
  ('a0000000-0000-0000-0000-00000000000a','shopify','big','2026-09-01','cod',9007199254740993);   -- 2^53 + 1
select test_h.ok((select gross_revenue_paise from orders where external_id='big') = 9007199254740993, '2^53+1 paise stored exactly');
select test_h.ok((select sum(gross_revenue_paise) from orders where external_id in ('big','t1')) = 9007199254740994, 'sum exact');
select test_h.throws($$insert into orders (org_id, channel, external_id, ordered_at, payment_method, gross_revenue_paise)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','ovf','2026-09-01','cod',9223372036854775808)$$, 'beyond bigint rejected', '22003');
select test_h.throws($$insert into sku_cogs (org_id, sku, effective_from, cogs_paise) values ('a0000000-0000-0000-0000-00000000000a','S','2026-01-01',-1)$$,
  'negative cogs rejected', '23514');
insert into sku_cogs (org_id, sku, effective_from, cogs_paise) values ('a0000000-0000-0000-0000-00000000000a','S','2026-01-01',0);  -- boundary 0 ok
select test_h.throws($$insert into sku_cogs (org_id, sku, effective_from, cogs_paise) values ('a0000000-0000-0000-0000-00000000000a','S','2026-01-01',5)$$,
  'duplicate cogs effective date rejected', '23505');

-- child-row idempotency keys
do $$ declare oid uuid; begin
  select id into oid from orders where external_id='1' and channel='shopify';
  insert into order_items (org_id, order_id, sku, qty, unit_price_paise) values ('a0000000-0000-0000-0000-00000000000a',oid,'S',1,1);
  perform test_h.throws(format($q$insert into order_items (org_id, order_id, sku, qty, unit_price_paise) values ('a0000000-0000-0000-0000-00000000000a',%L,'S',1,1)$q$, oid), 'duplicate order item rejected', '23505');
  perform test_h.throws(format($q$insert into order_items (org_id, order_id, sku, qty, unit_price_paise) values ('a0000000-0000-0000-0000-00000000000a',%L,'S2',0,1)$q$, oid), 'qty 0 rejected', '23514');
  insert into fees (org_id, order_id, type, amount_paise, source) values ('a0000000-0000-0000-0000-00000000000a',oid,'shipping',1,'estimated');
  insert into fees (org_id, order_id, type, amount_paise, source) values ('a0000000-0000-0000-0000-00000000000a',oid,'shipping',2,'api'); -- estimate and actual coexist
  perform test_h.throws(format($q$insert into fees (org_id, order_id, type, amount_paise, source) values ('a0000000-0000-0000-0000-00000000000a',%L,'shipping',3,'api')$q$, oid), 'duplicate fee rejected', '23505');
end $$;

-- empty data: aggregate over nothing is NULL/0, not an error
select test_h.ok((select count(*) from ad_spend_daily) = 0 and (select coalesce(sum(spend_paise),0) from ad_spend_daily) = 0, 'empty table aggregates');
rollback;
select 'PASS 02_constraints' as result;
