-- Trend Observer V2: master data, facts, derived results and operational state.
-- Apply through `supabase db push`; never run this file from the browser.

create extension if not exists pgcrypto;

create table if not exists public.securities (
  security_id uuid primary key,
  symbol text not null,
  market text not null check (market in ('CN', 'HK', 'US')),
  name text not null,
  asset_type text not null check (asset_type in ('指数', '股票')),
  currency text not null,
  industry_template text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique (market, symbol)
);

create table if not exists public.provider_symbol_map (
  provider_symbol_map_id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities(security_id) on delete cascade,
  provider text not null, provider_symbol text not null, priority smallint not null default 1 check (priority > 0),
  is_active boolean not null default true, valid_from date, valid_to date,
  created_at timestamptz not null default now(), unique (security_id, provider)
);

create table if not exists public.market_calendars (
  market text not null, trade_date date not null, is_trading_day boolean not null,
  previous_trade_date date, next_trade_date date, source text not null,
  updated_at timestamptz not null default now(), primary key (market, trade_date)
);

create table if not exists public.metric_definitions (
  metric_code text primary key, name text not null, industry_template text, unit text,
  definition text not null, requires_confirmation boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.ingestion_source_records (
  ingestion_source_record_id uuid primary key default gen_random_uuid(), dataset_key text not null, data_type text not null,
  source text not null, provider text, request_params jsonb not null default '{}'::jsonb,
  source_date date, source_record_id text, content_hash text not null,
  metadata jsonb not null default '{}'::jsonb, fetched_at timestamptz not null default now()
);
create unique index if not exists ingestion_source_records_dedup
  on public.ingestion_source_records (dataset_key, source, coalesce(source_record_id, ''), content_hash);

create table if not exists public.source_documents (
  source_document_id uuid primary key default gen_random_uuid(),
  security_id uuid references public.securities(security_id) on delete cascade,
  source text not null, source_record_id text not null, title text, document_type text not null,
  report_period date, announcement_date date, document_url text,
  content_hash text not null, fetched_at timestamptz not null default now(),
  unique (source, source_record_id, content_hash)
);

create table if not exists public.market_daily (
  security_id uuid not null references public.securities(security_id) on delete cascade, trade_date date not null,
  open numeric, high numeric, low numeric, close numeric not null, volume numeric,
  adjustment_method text not null default 'source', source text not null,
  ingestion_source_record_id uuid references public.ingestion_source_records(ingestion_source_record_id), source_updated_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  primary key (security_id, trade_date)
);

create table if not exists public.valuation_daily (
  security_id uuid not null references public.securities(security_id) on delete cascade, trade_date date not null,
  valuation_type text not null, value numeric not null, methodology text, source text not null,
  ingestion_source_record_id uuid references public.ingestion_source_records(ingestion_source_record_id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  primary key (security_id, trade_date, valuation_type)
);

create table if not exists public.financial_facts (
  financial_fact_id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities(security_id) on delete cascade,
  report_period date not null, metric_code text not null references public.metric_definitions(metric_code),
  value numeric, unit text, period_type text not null check (period_type in ('single_quarter', 'year_to_date', 'ttm', 'annual')),
  announcement_date date, source_document_id uuid references public.source_documents(source_document_id),
  version integer not null default 1, is_current boolean not null default true,
  created_at timestamptz not null default now(), unique (security_id, report_period, metric_code, version)
);
create unique index if not exists financial_facts_current_unique on public.financial_facts (security_id, report_period, metric_code) where is_current;

create table if not exists public.dividend_events (
  dividend_event_id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities(security_id) on delete cascade, fiscal_year integer not null,
  event_stage text not null check (event_stage in ('proposal', 'approved', 'implemented')),
  announcement_id text not null, cash_dividend_per_share numeric, cash_dividend_total numeric,
  ex_date date, payment_date date, announcement_date date,
  source_document_id uuid references public.source_documents(source_document_id), created_at timestamptz not null default now(),
  unique (security_id, fiscal_year, event_stage, announcement_id)
);

create table if not exists public.industry_metric_values (
  industry_metric_value_id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities(security_id) on delete cascade, period date not null,
  metric_code text not null references public.metric_definitions(metric_code), value numeric, unit text,
  source_document_id uuid references public.source_documents(source_document_id), extraction_method text not null default 'automatic',
  confirmation_status text not null default 'pending' check (confirmation_status in ('pending', 'confirmed', 'returned')),
  confirmed_by uuid references auth.users(id), confirmed_at timestamptz, version integer not null default 1,
  created_at timestamptz not null default now(), unique (security_id, period, metric_code, version)
);

create table if not exists public.portfolio_allocations (
  allocation_id uuid primary key default gen_random_uuid(),
  allocation_type text not null check (allocation_type in ('target_ratio', 'actual_amount')),
  category text not null check (category in ('海外', '红利', '成长', '债券', '大宗商品', '现金')),
  data_date date not null, value numeric not null check (value >= 0), version integer not null default 1,
  created_by uuid references auth.users(id), created_at timestamptz not null default now(),
  unique (allocation_type, category, data_date, version)
);

create table if not exists public.asset_daily_signals (
  security_id uuid not null references public.securities(security_id) on delete cascade, trade_date date not null,
  close numeric, daily_return numeric, return_ytd numeric, return_1w numeric, return_1m numeric, return_1y numeric, return_3y numeric,
  ma20 numeric, ma60 numeric, ma120 numeric, ma200 numeric, ma20_slope_5d numeric, ma60_slope_10d numeric,
  ma120_slope_20d numeric, ma200_slope_40d numeric, short_trend text, mid_trend text, long_trend text,
  overall_status text, investment_advice text, pe numeric, pe_percentile numeric, pe_percentile_period text,
  valuation_status text, dividend_yield numeric, fundamental_status text, dividend_safety_status text,
  calculation_version text not null, updated_at timestamptz not null default now(), primary key (security_id, trade_date)
);

create table if not exists public.style_compass_results (
  style_compass_result_id uuid primary key default gen_random_uuid(), as_of_date date not null,
  left_security_id uuid not null references public.securities(security_id), right_security_id uuid not null references public.securities(security_id),
  return_20d_left numeric, return_20d_right numeric, return_20d_diff numeric,
  return_60d_left numeric, return_60d_right numeric, return_60d_diff numeric,
  return_120d_left numeric, return_120d_right numeric, return_120d_diff numeric,
  weighted_return_diff numeric, score integer, direction text, recommendation text,
  calculation_version text not null, created_at timestamptz not null default now(),
  unique (as_of_date, left_security_id, right_security_id, calculation_version)
);

create table if not exists public.fundamental_assessments (
  fundamental_assessment_id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities(security_id) on delete cascade, report_period date not null,
  dividend_safety_status text not null, operating_quality_status text not null, cash_reinvestment_status text not null,
  capital_structure_status text not null, fundamental_status text not null, evidence jsonb not null default '[]'::jsonb,
  main_risk text, calculation_version text not null, created_at timestamptz not null default now(),
  unique (security_id, report_period, calculation_version)
);

create table if not exists public.ingestion_watermarks (
  dataset_key text primary key, last_attempt_at timestamptz, last_success_at timestamptz,
  source_latest_date date, database_latest_date date, source_record_id text, content_hash text,
  status text not null default 'normal' check (status in ('normal', 'pending', 'failed', 'backoff', 'paused')),
  consecutive_failures integer not null default 0, next_retry_at timestamptz, last_error text,
  updated_at timestamptz not null default now()
);

create table if not exists public.ingestion_runs (
  ingestion_run_id uuid primary key default gen_random_uuid(), job_type text not null,
  trigger_type text not null check (trigger_type in ('schedule', 'manual', 'retry')),
  started_at timestamptz not null default now(), finished_at timestamptz,
  status text not null default 'running' check (status in ('running', 'succeeded', 'partial', 'failed', 'skipped')),
  summary jsonb not null default '{}'::jsonb, error text
);

create table if not exists public.ingestion_run_items (
  ingestion_run_item_id uuid primary key default gen_random_uuid(),
  ingestion_run_id uuid not null references public.ingestion_runs(ingestion_run_id) on delete cascade,
  dataset_key text not null, status text not null, rows_received integer not null default 0, rows_changed integer not null default 0,
  first_affected_date date, message text, created_at timestamptz not null default now()
);

create table if not exists public.data_quality_issues (
  data_quality_issue_id uuid primary key default gen_random_uuid(), dataset_key text not null,
  severity text not null check (severity in ('info', 'warning', 'error')), issue_type text not null,
  details jsonb not null default '{}'::jsonb, first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(), resolved_at timestamptz
);

create table if not exists public.calculation_runs (
  calculation_run_id uuid primary key default gen_random_uuid(), calculation_type text not null,
  calculation_version text not null, security_id uuid references public.securities(security_id) on delete cascade,
  first_affected_date date, started_at timestamptz not null default now(), finished_at timestamptz,
  status text not null, details jsonb not null default '{}'::jsonb
);

create table if not exists public.dashboard_versions (
  dashboard_version_id uuid primary key default gen_random_uuid(), generated_at timestamptz not null default now(),
  latest_market_date date, is_complete boolean not null, completeness jsonb not null default '{}'::jsonb,
  payload jsonb not null, calculation_version text not null, source_run_id uuid references public.ingestion_runs(ingestion_run_id),
  content_hash text not null unique
);

create table if not exists public.notification_dispatches (
  notification_dispatch_id uuid primary key default gen_random_uuid(),
  dashboard_version_id uuid references public.dashboard_versions(dashboard_version_id) on delete cascade,
  message_type text not null check (message_type in ('morning_report', 'delay_notice')),
  status text not null check (status in ('succeeded', 'failed', 'skipped')), sent_at timestamptz not null default now(),
  response_code integer, error text, dispatch_key text not null unique,
  unique (dashboard_version_id, message_type)
);

create index if not exists market_daily_security_date_desc on public.market_daily (security_id, trade_date desc);
create index if not exists valuation_daily_security_date_desc on public.valuation_daily (security_id, trade_date desc);
create index if not exists asset_daily_signals_security_date_desc on public.asset_daily_signals (security_id, trade_date desc);
create index if not exists source_documents_security_date_desc on public.source_documents (security_id, announcement_date desc);
create index if not exists dashboard_versions_ready_desc on public.dashboard_versions (is_complete, generated_at desc);

alter table public.securities enable row level security;
alter table public.provider_symbol_map enable row level security;
alter table public.market_calendars enable row level security;
alter table public.metric_definitions enable row level security;
alter table public.ingestion_source_records enable row level security;
alter table public.source_documents enable row level security;
alter table public.market_daily enable row level security;
alter table public.valuation_daily enable row level security;
alter table public.financial_facts enable row level security;
alter table public.dividend_events enable row level security;
alter table public.industry_metric_values enable row level security;
alter table public.portfolio_allocations enable row level security;
alter table public.asset_daily_signals enable row level security;
alter table public.style_compass_results enable row level security;
alter table public.fundamental_assessments enable row level security;
alter table public.ingestion_watermarks enable row level security;
alter table public.ingestion_runs enable row level security;
alter table public.ingestion_run_items enable row level security;
alter table public.data_quality_issues enable row level security;
alter table public.calculation_runs enable row level security;
alter table public.dashboard_versions enable row level security;
alter table public.notification_dispatches enable row level security;

-- No browser policy is created intentionally. Edge Functions authenticate the
-- viewer and use server-side credentials; ingestion uses the service role.
