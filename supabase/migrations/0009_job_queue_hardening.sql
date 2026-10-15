-- 0009: queue hardening. claim_job now returns SETOF jobs (zero rows when empty, not a null composite).
-- `select * from claim_job(%s)` in engine/worker/queue.py keeps working: same name, same argument, same columns;
-- an empty queue now yields no row (fetchone() -> None), which queue.py already treats as "empty".

alter table jobs add column if not exists reaped_count int not null default 0;
create index if not exists jobs_running_idx on jobs (locked_at) where status = 'running';

drop function claim_job(text[]);
create function claim_job(p_kinds text[]) returns setof jobs
language plpgsql set search_path = public as $$
declare j jobs;
begin
  select * into j from jobs
   where status = 'queued' and run_after <= now() and kind = any(p_kinds)
   order by run_after, id
   for update skip locked limit 1;
  if not found then return; end if;
  update jobs set status = 'running', locked_at = now(), attempts = attempts + 1
   where id = j.id returning * into j;
  return next j;
end $$;

-- Requeue (or fail, if out of attempts) jobs left in 'running' by a crashed worker.
-- Call periodically from the worker/scheduler. Returns the number of jobs touched.
create function reap_stale_jobs(p_stale interval default interval '15 minutes') returns integer
language plpgsql set search_path = public as $$
declare n integer;
begin
  with r as (
    update jobs set
      status       = case when attempts >= max_attempts then 'failed' else 'queued' end,
      finished_at  = case when attempts >= max_attempts then now() else null end,
      locked_at    = null,
      run_after    = now(),
      reaped_count = reaped_count + 1,
      last_error   = left('reaped: worker lock older than ' || p_stale::text
                          || coalesce('; previous error: ' || last_error, ''), 2000)
    where status = 'running' and locked_at < now() - p_stale
    returning 1)
  select count(*) into n from r;
  return n;
end $$;

-- Queue functions are for the engine / service role only; never callable from the browser (PostgREST rpc).
revoke execute on function claim_job(text[]), reap_stale_jobs(interval) from public, anon, authenticated;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function claim_job(text[]), reap_stale_jobs(interval) to service_role;
  end if;
end $$;
