-- Align legacy derived labels with the dashboard's canonical display enums.
--
-- This migration is intentionally safe to rerun.  `高估` was used by the
-- legacy schema for both the old >=90% bucket and, after an earlier partial
-- migration, the current [70%, 90%) bucket.  PE percentile is the only
-- reliable discriminator, so normalize from it instead of chaining labels.
-- Rows without a percentile keep an existing `高估` label: changing it to
-- `极高估` without a source value would manufacture a valuation conclusion.
--
-- Early V2 projects were created before ``signal_tags`` became part of the
-- normalized signal payload.  The ingestion path writes this field, so add it
-- idempotently before normalizing legacy tags instead of skipping the update.
alter table public.asset_daily_signals
  add column if not exists signal_tags text;

update public.asset_daily_signals
set
  valuation_status = case
    when pe_percentile >= 90 then '极高估'
    when pe_percentile >= 70 then '高估'
    when valuation_status = '偏高' then '高估'
    else valuation_status
  end,
  short_trend = case
    when short_trend = '短期转弱' then '短期下跌'
    else short_trend
  end,
  signal_tags = case
    when signal_tags is null then null
    else regexp_replace(
      regexp_replace(
        replace(signal_tags, '短期转弱', '短期下跌'),
        '(^|, )偏高(?=, |$)', '\1高估', 'g'
      ),
      '(^|, )高估(?=, |$)',
      case when pe_percentile >= 90 then '\1极高估' else '\1高估' end,
      'g'
    )
  end
where valuation_status = '偏高'
   or (valuation_status in ('高估', '极高估') and pe_percentile >= 70)
   or short_trend = '短期转弱'
   or signal_tags ~ '(^|, )(偏高|短期转弱)(, |$)'
   or (pe_percentile >= 90 and signal_tags ~ '(^|, )高估(, |$)');

-- Before the rule was corrected, a PE percentile of 90% or above could
-- override a repaired/weakened long trend. Preserve the documented priority.
update public.asset_daily_signals
set investment_advice = '观察等待'
where long_trend in ('长期修复', '长期转弱')
  and investment_advice = '仅持有';
