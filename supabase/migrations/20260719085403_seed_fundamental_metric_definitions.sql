-- The pipeline writes only normalized values and compact provenance metadata.
-- It deliberately does not create Storage objects or retain source responses.
insert into public.metric_definitions (metric_code, name, industry_template, unit, definition, requires_confirmation)
values
  ('revenue', '营业收入', null, 'CNY', '报告期营业总收入；银行为营业总收入。', false),
  ('net_profit', '归母净利润', null, 'CNY', '报告期归属于母公司股东的净利润。', false),
  ('earnings_per_share', '基本每股收益', null, 'CNY/share', '报告期基本每股收益。', false),
  ('operating_cashflow_per_share', '每股经营现金流', null, 'CNY/share', '报告期每股经营活动现金流量净额。', false),
  ('free_cashflow', '自由现金流', null, 'CNY', '数据源给出的企业自由现金流；仅用于趋势比较。', false),
  ('roe', '加权净资产收益率', null, 'percent', '报告期加权平均净资产收益率。', false),
  ('asset_liability_ratio', '资产负债率', null, 'percent', '报告期资产负债率；不作为银行资本结构判断依据。', false),
  ('interest_debt_ratio', '有息负债率', null, 'percent', '报告期有息负债占比。', false),
  ('capital_ratio', '核心一级资本充足率', 'bank', 'percent', '银行核心一级资本充足率。', false),
  ('net_interest_margin', '净息差', 'bank', 'percent', '银行净息差。', false),
  ('non_performing_loan_ratio', '不良贷款率', 'bank', 'percent', '银行不良贷款率。', false),
  ('provision_coverage', '拨备覆盖率', 'bank', 'percent', '银行拨备覆盖率。', false)
on conflict (metric_code) do update set
  name = excluded.name,
  industry_template = excluded.industry_template,
  unit = excluded.unit,
  definition = excluded.definition,
  requires_confirmation = excluded.requires_confirmation;
