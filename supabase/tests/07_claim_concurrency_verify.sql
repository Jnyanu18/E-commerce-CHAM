-- Verify: 40 jobs, 40 claims, each job claimed exactly once, attempts == 1, work actually spread over workers.
select test_h.ok((select count(*) from conc_claims) = 40, 'every job claimed (no lost jobs)');
select test_h.ok((select count(distinct job_id) from conc_claims) = 40, 'no job claimed twice (no duplicates)');
select test_h.ok((select count(*) from jobs where kind='conc_test' and status='running' and attempts=1) = 40, 'all jobs running with attempts=1');
select test_h.ok((select count(distinct worker) from conc_claims) >= 2, 'more than one worker obtained jobs');
select test_h.ok((select count(*) from claim_job(array['conc_test'])) = 0, 'queue drained');
delete from jobs where kind = 'conc_test';
drop table conc_claims;
select 'PASS 07_claim_concurrency' as result;
