-- Single-writer portfolio persistence and database-managed freshness stamps.
-- No Storage bucket or raw filing content is introduced by this migration.

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke all on function private.set_updated_at() from public, anon, authenticated;

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'securities', 'market_calendars', 'market_daily', 'valuation_daily',
    'asset_daily_signals', 'ingestion_watermarks'
  ] loop
    execute format('drop trigger if exists set_updated_at on public.%I', table_name);
    execute format(
      'create trigger set_updated_at before update on public.%I for each row execute function private.set_updated_at()',
      table_name
    );
  end loop;
end;
$$;

create or replace function public.save_portfolio_allocation(
  p_allocation_type text,
  p_data_date date,
  p_values jsonb,
  p_created_by uuid
)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_version integer;
  v_count integer;
  v_distinct_count integer;
  v_total numeric;
begin
  if p_allocation_type not in ('target_ratio', 'actual_amount') or p_data_date is null
     or jsonb_typeof(p_values) <> 'array' then
    raise exception 'invalid_portfolio_payload' using errcode = '22023';
  end if;

  select count(*), count(distinct value->>'category'), sum((value->>'value')::numeric)
    into v_count, v_distinct_count, v_total
  from jsonb_array_elements(p_values) as items(value)
  where value->>'category' in ('海外', '红利', '成长', '债券', '大宗商品', '现金')
    and jsonb_typeof(value->'value') = 'number'
    and (value->>'value')::numeric >= 0;

  if v_count <> 6 or v_distinct_count <> 6 then
    raise exception 'invalid_portfolio_categories_or_values' using errcode = '22023';
  end if;
  if p_allocation_type = 'target_ratio' and abs(v_total - 100) > 0.0001 then
    raise exception 'target_ratio_must_total_100' using errcode = '22023';
  end if;
  if p_allocation_type = 'actual_amount' and v_total <= 0 then
    raise exception 'actual_amount_must_not_be_all_zero' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('portfolio_allocations:' || p_allocation_type, 0));
  select coalesce(max(version), 0) + 1 into v_version
  from public.portfolio_allocations
  where allocation_type = p_allocation_type;

  insert into public.portfolio_allocations
    (allocation_type, category, data_date, value, version, created_by)
  select p_allocation_type, value->>'category', p_data_date, (value->>'value')::numeric, v_version, p_created_by
  from jsonb_array_elements(p_values) as items(value);

  return v_version;
end;
$$;

revoke all on function public.save_portfolio_allocation(text, date, jsonb, uuid) from public, anon, authenticated;
grant execute on function public.save_portfolio_allocation(text, date, jsonb, uuid) to service_role;
