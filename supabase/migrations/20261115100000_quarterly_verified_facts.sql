-- Candidate provider values are not formal-report evidence.
alter table public.financial_facts
  add column if not exists verification_status text not null default 'candidate'
    check (verification_status in ('candidate', 'verified')),
  add column if not exists verified_by text,
  add column if not exists verified_at timestamptz;

alter table public.dividend_events
  add column if not exists plan_key text,
  add column if not exists superseded_by_plan_key text,
  add column if not exists version integer not null default 1,
  add column if not exists verification_status text not null default 'candidate'
    check (verification_status in ('candidate', 'verified')),
  add column if not exists approval_basis text
    check (approval_basis in ('shareholder_resolution', 'shareholder_authorized_board'));

-- The old announcement/stage uniqueness prevents a corrected version of the
-- same plan. Reviewed plans use (security, stable plan key, version) instead.
alter table public.dividend_events
  drop constraint if exists dividend_events_security_id_fiscal_year_event_stage_announcement_id_key;

alter table public.dividend_events
  add column if not exists verified_by text,
  add column if not exists verified_at timestamptz;

create unique index if not exists dividend_current_plan_unique
  on public.dividend_events (security_id, plan_key)
  where is_current and plan_key is not null;
create unique index if not exists dividend_plan_version_unique
  on public.dividend_events (security_id, plan_key, version)
  where plan_key is not null;

create table if not exists public.dividend_event_sources (
  dividend_event_id uuid not null references public.dividend_events(dividend_event_id) on delete cascade,
  source_document_id uuid not null references public.source_documents(source_document_id),
  evidence_role text not null check (evidence_role in ('proposal', 'authorization', 'approval', 'implementation', 'correction')),
  primary key (dividend_event_id, source_document_id, evidence_role)
);

create table if not exists public.dividend_year_reviews (
  security_id uuid not null references public.securities(security_id) on delete cascade,
  fiscal_year integer not null,
  status text not null check (status in ('pending', 'authorization_only', 'complete')),
  source_document_id uuid references public.source_documents(source_document_id),
  reviewed_by text,
  reviewed_at timestamptz not null default now(),
  primary key (security_id, fiscal_year),
  constraint completed_year_has_evidence check (status <> 'complete' or source_document_id is not null)
);

alter table public.dividend_event_sources enable row level security;
alter table public.dividend_year_reviews enable row level security;

insert into public.metric_definitions (metric_code, name, unit, definition, requires_confirmation)
values
  ('deducted_net_profit', '扣非归母净利润', 'CNY', '报告期年初至今扣除非经常性损益后归母净利润。', false),
  ('operating_cashflow', '经营活动现金流量净额', 'CNY', '报告期年初至今经营活动现金流量净额总额。', false)
on conflict (metric_code) do nothing;
