-- claim_job semantics (single session) and the stale-job reaper. Rolls back.
begin;
-- empty queue -> zero rows, not a row of nulls
select test_h.ok((select count(*) from claim_job(array['transform'])) = 0, 'empty queue yields zero rows');

insert into jobs (kind, run_after) values
  ('transform', now() - interval '2 minutes'),   -- 1: oldest
  ('transform', now() - interval '1 minute'),    -- 2
  ('mmm_run',   now() - interval '1 minute'),    -- 3: other kind
  ('transform', now() + interval '1 hour');      -- 4: not due

do $$ declare j jobs; begin
  select * into j from claim_job(array['transform']);
  perform test_h.ok(j.status = 'running' and j.attempts = 1 and j.locked_at is not null, 'claim marks running, attempts+1, locks');
  perform test_h.ok(j.run_after < now() - interval '90 seconds', 'oldest due job claimed first');
end $$;
select test_h.ok((select count(*) from claim_job(array['transform'])) = 1, 'second claim gets next due job');
select test_h.ok((select count(*) from claim_job(array['transform'])) = 0, 'future job not claimable, claimed jobs not reclaimed');
select test_h.ok((select count(*) from claim_job(array['nonexistent'])) = 0, 'kind filter respected');
select test_h.ok((select count(*) from claim_job('{}'::text[])) = 0, 'empty kinds array claims nothing');
select test_h.ok((select count(*) from claim_job(array['mmm_run'])) = 1, 'other kind claimable by its worker');
select test_h.ok((select count(*) from jobs where status='running') = 3, 'three running');

-- reaper: fresh locks untouched; stale locks requeued with note; exhausted jobs fail
select test_h.ok(reap_stale_jobs(interval '15 minutes') = 0, 'fresh running jobs not reaped');
update jobs set locked_at = now() - interval '1 hour' where kind = 'transform' and status = 'running';
update jobs set max_attempts = 1 where id = (select min(id) from jobs where kind='transform');   -- attempts(1) >= max(1): will fail
select test_h.ok(reap_stale_jobs(interval '15 minutes') = 2, 'two stale jobs reaped');
select test_h.ok((select status from jobs order by id limit 1) = 'failed', 'exhausted stale job marked failed');
select test_h.ok((select finished_at is not null from jobs order by id limit 1), 'failed job has finished_at');
select test_h.ok((select status from jobs where kind='transform' and attempts=1 and max_attempts=5) = 'queued', 'retryable stale job requeued');
select test_h.ok((select locked_at is null and reaped_count = 1 and last_error like 'reaped:%' from jobs where kind='transform' and max_attempts=5 and attempts>=1),
  'requeued job unlocked, counted, annotated');
select test_h.ok((select count(*) from claim_job(array['transform'])) = 1, 'reaped job claimable again');
select test_h.ok((select attempts from jobs where kind='transform' and max_attempts=5 and run_after < now() + interval '1 minute') = 2, 'attempts keep counting across reaps');
select test_h.ok((select count(*) from jobs where status='running' and kind='mmm_run') = 1, 'non-stale other-kind job untouched');
rollback;
select 'PASS 05_jobs' as result;
