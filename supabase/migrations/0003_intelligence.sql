-- 0003: modelling, decisions, experiments, insights
create table model_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  model_type text not null,                          -- mmm_v0, anomaly, ...
  data_start date, data_end date,
  params jsonb not null default '{}',
  diagnostics jsonb not null default '{}',           -- r_hat, ppc, sufficiency verdict
  sufficient_data boolean not null default false,    -- false => UI must fall back to descriptive view
  created_at timestamptz not null default now()
);
create index on model_runs (org_id, created_at desc);

create table channel_response_curves (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  model_run_id uuid not null references model_runs(id) on delete cascade,
  channel text not null,
  spend_grid_paise bigint[] not null,
  expected_revenue_paise bigint[] not null,
  lower_paise bigint[] not null,
  upper_paise bigint[] not null
);

create table experiments (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  hypothesis text not null,
  design text not null default 'geo_holdout',
  channel text,
  treatment_cities text[] not null default '{}',
  control_cities text[] not null default '{}',
  start_date date, end_date date,
  risk_paise bigint,
  predicted_effect jsonb,                            -- {point, ci_low, ci_high}
  measured_effect jsonb,
  status text not null default 'proposed'
    check (status in ('proposed','running','completed','cancelled')),
  created_at timestamptz not null default now()
);

create table recommendations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  model_run_id uuid references model_runs(id),
  experiment_id uuid references experiments(id),
  type text not null,                                -- budget_shift, run_experiment, ...
  payload jsonb not null,
  expected_impact_paise bigint,
  ci_low_paise bigint, ci_high_paise bigint,         -- 80% interval
  prob_beats_current numeric check (prob_beats_current between 0 and 1),
  status text not null default 'proposed'
    check (status in ('proposed','accepted','rejected','executed')),
  actual_impact_paise bigint,                        -- filled by the feedback loop
  created_at timestamptz not null default now()
);

create table insights (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  week_start date not null,
  anomalies jsonb not null default '[]',
  brief_markdown text,
  tool_calls jsonb,                                  -- audit trail: every number's source
  generated_at timestamptz not null default now(),
  emailed_at timestamptz,
  unique (org_id, week_start)
);
