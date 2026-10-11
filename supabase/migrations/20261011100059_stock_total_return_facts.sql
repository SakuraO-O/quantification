-- Keep the signal/MA close unchanged.  Total return must instead start from
-- unadjusted prices so cash distributions are never added to qfq twice.
alter table public.market_daily
  add column if not exists raw_close numeric,
  add column if not exists raw_close_source text;

alter table public.dividend_events
  add column if not exists bonus_shares_per_share numeric,
  add column if not exists transfer_shares_per_share numeric;

comment on column public.market_daily.raw_close is
  'Unadjusted closing price, independently fetched for stock total-return calculations.';
comment on column public.dividend_events.bonus_shares_per_share is
  'Bonus shares received per pre-ex-date share (Sina 送股 per ten / 10).';
comment on column public.dividend_events.transfer_shares_per_share is
  'Capitalization shares received per pre-ex-date share (Sina 转增 per ten / 10).';
