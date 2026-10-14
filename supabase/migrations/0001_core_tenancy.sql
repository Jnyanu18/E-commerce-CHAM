-- 0001: tenancy, integrations, job queue
create extension if not exists pgcrypto;

create table orgs (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  fy_start_month int not null default 4,           -- Indian FY starts April
  created_at timestamptz not null default now()
);

create table org_members (
  org_id uuid not null references orgs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'member' check (role in ('owner','admin','member')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index on org_members (user_id);

-- RLS helper: security definer so policies on org_members itself don't recurse.
create function is_org_member(p_org uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from org_members where org_id = p_org and user_id = auth.uid());
$$;

create table integrations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  provider text not null check (provider in ('shopify','meta','google_ads','shiprocket','csv')),
  account_ref text not null,                        -- shop domain / ad account id / etc.
  credentials_enc bytea,                            -- app-level AES-GCM ciphertext; never plaintext
  status text not null default 'pending' check (status in ('pending','active','error','disconnected')),
  last_synced_at timestamptz,
  last_error text,
  backfill_done boolean not null default false,
  created_at timestamptz not null default now(),
  unique (org_id, provider, account_ref)
);

-- Postgres-backed queue; workers claim with FOR UPDATE SKIP LOCKED.
create table jobs (
  id bigserial primary key,
  org_id uuid references orgs(id) on delete cascade,
  kind text not null,                               -- sync_shopify, transform, mmm_run, weekly_brief, ...
  payload jsonb not null default '{}',
  status text not null default 'queued' check (status in ('queued','running','done','failed')),
  run_after timestamptz not null default now(),
  attempts int not null default 0,
  max_attempts int not null default 5,
  locked_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create index jobs_claim_idx on jobs (run_after) where status = 'queued';

create function claim_job(p_kinds text[]) returns jobs
language plpgsql as $$
declare j jobs;
begin
  select * into j from jobs
   where status = 'queued' and run_after <= now() and kind = any(p_kinds)
   order by run_after for update skip locked limit 1;
  if not found then return null; end if;
  update jobs set status='running', locked_at=now(), attempts=attempts+1 where id=j.id returning * into j;
  return j;
end $$;

create table csv_mappings (                         -- saved column mappings per org per source
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  source text not null,                             -- amazon, flipkart, blinkit, zepto, instamart
  report_type text not null,
  mapping jsonb not null,
  updated_at timestamptz not null default now(),
  unique (org_id, source, report_type)
);
