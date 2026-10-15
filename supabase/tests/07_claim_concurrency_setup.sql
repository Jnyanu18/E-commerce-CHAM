-- Concurrency test fixtures (committed data; run.sh spawns parallel workers, then verifies, then cleans up).
-- Uses a dedicated kind so it cannot collide with anything else.
delete from jobs where kind = 'conc_test';
insert into jobs (kind) select 'conc_test' from generate_series(1, 40);
create table if not exists conc_claims (worker int, job_id bigint);
truncate conc_claims;
