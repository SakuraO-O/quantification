-- A cash dividend belongs to the period named by the disclosure, not to a
-- year inferred from its ex-dividend or announcement date. Existing Sina rows
-- have no reliable report period and stay NULL until the Eastmoney refill.
alter table public.dividend_events
  add column if not exists report_period date,
  add column if not exists is_current boolean not null default false;

comment on column public.dividend_events.report_period is
  'Source-reported period for this distribution; NULL means fiscal attribution is unverified.';
comment on column public.dividend_events.is_current is
  'True only when the latest successful report-period source fetch still contains this event.';

create index if not exists dividend_events_report_period_idx
  on public.dividend_events (security_id, fiscal_year, report_period)
  where report_period is not null and is_current;
