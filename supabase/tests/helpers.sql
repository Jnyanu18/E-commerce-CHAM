-- Tiny assertion helpers for the plain-SQL tests (no pgTAP dependency).
create schema if not exists test_h;
grant usage on schema test_h to public;

create or replace function test_h.ok(cond boolean, msg text) returns void language plpgsql as $$
begin
  if cond is distinct from true then raise exception 'ASSERT FAILED: %', msg; end if;
end $$;

-- Passes only if the statement raises; optionally require a SQLSTATE (e.g. '42501' insufficient_privilege).
create or replace function test_h.throws(stmt text, msg text, want_state text default null) returns void
language plpgsql as $$
begin
  begin
    execute stmt;
  exception when others then
    if want_state is not null and sqlstate <> want_state then
      raise exception 'ASSERT FAILED: % (wanted SQLSTATE %, got % : %)', msg, want_state, sqlstate, sqlerrm;
    end if;
    return;
  end;
  raise exception 'ASSERT FAILED: % (statement did not raise)', msg;
end $$;

-- Number of rows a query returns as the CURRENT role (RLS applies).
create or replace function test_h.cnt(q text) returns bigint language plpgsql as $$
declare n bigint;
begin execute 'select count(*) from (' || q || ') s' into n; return n; end $$;

-- Rows affected by a DML statement, as the CURRENT role.
create or replace function test_h.affected(stmt text) returns bigint language plpgsql as $$
declare n bigint;
begin execute stmt; get diagnostics n = row_count; return n; end $$;

-- Impersonate a signed-in user for the rest of the transaction.
create or replace function test_h.login(p_user uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claim.sub', p_user::text, true);
  execute 'set local role authenticated';
end $$;

create or replace function test_h.logout() returns void language plpgsql as $$
begin
  execute 'reset role';
  perform set_config('request.jwt.claim.sub', '', true);
end $$;

grant execute on all functions in schema test_h to public;
