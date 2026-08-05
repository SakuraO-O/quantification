-- Repair historical rows emitted by the CSIndex endpoint on weekends.  The
-- endpoint repeated Friday closes under Saturday/Sunday dates; they are not
-- tradable observations and polluted derived signals.
with invalid_csindex_rows as (
  select security_id, trade_date
  from public.market_daily
  where source = 'csindex'
    and extract(isodow from trade_date) in (6, 7)
)
delete from public.asset_daily_signals signal
using invalid_csindex_rows invalid
where signal.security_id = invalid.security_id
  and signal.trade_date = invalid.trade_date;

delete from public.market_daily
where source = 'csindex'
  and extract(isodow from trade_date) in (6, 7);

-- Before this migration, valuation facts could be carried forward for 45 days.
-- Clear those synthetic PE values so the latest date is empty whenever there
-- is no source observation on that exact date.  A forced market sync after
-- deployment recomputes the remaining trend and advice fields from facts.
update public.asset_daily_signals signal
set
  pe = null,
  pe_percentile = null,
  pe_percentile_period = null,
  valuation_status = '估值数据缺失'
from public.securities security
where signal.security_id = security.security_id
  and security.asset_type = '指数'
  and signal.pe is not null
  and not exists (
    select 1
    from public.valuation_daily valuation
    where valuation.security_id = signal.security_id
      and valuation.trade_date = signal.trade_date
      and valuation.valuation_type = 'pe'
  );
