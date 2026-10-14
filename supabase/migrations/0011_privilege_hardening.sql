-- 0011: defence in depth on top of RLS. Supabase's default privileges grant ALL on new public tables to
-- anon/authenticated; RLS (no write policy) already blocks writes, but revoking the privilege makes the
-- intent explicit and fails loudly (42501) instead of silently affecting 0 rows.
--   * anon: no access to any public table or the reconciliation view.
--   * authenticated: SELECT only, except the five user-editable tables (skus, sku_cogs, csv_mappings,
--     experiments, recommendations), which keep full DML subject to their RLS policies.
-- integrations keeps its column-level SELECT grant from 0004 (credentials_enc stays unreadable).
do $$
declare t text;
begin
  for t in select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where n.nspname = 'public' and c.relkind in ('r','v') loop
    execute format('revoke all on public.%I from anon', t);
    if t not in ('skus','sku_cogs','csv_mappings','experiments','recommendations') then
      execute format('revoke insert, update, delete, truncate, references, trigger on public.%I from authenticated', t);
    end if;
  end loop;
end $$;

-- Every client write to the user-editable tables still needs a matching org: TRUNCATE bypasses RLS, so never grant it.
do $$
declare t text;
begin
  foreach t in array array['skus','sku_cogs','csv_mappings','experiments','recommendations'] loop
    execute format('revoke truncate, references, trigger on public.%I from authenticated', t);
  end loop;
end $$;
