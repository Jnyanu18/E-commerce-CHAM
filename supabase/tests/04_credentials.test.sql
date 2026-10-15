-- Credentials must be unreadable (and unwritable) by browser roles. Rolls back.
begin;
insert into auth.users (id, email) values ('aaaaaaaa-0000-0000-0000-00000000000a','a@x.test');
insert into orgs (id, name) values ('a0000000-0000-0000-0000-00000000000a','OrgA');
insert into org_members values ('a0000000-0000-0000-0000-00000000000a','aaaaaaaa-0000-0000-0000-00000000000a');
insert into integrations (org_id, provider, account_ref, credentials_enc)
  values ('a0000000-0000-0000-0000-00000000000a','shopify','shop.myshopify.com','\xdeadbeef');

select test_h.login('aaaaaaaa-0000-0000-0000-00000000000a');
select test_h.throws('select credentials_enc from integrations', 'member cannot select credentials_enc', '42501');
select test_h.throws('select * from integrations', 'member cannot select *', '42501');
select test_h.throws('select count(credentials_enc) from integrations', 'member cannot aggregate credentials_enc', '42501');
select test_h.throws('select account_ref from integrations where credentials_enc is not null', 'member cannot filter on credentials_enc', '42501');
select test_h.ok(test_h.cnt('select id, org_id, provider, account_ref, status, last_synced_at, last_error, backfill_done, created_at from integrations') = 1,
  'safe columns remain readable');
select test_h.throws($$update integrations set credentials_enc='\x00'$$, 'member cannot write credentials', '42501');
select test_h.throws($$insert into integrations (org_id, provider, account_ref) values ('a0000000-0000-0000-0000-00000000000a','meta','x')$$,
  'member cannot create integrations', '42501');
select test_h.logout();

set local role anon;
select test_h.throws('select id from integrations', 'anon cannot read integrations', '42501');
reset role;

-- queue functions are not executable by browser roles (PostgREST rpc exposure)
select test_h.login('aaaaaaaa-0000-0000-0000-00000000000a');
select test_h.throws($$select * from claim_job(array['transform'])$$, 'authenticated cannot call claim_job', '42501');
select test_h.throws($$select reap_stale_jobs()$$, 'authenticated cannot call reap_stale_jobs', '42501');
select test_h.logout();

-- service_role (bypassrls, emulated) can read everything
set local role service_role;
select test_h.ok((select count(credentials_enc) from integrations) = 1, 'service_role can read credentials');
reset role;
rollback;
select 'PASS 04_credentials' as result;
