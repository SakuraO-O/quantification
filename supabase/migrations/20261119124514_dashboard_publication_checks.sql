-- Publication attempts are mutable operational records; content versions stay immutable.
alter table public.securities add column if not exists listing_date date;
alter table public.securities add column if not exists listing_source_document_id uuid references public.source_documents;
alter table public.dividend_events add column if not exists approval_date date;

update public.dividend_events set approval_date = announcement_date
where event_stage = 'approved' and verification_status = 'verified'
  and approval_basis is not null and approval_date is null;
update public.dividend_events event set approval_date = approved.approval_date
from (select security_id, plan_key, min(approval_date) as approval_date from public.dividend_events
      where plan_key is not null and approval_date is not null group by security_id, plan_key) approved
where event.security_id = approved.security_id and event.plan_key = approved.plan_key and event.approval_date is null;

create function public.set_dividend_approval_date() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.verification_status = 'verified' and new.approval_basis is not null and new.approval_date is null then
    if new.event_stage = 'approved' then new.approval_date = new.announcement_date;
    elsif new.plan_key is not null then
      select min(approval_date) into new.approval_date from public.dividend_events
        where security_id = new.security_id and plan_key = new.plan_key;
    end if;
  end if;
  return new;
end;
$$;
create trigger dividend_approval_date before insert or update on public.dividend_events
  for each row execute function public.set_dividend_approval_date();

create function public.link_dividend_approval_date() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.evidence_role = 'approval' then
    update public.dividend_events event set approval_date = document.announcement_date
      from public.source_documents document where event.dividend_event_id = new.dividend_event_id
      and document.source_document_id = new.source_document_id
      and event.verification_status = 'verified' and event.approval_basis is not null;
  end if;
  return new;
end;
$$;
create trigger dividend_approval_evidence after insert or update on public.dividend_event_sources
  for each row execute function public.link_dividend_approval_date();

create table public.dashboard_batches (
  batch_id uuid primary key default gen_random_uuid(),
  phase text not null check (phase in ('morning', 'evening', 'manual')),
  business_date date not null default (now() at time zone 'Asia/Shanghai')::date,
  started_at timestamptz not null default now(),
  tasks jsonb not null default '{}'::jsonb
);
create table public.dashboard_publication_checks (
  check_id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references public.dashboard_batches,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
  input_revision bigint not null,
  dashboard_version_id uuid references public.dashboard_versions,
  error text
);
create table public.dashboard_publication_state (
  singleton boolean primary key default true check (singleton),
  input_revision bigint not null default 0,
  lease_id uuid,
  lease_until timestamptz,
  current_check_id uuid references public.dashboard_publication_checks
);
insert into public.dashboard_publication_state(singleton) values(true);
alter table public.dashboard_batches enable row level security;
alter table public.dashboard_publication_checks enable row level security;
alter table public.dashboard_publication_state enable row level security;
revoke all on public.dashboard_batches, public.dashboard_publication_checks, public.dashboard_publication_state from public, anon, authenticated;
grant all on public.dashboard_batches, public.dashboard_publication_checks, public.dashboard_publication_state to service_role;
create index on public.dashboard_publication_checks (finished_at desc) where status = 'succeeded';

create function public.bump_dashboard_input_revision() returns trigger
language plpgsql set search_path = '' as $$
begin
  update public.dashboard_publication_state set input_revision = input_revision + 1 where singleton;
  return null;
end;
$$;
-- Statement triggers cover every input read by the publisher, including source
-- evidence and configuration, so a concurrent update cannot produce a mixed snapshot.
do $$
declare tab text;
begin
  foreach tab in array array['securities','market_calendars','asset_daily_signals',
    'market_daily','dividend_events','financial_facts','source_documents',
    'dividend_year_reviews','dividend_event_sources','fundamental_assessments','ingestion_watermarks','portfolio_allocations'] loop
    execute format('create trigger dashboard_input_changed after insert or update or delete or truncate on public.%I for each statement execute function public.bump_dashboard_input_revision()', tab);
  end loop;
end;
$$;

create function public.record_dashboard_batch_task(p_batch uuid, p_task text, p_status text)
returns void language plpgsql set search_path = '' as $$
begin
  if p_status not in ('running','succeeded','degraded','failed') then raise exception 'invalid task status'; end if;
  update public.dashboard_batches set tasks = jsonb_set(tasks, array[p_task], to_jsonb(p_status)) where batch_id = p_batch;
  if not found then raise exception 'batch not found'; end if;
end;
$$;

create function public.begin_dashboard_publication(p_batch uuid)
returns jsonb language plpgsql set search_path = '' as $$
declare state public.dashboard_publication_state; run_id uuid; tasks jsonb;
begin
  select * into state from public.dashboard_publication_state where singleton for update;
  if state.lease_until > now() then raise exception 'publication already running'; end if;
  select b.tasks into tasks from public.dashboard_batches b where b.batch_id = p_batch;
  if not found then raise exception 'batch not found'; end if;
  if exists (select 1 from jsonb_each_text(tasks) where value not in ('succeeded','degraded')) then
    raise exception 'upstream batch has unfinished or fatal tasks';
  end if;
  -- An expired worker is fenced out even if it later finishes its Python calculation.
  update public.dashboard_publication_checks set status = 'failed', finished_at = now(), error = 'publication lease expired'
    where check_id = state.lease_id and status = 'running';
  insert into public.dashboard_publication_checks(batch_id,input_revision)
    values(p_batch,state.input_revision) returning check_id into run_id;
  update public.dashboard_publication_state set lease_id = run_id, lease_until = now() + interval '20 minutes' where singleton;
  return jsonb_build_object('check_id', run_id, 'input_revision', state.input_revision);
end;
$$;

create function public.finish_dashboard_publication(p_check uuid, p_payload jsonb,
  p_complete boolean, p_completeness jsonb, p_calculation_version text, p_content_hash text,
  p_source_run uuid default null)
returns jsonb language plpgsql set search_path = '' as $$
declare state public.dashboard_publication_state; attempt public.dashboard_publication_checks; version public.dashboard_versions;
begin
  select * into state from public.dashboard_publication_state where singleton for update;
  select * into attempt from public.dashboard_publication_checks where check_id = p_check;
  if state.lease_id is distinct from p_check or state.lease_until <= now() then raise exception 'publication lease lost'; end if;
  if state.input_revision <> attempt.input_revision then raise exception 'publication inputs changed; retry required'; end if;
  if not p_complete then raise exception 'dashboard completeness validation failed'; end if;
  insert into public.dashboard_versions(latest_market_date,is_complete,completeness,payload,calculation_version,source_run_id,content_hash)
    values((p_payload->>'latest_market_date')::date,p_complete,p_completeness,p_payload,p_calculation_version,p_source_run,p_content_hash)
    on conflict(content_hash) do nothing;
  select * into version from public.dashboard_versions where content_hash = p_content_hash;
  update public.dashboard_publication_checks set status = 'succeeded', finished_at = now(), dashboard_version_id = version.dashboard_version_id where check_id = p_check;
  update public.dashboard_publication_state set current_check_id = p_check, lease_id = null, lease_until = null where singleton;
  return to_jsonb(version);
end;
$$;

create function public.fail_dashboard_publication(p_check uuid, p_error text)
returns void language plpgsql set search_path = '' as $$
begin
  perform 1 from public.dashboard_publication_state where singleton for update;
  update public.dashboard_publication_checks set status = 'failed', finished_at = now(), error = p_error where check_id = p_check and status = 'running';
  update public.dashboard_publication_state set lease_id = null, lease_until = null where singleton and lease_id = p_check;
end;
$$;

create function public.dashboard_runtime_status()
returns jsonb language sql stable set search_path = '' as $$
  select jsonb_build_object(
    'current_version_id', current.dashboard_version_id,
    'checked_at', current.finished_at,
    'input_revision', state.input_revision,
    'published_revision', current.input_revision,
    'latest_attempt', (select to_jsonb(c) from public.dashboard_publication_checks c order by c.started_at desc limit 1),
    'calendars', (select coalesce(jsonb_agg(to_jsonb(cal)), '[]'::jsonb) from public.market_calendars cal
      where cal.trade_date = (now() at time zone case cal.market when 'US' then 'America/New_York' when 'HK' then 'Asia/Hong_Kong' else 'Asia/Shanghai' end)::date),
    'latest_batch', (select to_jsonb(b) from public.dashboard_batches b order by started_at desc limit 1),
    'state', case
      when exists (select 1 from jsonb_each_text((select b.tasks from public.dashboard_batches b order by b.started_at desc limit 1)) where value = 'failed') then 'failed'
      when state.lease_until > now() then 'publishing'
      when state.lease_id is not null and state.lease_until <= now() then 'failed'
      when (select c.status from public.dashboard_publication_checks c order by c.started_at desc limit 1) = 'failed' then 'failed'
      when current.input_revision is distinct from state.input_revision then 'pending'
      else 'current' end)
  from public.dashboard_publication_state state
  left join public.dashboard_publication_checks current on current.check_id = state.current_check_id
  where state.singleton;
$$;

-- Per-date idempotency replaces per-version uniqueness. Existing history is kept.
alter table public.notification_dispatches drop constraint if exists notification_dispatches_dashboard_version_id_message_type_key;
alter table public.notification_dispatches drop constraint if exists notification_dispatches_status_check;
alter table public.notification_dispatches add constraint notification_dispatches_status_check
  check(status in ('sending','succeeded','failed','skipped'));
alter table public.notification_dispatches add column if not exists claimed_at timestamptz;

create function public.claim_dashboard_dispatch(p_key text, p_version uuid, p_type text)
returns boolean language plpgsql set search_path = '' as $$
declare claimed uuid;
begin
  insert into public.notification_dispatches(dispatch_key,dashboard_version_id,message_type,status,claimed_at)
    values(p_key,p_version,p_type,'sending',now())
    on conflict(dispatch_key) do update set status = 'sending', claimed_at = now(),
      dashboard_version_id = excluded.dashboard_version_id, error = null
    where public.notification_dispatches.status = 'failed'
    returning notification_dispatch_id into claimed;
  return claimed is not null;
end;
$$;
-- A worker lost after webhook delivery is kept as sending. Automatic replay
-- cannot guarantee exactly-once delivery to a webhook with no idempotency API.

do $$
declare signature regprocedure;
begin
  foreach signature in array array[
    'public.set_dividend_approval_date()'::regprocedure,
    'public.link_dividend_approval_date()'::regprocedure,
    'public.bump_dashboard_input_revision()'::regprocedure,
    'public.record_dashboard_batch_task(uuid,text,text)'::regprocedure,
    'public.begin_dashboard_publication(uuid)'::regprocedure,
    'public.finish_dashboard_publication(uuid,jsonb,boolean,jsonb,text,text,uuid)'::regprocedure,
    'public.fail_dashboard_publication(uuid,text)'::regprocedure,
    'public.dashboard_runtime_status()'::regprocedure,
    'public.claim_dashboard_dispatch(text,uuid,text)'::regprocedure] loop
    execute format('revoke all on function %s from public, anon, authenticated', signature);
    execute format('grant execute on function %s to service_role', signature);
  end loop;
end;
$$;
