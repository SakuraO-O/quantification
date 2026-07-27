-- One authoritative allocation calculation for the publisher and Edge API.
-- It reads the newest complete target and actual configuration together.

create or replace function public.compute_portfolio_allocation()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with categories(category, ordinal) as (
  values
    ('海外'::text, 1), ('红利', 2), ('成长', 3),
    ('债券', 4), ('大宗商品', 5), ('现金', 6)
),
latest as (
  select distinct on (a.allocation_type, a.category)
    a.allocation_type, a.category, a.data_date, a.value, a.version, a.created_at
  from public.portfolio_allocations a
  join categories c on c.category = a.category
  order by a.allocation_type, a.category, a.version desc, a.data_date desc, a.created_at desc
),
assembled as (
  select
    c.category, c.ordinal,
    target.value as target_ratio,
    actual.value as actual_amount,
    target.data_date as target_data_date,
    actual.data_date as actual_data_date,
    target.version as target_version,
    actual.version as actual_version,
    target.created_at as target_created_at,
    actual.created_at as actual_created_at
  from categories c
  left join latest target on target.category = c.category and target.allocation_type = 'target_ratio'
  left join latest actual on actual.category = c.category and actual.allocation_type = 'actual_amount'
),
validity as (
  select
    count(*) filter (where target_ratio is not null) as target_count,
    count(*) filter (where actual_amount is not null) as actual_count,
    coalesce(sum(target_ratio), 0) as target_total,
    coalesce(sum(actual_amount), 0) as actual_total
  from assembled
),
calculated as (
  select
    a.*,
    a.actual_amount / nullif(v.actual_total, 0) * 100 as actual_ratio,
    a.actual_amount / nullif(v.actual_total, 0) * 100 - a.target_ratio as deviation,
    v.actual_total
  from assembled a
  cross join validity v
),
rows as (
  select
    *,
    case
      when abs(deviation) <= 2 then '均衡'
      when abs(deviation) <= 5 then '关注'
      when deviation < 0 then '明显低配'
      else '明显超配'
    end as deviation_state,
    actual_total * target_ratio / 100 - actual_amount as theoretical_adjustment_amount
  from calculated
),
summary as (
  select
    (select category from rows order by deviation desc, ordinal limit 1) as overweight_category,
    (select actual_ratio from rows order by deviation desc, ordinal limit 1) as overweight_ratio,
    (select deviation from rows order by deviation desc, ordinal limit 1) as overweight_deviation,
    (select category from rows order by deviation asc, ordinal limit 1) as underweight_category,
    (select deviation from rows order by deviation asc, ordinal limit 1) as underweight_deviation
)
select case
  when v.target_count <> 6
    or v.actual_count <> 6
    or abs(v.target_total - 100) > 0.0001
    or v.actual_total <= 0 then null
  else jsonb_build_object(
    'rows', (
      select jsonb_agg(jsonb_build_object(
        'category', r.category,
        'target_ratio', round(r.target_ratio, 1),
        'actual_amount', round(r.actual_amount, 2),
        'actual_ratio', round(r.actual_ratio, 1),
        'deviation', round(r.deviation, 1),
        'deviation_state', r.deviation_state,
        'theoretical_adjustment_amount', round(r.theoretical_adjustment_amount, 2),
        'target_data_date', r.target_data_date,
        'actual_data_date', r.actual_data_date
      ) order by r.ordinal)
      from rows r
    ),
    'summary', jsonb_build_object(
      'text', format(
        '%s实际占比%s%%，较目标高%s个百分点；%s低配%s个百分点，是当前最需要补足的类别。',
        s.overweight_category,
        to_char(s.overweight_ratio, 'FM999990.0'),
        to_char(s.overweight_deviation, 'FM999990.0'),
        s.underweight_category,
        to_char(abs(s.underweight_deviation), 'FM999990.0')
      ),
      'total_amount', round(v.actual_total, 2)
    ),
    'updated_at', (
      select max(value) from (
        select max(target_created_at) as value from assembled
        union all
        select max(actual_created_at) as value from assembled
      ) timestamps
    ),
    'data_date', greatest(
      (select max(target_data_date) from assembled),
      (select max(actual_data_date) from assembled)
    ),
    'versions', jsonb_build_object(
      'target_ratio', (select max(target_version) from assembled),
      'actual_amount', (select max(actual_version) from assembled)
    )
  )
end
from validity v
cross join summary s;
$$;

revoke all on function public.compute_portfolio_allocation() from public, anon, authenticated;
grant execute on function public.compute_portfolio_allocation() to service_role;
