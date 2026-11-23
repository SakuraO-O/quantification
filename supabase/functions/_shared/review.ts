// Presentation invalidation only. Python remains the owner of financial arithmetic.
export function safeJob(job: Record<string, unknown> | null) {
  if (!job) return null;
  const {lease_token: _lease, ...safe} = job;
  return safe;
}

export function safeSummary(summary: Record<string, unknown>) {
  return {...summary, latest_job: safeJob(summary.latest_job as Record<string, unknown> | null)};
}

export function applyReviewInvalidations(assets: Array<Record<string, unknown>>, summary: Record<string, unknown>) {
  const invalidations = (summary.invalidations || []) as Array<Record<string, unknown>>;
  return assets.map(original => {
    const changed = invalidations.filter(x => (x.security_id && x.security_id === original.security_id) || (x.symbol && x.symbol === original.symbol));
    if (!changed.length) return original;
    const keys = new Set(changed.map(x => String(x.object_key)));
    const visit = (node: unknown): unknown => {
      if (Array.isArray(node)) return node.map(visit);
      if (!node || typeof node !== 'object') return node;
      const result: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(node)) result[key] = visit(value);
      if (Array.isArray(result.review_dependencies) && result.review_dependencies.some(k => keys.has(String(k)))) {
        for (const key of (result.review_values || []) as string[]) result[key] = null;
        result.status = '更新待确认';
        result.reason = '原依据已变化，等待核实和更新看板';
      }
      return result;
    };
    const asset: Record<string, unknown> = {...original, quarterly: visit(original.quarterly), review_issue: '原依据已变化，等待更新看板'};
    // Older snapshots have no dependency contract. Do not present them as confirmed.
    if (!(original.quarterly as Record<string, unknown>)?.review_contract) asset.quarterly = null;
    // Derived list conclusions depend on financial/dividend inputs and are not
    // independently recalculated by Edge. Hide until the next Python snapshot.
    for (const key of ['fundamental_assessment','dividend_yield','last_year_dividend','implemented_dividend_per_share',
      'approved_dividend_per_share','total_return_3y','fundamental_status','dividend_safety_status']) (asset as Record<string, unknown>)[key] = null;
    asset.dividend_yield_status = '更新待确认';
    asset.dividend_yield_issue = asset.review_issue;
    asset.total_return_3y_status = '更新待确认';
    const quarterly = asset.quarterly as Record<string, unknown> | null;
    if (quarterly?.dividend_year_complete) {
      const complete = {...quarterly.dividend_year_complete as Record<string, unknown>};
      for (const change of changed) if (['annual','dividend'].includes(String(change.kind))) complete[String(change.fiscal_year)] = false;
      quarterly.dividend_year_complete = complete;
    }
    return asset;
  });
}
