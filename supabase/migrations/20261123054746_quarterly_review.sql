-- Quarterly verification is single-user after Auth. All writes go through Edge
-- or the local trusted worker; browser callers never hold a database secret.
alter table public.financial_facts add column review_data jsonb not null default '{}'::jsonb;
alter table public.dividend_events add column review_data jsonb not null default '{}'::jsonb;
alter table public.dividend_year_reviews
  add column version integer not null default 1,
  add column review_note text,
  add column zero_confirmed boolean not null default false,
  add column evidence_scope jsonb not null default '{}'::jsonb;

-- Audit/check-only changes do not invalidate a computed financial snapshot.
-- Actual fact changes still participate in the existing publication fence.
drop trigger dashboard_input_changed on public.financial_facts;
drop trigger dashboard_input_changed on public.dividend_events;
create trigger dashboard_input_changed after insert or delete or truncate on public.financial_facts
 for each statement execute function public.bump_dashboard_input_revision();
create trigger dashboard_input_changed after insert or delete or truncate on public.dividend_events
 for each statement execute function public.bump_dashboard_input_revision();
create trigger dashboard_fact_changed after update on public.financial_facts for each row
 when ((to_jsonb(new)-'review_data') is distinct from (to_jsonb(old)-'review_data')) execute function public.bump_dashboard_input_revision();
create trigger dashboard_fact_changed after update on public.dividend_events for each row
 when ((to_jsonb(new)-'review_data') is distinct from (to_jsonb(old)-'review_data')) execute function public.bump_dashboard_input_revision();

create table public.review_jobs (
  job_id uuid primary key default gen_random_uuid(),
  kind text not null check(kind in ('verification','publish')),
  object_key text not null,
  fact_kind text check(fact_kind in ('financial','dividend')),
  fact_id uuid,
  fact_hash text,
  state text not null check(state in ('queued','running','completed','failed','expired')),
  attempt_seq integer not null default 1,
  lease_token uuid,
  lease_until timestamptz,
  target_revision bigint,
  external_run_id text,
  requested_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  error text,
  result jsonb,
  unique(kind, object_key)
);
create table public.review_events (
  event_id bigint generated always as identity primary key,
  event_type text not null,
  object_key text not null,
  security_id uuid references public.securities(security_id),
  job_id uuid references public.review_jobs(job_id),
  actor text not null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index review_events_lookup on public.review_events(object_key,event_type,event_id desc);
create index review_events_revision on public.review_events(event_id) where event_type='change';
alter table public.review_jobs enable row level security;
alter table public.review_events enable row level security;
revoke all on public.review_jobs,public.review_events from public,anon,authenticated;
grant all on public.review_jobs,public.review_events to service_role;
grant usage,select on sequence public.review_events_event_id_seq to service_role;

create view public.quarterly_review_records with (security_invoker=true) as
with facts as (
 select 'financial'::text kind,f.financial_fact_id id,f.security_id,f.report_period,
   extract(year from f.report_period)::int fiscal_year,f.metric_code,
   f.value::text value,f.unit,f.period_type,f.version,f.verification_status,
   f.verified_by,f.verified_at,f.source_document_id,f.review_data,f.created_at,
   jsonb_build_object('period_type',f.period_type) fields
 from public.financial_facts f where f.is_current and f.metric_code in
   ('net_profit','deducted_net_profit','revenue','operating_cashflow','roe','asset_liability_ratio')
 union all
 select 'dividend',d.dividend_event_id,d.security_id,d.report_period,d.fiscal_year,'cash_dividend_per_share',
   d.cash_dividend_per_share::text,'CNY/share','event',d.version,d.verification_status,
   d.verified_by,d.verified_at,d.source_document_id,d.review_data,d.created_at,
   jsonb_build_object('announcement_id',d.announcement_id,'plan_key',d.plan_key,'event_stage',d.event_stage,'approval_basis',d.approval_basis,
    'cash_dividend_total',d.cash_dividend_total::text,'ex_date',d.ex_date,
    'bonus_shares_per_share',d.bonus_shares_per_share::text,
    'transfer_shares_per_share',d.transfer_shares_per_share::text,'announcement_date',d.announcement_date)
 from public.dividend_events d where d.is_current and d.superseded_by_plan_key is null
), scoped as (
 select f.*,s.symbol,s.name,s.market,sd.source,sd.document_url,sd.content_hash,sd.announcement_date,
  sd.document_type,coalesce(m.name,'每股现金分红') metric_name,
  case when f.kind='financial' then 'financial:'||f.security_id||':'||f.report_period||':'||f.metric_code
   else 'dividend:'||f.security_id||':'||coalesce(f.fields->>'plan_key',f.fields->>'announcement_id',f.id::text) end object_key
 from facts f join public.securities s using(security_id)
 left join public.source_documents sd using(source_document_id)
 left join public.metric_definitions m using(metric_code)
 where s.is_active and s.asset_type='股票' and coalesce(f.fiscal_year,9999)>=
  coalesce((select extract(year from max(x.report_period))::int-10 from public.financial_facts x where x.security_id=f.security_id),extract(year from current_date)::int-10)
)
select x.*,
 case when verification_status='verified' then coalesce(review_data->>'method','legacy')
   else coalesce(review_data->>'status','pending') end status,
 case coalesce(review_data->>'status','pending') when 'changed' then 1 when 'diff' then 2 when 'basis' then 3 when 'failed' then 4 else 5 end status_order,
 coalesce(verified_at,(review_data->>'checked_at')::timestamptz,created_at) review_updated_at,
 md5(jsonb_build_array(id,report_period,metric_code,value,unit,period_type,version,source_document_id,fields)::text) fact_hash,
 coalesce((value is not null and report_period is not null and source_document_id is not null and
  case when kind='financial' then
    unit=case when metric_code in ('roe','asset_liability_ratio') then 'percent' else 'CNY' end
    and period_type=case when to_char(report_period,'MM-DD')='12-31' then 'annual' else 'year_to_date' end
    and to_char(report_period,'MM-DD') in ('03-31','06-30','09-30','12-31')
  else fiscal_year=extract(year from report_period)::int and fields->>'plan_key' is not null and fields->>'plan_key' not like 'review:%'
    and fields->>'cash_dividend_total' is not null
    and ((fields->>'event_stage'='implemented' and fields->>'ex_date' is not null)
      or (fields->>'event_stage'='approved' and fields->>'approval_basis' is not null)
      or fields->>'event_stage'='proposal') end),false) metadata_complete
from scoped x;
revoke all on public.quarterly_review_records from public,anon,authenticated;
grant select on public.quarterly_review_records to service_role;

create function public.review_revision() returns bigint language sql stable set search_path='' as $$
  select coalesce(max(event_id),0) from public.review_events where event_type='change'
$$;
create function public.review_published_revision(p_version uuid default null) returns bigint
language sql stable set search_path='' as $$
  select coalesce((select (v.payload->>'review_revision')::bigint
    from public.dashboard_versions v where v.dashboard_version_id=coalesce(p_version,
      (select c.dashboard_version_id from public.dashboard_publication_state s join public.dashboard_publication_checks c on c.check_id=s.current_check_id where s.singleton))),0)
$$;
create function public.review_event(p_type text,p_key text,p_security uuid,p_actor text,p_data jsonb default '{}')
returns bigint language plpgsql set search_path='' as $$
declare id bigint;
begin
  insert into public.review_events(event_type,object_key,security_id,actor,data)
    values(p_type,p_key,p_security,p_actor,coalesce(p_data,'{}')) returning event_id into id;
  if p_type='change' then
    update public.dashboard_publication_state set input_revision=input_revision+1 where singleton;
  end if;
  return id;
end $$;

create function public.review_item(p_kind text,p_id uuid) returns jsonb language plpgsql stable set search_path='' as $$
declare item jsonb;
begin
 select to_jsonb(r) into item from public.quarterly_review_records r where kind=p_kind and id=p_id;
 if item is null then raise exception 'review_item_not_current'; end if;
 return item;
end $$;

create function public.review_evidence(p_id uuid,p_security uuid) returns jsonb language plpgsql stable set search_path='' as $$
declare d jsonb;
begin
 select to_jsonb(s) into d from public.source_documents s where source_document_id=p_id and security_id=p_security;
 if d is null or coalesce(d->>'document_url','')='' or coalesce(d->>'content_hash','')='' then
  raise exception 'review_evidence_required';
 end if;
 return d;
end $$;

create function public.review_touch_year(p_security uuid,p_year int,p_actor text,p_reason text)
returns void language plpgsql set search_path='' as $$
begin
 if p_year is null then return; end if;
 insert into public.dividend_year_reviews(security_id,fiscal_year,status,version,review_note)
 values(p_security,p_year,'pending',1,p_reason)
 on conflict(security_id,fiscal_year) do update set status='pending',version=public.dividend_year_reviews.version+1,
  reviewed_at=now(),review_note=excluded.review_note;
 perform public.review_event('change','annual:'||p_security||':'||p_year,p_security,p_actor,
  jsonb_build_object('kind','annual','fiscal_year',p_year,'reason',p_reason,'invalidate',true));
end $$;

-- Every adopted version is retained. This helper accepts only the finite set of
-- editable fact fields; client JSON can never inject IDs or verification flags.
create function public.review_replace(p_kind text,p_id uuid,p_changes jsonb,p_verified boolean,p_actor text,p_data jsonb)
returns jsonb language plpgsql set search_path='' as $$
declare old jsonb; fresh uuid; sid uuid; period date; ver int; old_year int; target uuid;
begin
 old:=public.review_item(p_kind,p_id);sid:=(old->>'security_id')::uuid;
 -- Serialize a security's logical facts, including edits that move a period.
 perform 1 from public.securities where security_id=sid for update;
 old:=public.review_item(p_kind,p_id);period:=coalesce((p_changes->>'report_period')::date,(old->>'report_period')::date);
 old_year:=(old->>'fiscal_year')::int;
 if p_kind='financial' then
  if exists(select 1 from public.financial_facts where security_id=sid and report_period=period
     and metric_code=old->>'metric_code' and is_current and financial_fact_id<>p_id) then raise exception 'review_target_exists'; end if;
  select coalesce(max(version),0)+1 into ver from public.financial_facts
    where security_id=sid and report_period=period and metric_code=old->>'metric_code';
  update public.financial_facts set is_current=false where financial_fact_id=p_id;
  insert into public.financial_facts(security_id,report_period,metric_code,value,unit,period_type,
    announcement_date,source_document_id,version,is_current,verification_status,verified_by,verified_at,review_data)
   values(sid,period,old->>'metric_code',coalesce(p_changes->>'value',old->>'value')::numeric,
    coalesce(p_changes->>'unit',old->>'unit'),coalesce(p_changes->>'period_type',old->>'period_type'),
    coalesce((select announcement_date from public.source_documents where source_document_id=(p_changes->>'source_document_id')::uuid),(old->>'announcement_date')::date),coalesce((p_changes->>'source_document_id')::uuid,(old->>'source_document_id')::uuid),
    ver,true,case when p_verified then 'verified' else 'candidate' end,
    case when p_verified then p_actor end,case when p_verified then now() end,coalesce(p_data,'{}'))
   returning financial_fact_id into fresh;
 elsif p_kind='dividend' then
  select dividend_event_id into target from public.dividend_events where security_id=sid and is_current
    and dividend_event_id<>p_id and plan_key=coalesce(p_changes->>'plan_key',old->'fields'->>'plan_key') for update;
  if target is not null then
   if p_changes->>'supersedes_id' is distinct from target::text then raise exception 'review_plan_link_required'; end if;
   if exists(select 1 from public.review_jobs where fact_id=target and state in ('queued','running')) then raise exception 'review_task_active'; end if;
   update public.dividend_events set is_current=false,superseded_by_plan_key=p_changes->>'plan_key' where dividend_event_id=target;
  end if;
  update public.dividend_events set is_current=false,superseded_by_plan_key=coalesce(p_changes->>'plan_key',plan_key,'review:'||p_id) where dividend_event_id=p_id;
  insert into public.dividend_events(security_id,report_period,fiscal_year,event_stage,announcement_id,plan_key,
    cash_dividend_per_share,cash_dividend_total,ex_date,payment_date,announcement_date,
    bonus_shares_per_share,transfer_shares_per_share,approval_basis,source_document_id,version,is_current,
    verification_status,verified_by,verified_at,review_data)
  select sid,period,extract(year from period)::int,
    coalesce(p_changes->>'event_stage',d.event_stage),d.announcement_id,coalesce(p_changes->>'plan_key',d.plan_key,'review:'||p_id),
    coalesce(p_changes->>'value',d.cash_dividend_per_share::text)::numeric,
    coalesce(p_changes->>'cash_dividend_total',d.cash_dividend_total::text)::numeric,
    coalesce(p_changes->>'ex_date',d.ex_date::text)::date,d.payment_date,coalesce((select announcement_date from public.source_documents where source_document_id=(p_changes->>'source_document_id')::uuid),d.announcement_date),
    coalesce(p_changes->>'bonus_shares_per_share',d.bonus_shares_per_share::text)::numeric,
    coalesce(p_changes->>'transfer_shares_per_share',d.transfer_shares_per_share::text)::numeric,
    coalesce(p_changes->>'approval_basis',d.approval_basis),
    coalesce((p_changes->>'source_document_id')::uuid,d.source_document_id),
    (select coalesce(max(z.version),0)+1 from public.dividend_events z where z.security_id=sid
      and coalesce(z.plan_key,'review:'||p_id)=coalesce(p_changes->>'plan_key',d.plan_key,'review:'||p_id)),
    true,case when p_verified then 'verified' else 'candidate' end,
    case when p_verified then p_actor end,case when p_verified then now() end,coalesce(p_data,'{}')
   from public.dividend_events d where d.dividend_event_id=p_id returning dividend_event_id into fresh;
  insert into public.dividend_event_sources(dividend_event_id,source_document_id,evidence_role)
    select fresh,source_document_id,evidence_role from public.dividend_event_sources where dividend_event_id=p_id or dividend_event_id=target on conflict do nothing;
  perform public.review_touch_year(sid,old_year,p_actor,'分红事实变更，需重新查全');
  if old_year<>extract(year from period)::int then
    perform public.review_touch_year(sid,extract(year from period)::int,p_actor,'分红报告年度变更');
  end if;
 else raise exception 'invalid_fact_kind'; end if;
 perform public.review_event('change',old->>'object_key',sid,p_actor,
  jsonb_build_object('kind',p_kind,'report_period',old->>'report_period','metric_code',old->>'metric_code',
   'fiscal_year',old_year,'reason',case when p_verified then '核实完成' else '旧依据失效，重新核实' end,
   'invalidate',not p_verified,'old_id',p_id,'new_id',fresh,'old_value',old->>'value','new_value',p_changes->>'value'));
 return public.review_item(p_kind,fresh);
end $$;

create function public.review_enqueue(p_kind text,p_id uuid,p_actor text) returns jsonb language plpgsql set search_path='' as $$
declare item jsonb; job public.review_jobs; key text;
begin
 item:=public.review_item(p_kind,p_id);
 perform 1 from public.securities where security_id=(item->>'security_id')::uuid for update;
 item:=public.review_item(p_kind,p_id);
 if item->>'verification_status'='verified' then raise exception 'review_already_verified'; end if;
 key:=p_kind||':'||p_id||':'||(item->>'fact_hash');
 select * into job from public.review_jobs where kind='verification' and object_key=key for update;
 if found and (job.state='queued' or (job.state='running' and job.lease_until>now())) then return to_jsonb(job); end if;
 insert into public.review_jobs(kind,object_key,fact_kind,fact_id,fact_hash,state,requested_by)
 values('verification',key,p_kind,p_id,item->>'fact_hash','queued',p_actor)
 on conflict on constraint review_jobs_kind_object_key_key do update set state='queued',attempt_seq=public.review_jobs.attempt_seq+1,
  lease_token=null,lease_until=null,error=null,result=null,requested_by=p_actor,updated_at=now()
 returning * into job;
 if p_kind='financial' then update public.financial_facts set review_data=review_data-'ready' where financial_fact_id=p_id;
 else update public.dividend_events set review_data=review_data-'ready' where dividend_event_id=p_id; end if;
 perform public.review_event('attempt',item->>'object_key',(item->>'security_id')::uuid,p_actor,
  jsonb_build_object('job_id',job.job_id,'attempt_seq',job.attempt_seq,'state','queued'));
 return to_jsonb(job);
end $$;
create function public.review_annual_records() returns jsonb language sql stable set search_path='' as $$
 with scope as (
  select s.security_id,s.symbol,s.name,coalesce(extract(year from max(f.report_period))::int,extract(year from current_date)::int) last_year
   from public.securities s left join public.financial_facts f using(security_id)
   where s.is_active and s.asset_type='股票' group by s.security_id
 ), years as (
  select s.*,y fiscal_year from scope s cross join lateral generate_series(s.last_year-9,s.last_year) y
 ), rows as (
  select y.*,coalesce(r.status,'pending') status,coalesce(r.version,0) version,r.source_document_id,
   r.reviewed_at,r.review_note,r.zero_confirmed,r.evidence_scope,
   exists(select 1 from public.source_documents d where d.security_id=y.security_id
    and d.report_period=make_date(y.fiscal_year,12,31)
    and d.document_type in ('annual_report','financial_report','structured_financial_snapshot','structured_cashflow_snapshot')
    and make_date(y.fiscal_year,12,31)<current_date) ready,
   (select count(*) from public.dividend_events d where d.security_id=y.security_id and d.fiscal_year=y.fiscal_year
    and d.is_current and d.superseded_by_plan_key is null and (d.verification_status<>'verified' or d.event_stage='proposal')) unresolved,
   (select count(*) from public.dividend_events d where d.security_id=y.security_id and d.fiscal_year=y.fiscal_year
    and d.is_current and d.superseded_by_plan_key is null
    and d.cash_dividend_per_share>0) positive_plans
  from years y left join public.dividend_year_reviews r using(security_id,fiscal_year)
 ) select coalesce(jsonb_agg(to_jsonb(rows) order by case when ready and status<>'complete' then 0 when status='complete' then 1 else 2 end,fiscal_year desc,name),'[]') from rows
$$;

create function public.review_overview(p_version uuid default null) returns jsonb language sql stable set search_path='' as $$
 with annual as (select value item from jsonb_array_elements(public.review_annual_records())),
 changes as (select distinct on(object_key) * from public.review_events
   where event_type='change' and event_id>public.review_published_revision(p_version) order by object_key,event_id desc)
 select jsonb_build_object('revision',public.review_revision(),'published_revision',public.review_published_revision(p_version),
  'pending_facts',(select count(*) from public.quarterly_review_records where verification_status<>'verified'),
  'pending_years',(select count(*) from annual where (item->>'ready')::boolean and item->>'status'<>'complete'),
  'waiting_years',(select count(*) from annual where not (item->>'ready')::boolean),
  'auto',(select count(*) from public.quarterly_review_records where status='auto'),
  'manual',(select count(*) from public.quarterly_review_records where status in ('manual','legacy')),
  'pending_publish',(select count(*) from changes),
  'invalidations',(select coalesce(jsonb_agg(jsonb_build_object('security_id',security_id,'symbol',(select s.symbol from public.securities s where s.security_id=invalid.security_id),'object_key',object_key)||data),'[]')
    from (select distinct on(object_key) * from public.review_events where event_type='change' and event_id>public.review_published_revision(p_version) and (data->>'invalidate')::boolean order by object_key,event_id desc) invalid),
  'latest_publish_job',(select to_jsonb(j)-'lease_token' from public.review_jobs j where kind='publish' order by updated_at desc limit 1),
  'latest_job',(select to_jsonb(j) from public.review_jobs j order by updated_at desc limit 1),
  'worker',(select data||jsonb_build_object('seen_at',created_at) from public.review_events where event_type='heartbeat' order by event_id desc limit 1))
$$;

create function public.review_number(p_text text,p_scale int,p_nonnegative boolean default false) returns numeric
language plpgsql immutable set search_path='' as $$
declare n numeric;
begin
 if p_text is null or length(p_text)>70 or p_text !~ '^[+-]?[0-9]+(\.[0-9]+)?$' then raise exception 'review_invalid_value'; end if;
 n:=p_text::numeric;
 if scale(n)>p_scale or (p_nonnegative and n<0) then raise exception 'review_invalid_precision_or_sign'; end if;
 return n;
end $$;

create function public.review_command(p_action text,p_payload jsonb,p_actor text) returns jsonb
language plpgsql set search_path='' as $$
declare item jsonb; x jsonb; results jsonb:='[]'; sid uuid; evidence jsonb; fresh jsonb;
 kind text; id uuid; note text; changes jsonb; original_input jsonb; value numeric; year int; annual jsonb; job public.review_jobs;
begin
 if coalesce(p_actor,'')='' then raise exception 'review_actor_required'; end if;
 if p_action='enqueue' then
  if jsonb_typeof(p_payload->'items') is distinct from 'array' or jsonb_array_length(p_payload->'items') not between 1 and 100 then raise exception 'review_batch_size'; end if;
  for x in select e.value from jsonb_array_elements(p_payload->'items') e loop
   results:=results||jsonb_build_array(public.review_enqueue(x->>'kind',(x->>'id')::uuid,p_actor));
  end loop;
  return results;
 elsif p_action in ('confirm','mark_ready','metadata') then
  if jsonb_array_length(p_payload->'items') not between 1 and 100 then raise exception 'review_batch_size'; end if;
  -- A failed item rolls back the whole RPC, including any previous item writes.
  for x in select e.value from jsonb_array_elements(p_payload->'items') e order by e.value->>'security_id',e.value->>'id' loop
   kind:=x->>'kind';id:=(x->>'id')::uuid;item:=public.review_item(kind,id);sid:=(item->>'security_id')::uuid;
   perform 1 from public.securities where security_id=sid for update;
   item:=public.review_item(kind,id);
   if x->>'fact_hash' is distinct from item->>'fact_hash' or item->>'verification_status'='verified' then raise exception 'review_version_changed'; end if;
   if exists(select 1 from public.review_jobs j where j.fact_id=id and
       (j.state='queued' or (j.state='running' and j.lease_until>now()))) then raise exception 'review_task_active'; end if;
   note:=btrim(coalesce(x->>'note',''));if length(note) not between 1 and 1000 then raise exception 'review_note_required'; end if;
   evidence:=public.review_evidence((x->>'evidence_id')::uuid,sid);
   changes:=coalesce(x->'changes','{}');
   if evidence->>'report_period' is distinct from coalesce(changes->>'report_period',item->>'report_period') then raise exception 'review_evidence_period_mismatch'; end if;
   if not coalesce((x->>'reviewed')::boolean,false) then raise exception 'review_evidence_not_reviewed'; end if;
   if p_action='metadata' then
    original_input:=changes;
    if changes->>'unit' in ('万元','亿元','百万元') then
     value:=public.review_number(changes->>'value',14,false)*(case changes->>'unit' when '万元' then 10000 when '亿元' then 100000000 else 1000000 end);
     changes:=changes||jsonb_build_object('unit','CNY','value',value::text);
    end if;
    if changes->>'currency' is distinct from 'CNY' then raise exception 'review_currency_unsupported'; end if;
    if to_char((changes->>'report_period')::date,'MM-DD') not in ('03-31','06-30','09-30','12-31') then raise exception 'review_invalid_period'; end if;
    value:=public.review_number(changes->>'value',case when item->>'unit'='CNY' then 14 else 6 end,kind='dividend');
    if value is null or value::text in ('NaN','Infinity','-Infinity') then raise exception 'review_invalid_value'; end if;
    if kind='dividend' then
     if changes->>'unit' is distinct from 'CNY/share' or changes->>'period_type' is distinct from 'event' then raise exception 'review_invalid_basis'; end if;
     perform public.review_number(changes->>'cash_dividend_total',14,true);
     perform public.review_number(changes->>'bonus_shares_per_share',6,true);
     perform public.review_number(changes->>'transfer_shares_per_share',6,true);
    end if;
    if kind='financial' and changes->>'unit' is distinct from
     (case when item->>'metric_code' in ('roe','asset_liability_ratio') then 'percent' else 'CNY' end) then raise exception 'review_invalid_unit'; end if;
    if kind='financial' and changes->>'period_type' is distinct from
     (case when to_char((changes->>'report_period')::date,'MM-DD')='12-31' then 'annual' else 'year_to_date' end) then raise exception 'review_invalid_basis'; end if;
    fresh:=public.review_replace(kind,id,changes||jsonb_build_object('source_document_id',evidence->>'source_document_id'),false,p_actor,
      jsonb_build_object('status','pending','note',note,'evidence_id',evidence->>'source_document_id','original_input',original_input));
    if not (fresh->>'metadata_complete')::boolean then raise exception 'review_metadata_incomplete'; end if;
   else
    if not (item->>'metadata_complete')::boolean then raise exception 'review_metadata_incomplete'; end if;
    if not coalesce((x->>'reviewed')::boolean,false) then raise exception 'review_evidence_not_reviewed'; end if;
    if p_action='mark_ready' then
     if x->>'mode' is distinct from 'candidate' then raise exception 'review_batch_mode'; end if;
     if kind='financial' then update public.financial_facts set review_data=review_data||jsonb_build_object(
      'ready',true,'note',note,'evidence_id',evidence->>'source_document_id') where financial_fact_id=id;
     else update public.dividend_events set review_data=review_data||jsonb_build_object(
      'ready',true,'note',note,'evidence_id',evidence->>'source_document_id') where dividend_event_id=id; end if;
     fresh:=public.review_item(kind,id);
    else
     if jsonb_array_length(p_payload->'items')>1 and not coalesce((item->'review_data'->>'ready')::boolean,false) then raise exception 'review_batch_not_ready'; end if;
     if jsonb_array_length(p_payload->'items')>1 and x->>'mode' is distinct from 'candidate' then raise exception 'review_batch_mode'; end if;
     if x->>'mode'='candidate' then value:=(item->>'value')::numeric;
     elsif x->>'mode'='reference' then
      if item->'review_data'->'reference'->>'unit' is distinct from item->>'unit'
       or item->'review_data'->'reference'->>'report_period' is distinct from item->>'report_period'
       or item->'review_data'->'reference'->>'period_type' is distinct from item->>'period_type'
       or (item->>'unit'<>'percent' and item->'review_data'->'reference'->>'currency' is distinct from 'CNY')
       or item->'review_data'->'reference'->>'wind_code' is distinct from (right(item->>'symbol',6)||(case when left(item->>'symbol',2)='sh' then '.SH' else '.SZ' end))
       then raise exception 'review_reference_not_comparable'; end if;
      value:=(item->'review_data'->'reference'->>'value')::numeric;
     elsif x->>'mode'='custom' then value:=(x->>'value')::numeric;
     else raise exception 'review_invalid_mode'; end if;
     value:=public.review_number(value::text,case when item->>'unit'='CNY' then 14 else 6 end,kind='dividend');
     fresh:=public.review_replace(kind,id,jsonb_build_object('value',value::text,'source_document_id',evidence->>'source_document_id'),true,p_actor,
      jsonb_build_object('method','manual','note',note,'reference',item->'review_data'->'reference','original',item));
    end if;
   end if;
   perform public.review_event(p_action,item->>'object_key',sid,p_actor,
    jsonb_build_object('original',item,'result_id',fresh->>'id','note',note,'evidence_id',evidence->>'source_document_id'));
   results:=results||jsonb_build_array(fresh);
  end loop;
  return results;
 elsif p_action='annual' then
  sid:=(p_payload->>'security_id')::uuid;year:=(p_payload->>'fiscal_year')::int;
  perform 1 from public.securities where security_id=sid and is_active and asset_type='股票' for update;
  select e.value into annual from jsonb_array_elements(public.review_annual_records()) e where
    e.value->>'security_id'=sid::text and (e.value->>'fiscal_year')::int=year;
  if annual is null or not (annual->>'ready')::boolean or (annual->>'unresolved')::int>0 then raise exception 'review_year_not_ready'; end if;
  if (annual->>'version')::int<>(p_payload->>'version')::int then raise exception 'review_year_changed'; end if;
  if annual->>'status'='complete' then raise exception 'review_year_already_complete'; end if;
  if not coalesce((p_payload->>'scope_checked')::boolean,false) then raise exception 'review_year_scope_required'; end if;
  evidence:=public.review_evidence((p_payload->>'evidence_id')::uuid,sid);
  if evidence->>'report_period' is distinct from make_date(year,12,31)::text then raise exception 'review_evidence_period_mismatch'; end if;
  if evidence->>'document_type' like 'structured_%' or length(evidence->>'content_hash')<>64 then raise exception 'review_formal_evidence_required'; end if;
  if p_payload->>'conclusion'='zero' then
   if (annual->>'positive_plans')::int>0 or not coalesce((p_payload->>'zero_confirmed')::boolean,false) then raise exception 'review_year_zero_evidence'; end if;
  elsif p_payload->>'conclusion'='with_dividend' then
   if (annual->>'positive_plans')::int=0 then raise exception 'review_year_no_dividend'; end if;
  else raise exception 'review_year_conclusion_required'; end if;
  note:=btrim(coalesce(p_payload->>'note',''));if length(note) not between 1 and 1000 then raise exception 'review_note_required'; end if;
  insert into public.dividend_year_reviews(security_id,fiscal_year,status,source_document_id,version,reviewed_by,reviewed_at,review_note,zero_confirmed,evidence_scope)
  values(sid,year,'complete',(evidence->>'source_document_id')::uuid,1,p_actor,now(),note,
    coalesce((p_payload->>'zero_confirmed')::boolean,false),jsonb_build_object('scope_checked',true,'conclusion',p_payload->>'conclusion'))
  on conflict(security_id,fiscal_year) do update set status='complete',source_document_id=excluded.source_document_id,
    version=public.dividend_year_reviews.version+1,reviewed_by=p_actor,reviewed_at=now(),review_note=note,
    zero_confirmed=excluded.zero_confirmed,evidence_scope=excluded.evidence_scope;
  perform public.review_event('change','annual:'||sid||':'||year,sid,p_actor,
    jsonb_build_object('kind','annual','fiscal_year',year,'reason','年度查全完成','invalidate',false,'note',note));
  return public.review_overview();
 elsif p_action='publish' then
  -- One active publish request; a terminal request gets a new attempt.
  insert into public.review_jobs(kind,object_key,state,target_revision,requested_by)
   values('publish','dashboard','queued',public.review_revision(),p_actor)
  on conflict on constraint review_jobs_kind_object_key_key do update set
   state=case when public.review_jobs.state in ('queued','running') and coalesce(public.review_jobs.lease_until,now()+interval '1 minute')>now() then public.review_jobs.state else 'queued' end,
   attempt_seq=case when public.review_jobs.state in ('queued','running') and coalesce(public.review_jobs.lease_until,now()+interval '1 minute')>now() then public.review_jobs.attempt_seq else public.review_jobs.attempt_seq+1 end,
   target_revision=case when public.review_jobs.state in ('queued','running') and coalesce(public.review_jobs.lease_until,now()+interval '1 minute')>now() then public.review_jobs.target_revision else excluded.target_revision end,
   lease_until=case when public.review_jobs.state in ('queued','running') and coalesce(public.review_jobs.lease_until,now()+interval '1 minute')>now() then public.review_jobs.lease_until else null end,
   lease_token=case when public.review_jobs.state in ('queued','running') and coalesce(public.review_jobs.lease_until,now()+interval '1 minute')>now() then public.review_jobs.lease_token else null end,
   error=null,result=null,requested_by=p_actor,updated_at=now()
   returning * into job;
  return to_jsonb(job);
 else raise exception 'review_unknown_action'; end if;
end $$;

create function public.review_claim(p_kind text,p_job uuid default null,p_attempt int default null)
returns jsonb language plpgsql set search_path='' as $$
declare j public.review_jobs; item jsonb;
begin
 select * into j from public.review_jobs where kind=p_kind and (p_job is null or job_id=p_job)
  and (p_attempt is null or attempt_seq=p_attempt)
  and (state='queued' or (state='running' and lease_until<now()))
  order by updated_at for update skip locked limit 1;
 if not found then return null; end if;
 update public.review_jobs set state='running',lease_token=gen_random_uuid(),
  attempt_seq=attempt_seq+case when j.state='running' then 1 else 0 end,
  lease_until=now()+case when p_kind='publish' then interval '30 minutes' else interval '10 minutes' end,
  updated_at=now() where job_id=j.job_id returning * into j;
 if p_kind='verification' then
  begin item:=public.review_item(j.fact_kind,j.fact_id);
   if item->>'fact_hash'<>j.fact_hash or item->>'verification_status'='verified' then raise exception 'stale'; end if;
  exception when others then
   update public.review_jobs set state='expired',error='事实版本已变化' where job_id=j.job_id;
   return null;
  end;
 end if;
 return to_jsonb(j)||jsonb_build_object('item',item);
end $$;

create function public.review_complete(p_job uuid,p_attempt int,p_lease uuid,p_result jsonb)
returns jsonb language plpgsql set search_path='' as $$
declare j public.review_jobs; item jsonb; fresh jsonb; sid uuid; status text; data jsonb;
begin
 -- Match lock ordering used by manual edits and enqueue (security before job).
 select * into j from public.review_jobs where job_id=p_job;
 if j.fact_id is not null then
  begin item:=public.review_item(j.fact_kind,j.fact_id);
   perform 1 from public.securities where security_id=(item->>'security_id')::uuid for update;
  exception when others then item:=null; end;
 end if;
 select * into j from public.review_jobs where job_id=p_job for update;
 if not found then raise exception 'review_job_missing'; end if;
 if j.attempt_seq=p_attempt and j.lease_token=p_lease and j.state='completed' then return j.result; end if;
 if j.state<>'running' or j.attempt_seq<>p_attempt or j.lease_token is distinct from p_lease or j.lease_until<now() then
  perform public.review_event('stale_result',j.object_key,null,'worker',jsonb_build_object('job_id',p_job,'attempt_seq',p_attempt));
  return jsonb_build_object('stale',true);
 end if;
 if j.kind='publish' then
  if p_result->>'status'='succeeded' then
   if not exists(select 1 from public.dashboard_versions v where v.dashboard_version_id=(p_result->>'version')::uuid
      and v.is_complete and coalesce((v.payload->>'review_revision')::bigint,0)>=j.target_revision) then raise exception 'review_publication_not_complete'; end if;
   update public.review_jobs set state='completed',result=p_result,external_run_id=p_result->>'run_id',updated_at=now()
    where job_id=p_job;
   perform public.review_event('publication_result','dashboard',null,'cloud-worker',p_result||jsonb_build_object('job_id',p_job,'attempt_seq',p_attempt));
   return p_result;
  end if;
  perform public.review_event('publication_result','dashboard',null,'cloud-worker',p_result||jsonb_build_object('job_id',p_job,'attempt_seq',p_attempt));
  update public.review_jobs set state='failed',error=left(p_result->>'reason',1000),updated_at=now(),result=p_result where job_id=p_job;
  return p_result;
 end if;
 if item is null or item->>'fact_hash' is distinct from j.fact_hash then
  update public.review_jobs set state='expired',error='事实版本已变化',updated_at=now() where job_id=p_job;
  return jsonb_build_object('stale',true);
 end if;
 sid:=(item->>'security_id')::uuid;status:=p_result->>'status';
 if status not in ('auto','diff','basis','failed') then raise exception 'review_invalid_result'; end if;
 data:=jsonb_build_object('status',status,'reason',left(p_result->>'reason',1000),
  'reference',p_result->'reference','checked_at',now(),'job_id',p_job,'attempt_seq',p_attempt,
  'rule_version',p_result->>'rule_version');
 if status='auto' then
  if not (item->>'metadata_complete')::boolean or p_result->'reference' is null
    or coalesce(p_result->>'rule_version','')='' then raise exception 'review_invalid_auto_evidence'; end if;
  fresh:=public.review_replace(j.fact_kind,j.fact_id,'{}',true,'auto:wind',data||jsonb_build_object('method','auto'));
 else
  if j.fact_kind='financial' then update public.financial_facts set review_data=review_data||data where financial_fact_id=j.fact_id;
  else update public.dividend_events set review_data=review_data||data where dividend_event_id=j.fact_id; end if;
  fresh:=public.review_item(j.fact_kind,j.fact_id);
 end if;
 perform public.review_event('verification',item->>'object_key',sid,'worker',data||jsonb_build_object('original_id',j.fact_id,'result_id',fresh->>'id'));
 update public.review_jobs set state=case when status='failed' then 'failed' else 'completed' end,
  updated_at=now(),result=fresh,error=case when status='failed' then p_result->>'reason' end where job_id=p_job;
 return fresh;
end $$;

create function public.review_publish_dispatched(p_job uuid,p_attempt int,p_error text default null)
returns void language plpgsql set search_path='' as $$
begin
 update public.review_jobs set state=case when p_error is null then 'queued' else 'failed' end,
  error=p_error,lease_until=now()+interval '10 minutes',updated_at=now()
 where job_id=p_job and attempt_seq=p_attempt and state='queued';
 if found then perform public.review_event('publication_dispatch','dashboard',null,'edge',jsonb_build_object('job_id',p_job,'attempt_seq',p_attempt,'error',p_error,'state',case when p_error is null then 'queued' else 'failed' end)); end if;
end $$;
create function public.review_reap() returns void language plpgsql set search_path='' as $$
begin
 update public.review_jobs set state='failed',error='云端工作流未按时启动或执行超时',updated_at=now()
 where kind='publish' and state in ('queued','running') and coalesce(lease_until,created_at+interval '10 minutes')<now();
end $$;

-- Observation identity belongs to a provider, not the adopted fact. Polling a
-- previously rejected provider value must never undo a manual correction.
create function public.review_observe(p_kind text,p_security uuid,p_payload jsonb,p_actor text default 'source')
returns jsonb language plpgsql set search_path='' as $$
declare key text; source_key text; prior jsonb; observation jsonb; current_item jsonb; current_id uuid;
 payload jsonb; document jsonb; item_hash text; old_source jsonb; baseline text; d_id uuid;
begin
 perform 1 from public.securities where security_id=p_security for update;
 if p_kind not in ('financial','dividend') then raise exception 'review_invalid_kind'; end if;
 payload:=p_payload->'fact';
 perform public.review_number(payload->>'value',14,p_kind='dividend');
 d_id:=(p_payload->>'source_document_id')::uuid;
 select to_jsonb(d) into document from public.source_documents d where source_document_id=d_id and security_id=p_security;
 if document is null then raise exception 'review_source_document_missing'; end if;
 if p_kind='financial' then
  key:='financial:'||p_security||':'||(payload->>'report_period')||':'||(payload->>'metric_code');
  select financial_fact_id into current_id from public.financial_facts where security_id=p_security
   and report_period=(payload->>'report_period')::date and metric_code=payload->>'metric_code' and is_current;
 else
  key:='dividend:'||p_security||':'||coalesce(payload->>'plan_key',payload->>'announcement_id');
  select dividend_event_id into current_id from public.dividend_events where security_id=p_security and is_current
   and superseded_by_plan_key is null and ((payload->>'plan_key' is not null and plan_key=payload->>'plan_key')
    or announcement_id=payload->>'announcement_id') order by version desc limit 1;
 end if;
 source_key:=(document->>'source')||':'||key;
 -- The caller provides normalized per-fact content, excluding fetch timestamps.
 item_hash:=p_payload->>'observation_hash';if coalesce(item_hash,'')='' then raise exception 'review_observation_hash_required'; end if;
 select data into prior from public.review_events where event_type='observation' and object_key=source_key order by event_id desc limit 1;
 if prior->>'hash'=item_hash then
  if current_id is not null and not exists(select 1 from public.review_jobs where fact_id=current_id) then
   current_item:=public.review_item(p_kind,current_id);
   if current_item->>'verification_status'='candidate' then perform public.review_enqueue(p_kind,current_id,p_actor); end if;
  end if;
  return jsonb_build_object('changed',false,'id',current_id);
 end if;
 if current_id is not null then current_item:=public.review_item(p_kind,current_id); end if;
 observation:=jsonb_build_object('hash',item_hash,'source',document->>'source','fact',payload,'source_document_id',d_id);
 perform public.review_event('observation',source_key,p_security,p_actor,observation);
 if current_id is null then
  -- A legacy candidate may have been moved to the correct period before its
  -- first observed sync. The exact old source document is not a new report.
  if p_kind='financial' then
   select e.data->'original'||jsonb_build_object('result_id',e.data->>'result_id') into old_source
   from public.review_events e where e.event_type='metadata' and e.object_key=key
    and e.data->'original'->>'source_document_id'=d_id::text
    and exists(select 1 from public.financial_facts f where f.financial_fact_id=(e.data->>'result_id')::uuid and f.is_current)
   order by e.event_id desc limit 1;
   if old_source is not null and (old_source->>'value')::numeric is not distinct from (payload->>'value')::numeric
    and old_source->>'unit' is not distinct from payload->>'unit'
    and old_source->>'period_type' is not distinct from payload->>'period_type' then
    return jsonb_build_object('changed',false,'baseline',true,'id',old_source->>'result_id');
   end if;
  end if;
  if p_kind='financial' then
   insert into public.financial_facts(security_id,report_period,metric_code,value,unit,period_type,source_document_id,
    announcement_date,version,verification_status)
   values(p_security,(payload->>'report_period')::date,payload->>'metric_code',(payload->>'value')::numeric,
    payload->>'unit',payload->>'period_type',d_id,(document->>'announcement_date')::date,
    (select coalesce(max(version),0)+1 from public.financial_facts where security_id=p_security
      and report_period=(payload->>'report_period')::date and metric_code=payload->>'metric_code'),'candidate') returning financial_fact_id into current_id;
  else
   insert into public.dividend_events(security_id,report_period,fiscal_year,event_stage,announcement_id,plan_key,
    cash_dividend_per_share,cash_dividend_total,ex_date,announcement_date,bonus_shares_per_share,transfer_shares_per_share,
    source_document_id,verification_status,is_current)
   values(p_security,(payload->>'report_period')::date,(payload->>'fiscal_year')::int,payload->>'event_stage',payload->>'announcement_id',payload->>'plan_key',
    (payload->>'value')::numeric,(payload->>'cash_dividend_total')::numeric,(payload->>'ex_date')::date,
    (payload->>'announcement_date')::date,(payload->>'bonus_shares_per_share')::numeric,(payload->>'transfer_shares_per_share')::numeric,d_id,'candidate',true)
   returning dividend_event_id into current_id;
   perform public.review_touch_year(p_security,(payload->>'fiscal_year')::int,p_actor,'新增分红方案');
  end if;
  perform public.review_event('change',key,p_security,p_actor,jsonb_build_object('kind',p_kind,'report_period',payload->>'report_period',
   'metric_code',payload->>'metric_code','fiscal_year',payload->>'fiscal_year','invalidate',false,'reason','新候选'));
 else
  -- Initial adoption: compare against the original provider observation saved
  -- in fact history. If unavailable, establish a baseline without discarding a
  -- pre-existing verified result whose provenance must remain intact.
  if prior is null and current_item->>'verification_status'='verified' then
   if p_kind='financial' then
    select to_jsonb(f)||jsonb_build_object('source_date',sd.announcement_date) into old_source
    from public.financial_facts f join public.source_documents sd using(source_document_id)
    where f.security_id=p_security and f.report_period=(payload->>'report_period')::date
     and f.metric_code=payload->>'metric_code' and sd.source=document->>'source'
     and coalesce(f.review_data->>'method','') not in ('manual','auto')
    order by f.version desc limit 1;
   else
    select to_jsonb(d)||jsonb_build_object('source_date',sd.announcement_date) into old_source
    from public.dividend_events d join public.source_documents sd using(source_document_id)
    where d.security_id=p_security and d.announcement_id=payload->>'announcement_id' and sd.source=document->>'source'
     and coalesce(d.review_data->>'method','') not in ('manual','auto')
    order by d.version desc limit 1;
   end if;
   if old_source is null or
    ((case when p_kind='financial' then old_source->>'value' else old_source->>'cash_dividend_per_share' end)::numeric
       is not distinct from (payload->>'value')::numeric
      and old_source->>'source_date' is not distinct from document->>'announcement_date') then
    return jsonb_build_object('changed',false,'baseline',true,'id',current_id);
   end if;
  end if;
  if current_item->>'verification_status'='verified' and document->>'announcement_date' is not null
    and current_item->>'announcement_date' is not null
    and (document->>'announcement_date')::date<(current_item->>'announcement_date')::date
    and not coalesce((p_payload->>'restatement')::boolean,false) then
   return jsonb_build_object('changed',false,'older_source',true,'id',current_id);
  end if;
  if prior is null and (current_item->>'value')::numeric is not distinct from (payload->>'value')::numeric
    and current_item->>'source_document_id'=d_id::text then
   if current_item->>'verification_status'='candidate' and not exists(select 1 from public.review_jobs where fact_id=current_id) then perform public.review_enqueue(p_kind,current_id,p_actor); end if;
   return jsonb_build_object('changed',false,'id',current_id);
  end if;
  current_item:=public.review_replace(p_kind,current_id,payload||jsonb_build_object('source_document_id',d_id),false,p_actor,
    jsonb_build_object('status','changed','reason','来源自身出现新有效观察版本，需重新核实'));
  current_id:=(current_item->>'id')::uuid;
 end if;
 perform public.review_enqueue(p_kind,current_id,p_actor);
 return jsonb_build_object('changed',true,'id',current_id);
end $$;


create function public.review_periods() returns jsonb language sql stable set search_path='' as $$
 select coalesce(jsonb_agg(report_period order by report_period desc),'[]') from
 (select distinct report_period from public.quarterly_review_records where report_period is not null) p
$$;
create function public.review_prepare_dispatch(p_job uuid,p_attempt int) returns boolean
language plpgsql set search_path='' as $$
begin
 update public.review_jobs set lease_until=now()+interval '10 minutes',updated_at=now()
 where job_id=p_job and kind='publish' and state='queued' and attempt_seq=p_attempt and lease_until is null;
 if found then perform public.review_event('publication_attempt','dashboard',null,'edge',jsonb_build_object('job_id',p_job,'attempt_seq',p_attempt));return true; end if;
 return false;
end $$;

create function public.review_history(p_kind text,p_id uuid) returns jsonb language plpgsql stable set search_path='' as $$
declare item jsonb;
begin
 item:=public.review_item(p_kind,p_id);
 return (select coalesce(jsonb_agg(to_jsonb(e) order by event_id desc),'[]') from
  (select event_id,event_type,actor,data,created_at from public.review_events
   where security_id=(item->>'security_id')::uuid and
    (object_key=item->>'object_key' or (event_type='observation' and right(object_key,length(item->>'object_key')+1)=':'||(item->>'object_key')))
   order by event_id desc limit 100) e);
end $$;

create function public.review_enqueue_all(p_actor text) returns jsonb language plpgsql set search_path='' as $$
declare r record; total int:=0;
begin
 for r in select kind,id from public.quarterly_review_records where verification_status<>'verified' order by security_id,id loop
  begin perform public.review_enqueue(r.kind,r.id,p_actor);total:=total+1;
  exception when raise_exception then if sqlerrm not in ('review_item_not_current','review_already_verified') then raise; end if;end;
 end loop;
 return jsonb_build_object('queued',total);
end $$;

-- Only trusted service callers execute these transactions. Authenticated users
-- call the login-only Edge boundary, which exposes no worker completion action.
do $$ declare f regprocedure; begin
 for f in select p.oid::regprocedure from pg_proc p join pg_namespace n on p.pronamespace=n.oid
  where n.nspname='public' and p.proname like 'review_%' loop
  execute format('revoke all on function %s from public,anon,authenticated',f);
  execute format('grant execute on function %s to service_role',f);
 end loop;
end $$;
