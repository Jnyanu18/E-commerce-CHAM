-- One worker session: claim until the queue is empty, recording what it got. :worker is set by run.sh.
-- (psql variables are not expanded inside dollar-quoted bodies, so pass it via a setting.)
select set_config('app.worker', :'worker', false);
do $$
declare j jobs; w int := current_setting('app.worker')::int;
begin
  loop
    select * into j from claim_job(array['conc_test']);
    exit when not found;
    insert into conc_claims values (w, j.id);
    perform pg_sleep(0.01);   -- widen the race window
  end loop;
end $$;
