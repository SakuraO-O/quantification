const { test, expect } = require('@playwright/test');

const categories = ['海外', '红利', '成长', '债券', '大宗商品', '现金'];
const fixedAllocation = (first = 20) => ({
  rows: categories.map((category, index) => ({
    category,
    target_ratio: index === 0 ? first : index === 5 ? 5 - (first - 20) : index === 3 ? 25 : index === 4 ? 10 : 20,
    actual_amount: 100000,
    actual_ratio: index === 0 ? 20 : index === 3 ? 25 : index === 4 ? 10 : index === 5 ? 5 : 20,
    deviation: 0,
    deviation_state: '均衡',
    theoretical_adjustment_amount: 0
  })),
  summary: { text: `测试配置：海外目标 ${first}%`, total_amount: 600000 },
  data_date: '2026-10-10',
  updated_at: '2026-10-10T10:00:00Z'
});

const assets = [
  {
    asset_type: '指数', symbol: '000300', name: '沪深300', market: 'CN', trade_date: '2026-10-10',
    close: 4100, daily_return: 0.01, pe: 12, pe_percentile: 40,
    valuation_status: '合理', investment_advice: '可新增', overall_status: '健康上升',
    short_trend: '短期强势', mid_trend: '中期上升', long_trend: '长期上升'
  },
  {
    asset_type: '股票', symbol: '600900', name: '长江电力', market: 'CN', trade_date: '2026-10-10',
    close: 30, daily_return: 0.005, investment_advice: '可关注', overall_status: '健康上升',
    short_trend: '短期强势', mid_trend: '中期上升', long_trend: '长期上升',
    dividend_yield: 4, last_year_dividend: 1.2, dividend_fiscal_year: 2025,
    dividend_yield_status: '已计算', dividend_safety_status: '稳健',
    fundamental_status: '稳健', report_period: '2026Q2'
  }
];

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockBackend(page, options = {}) {
  const state = { allocation: fixedAllocation(), writes: [], unexpected: [], requests: [], detailGates: [], ...options };
  const localOrigin = new URL(test.info().project.use.baseURL).origin;
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== localOrigin) {
      state.unexpected.push(`${request.method()} ${url.origin}${url.pathname}`);
      return route.abort();
    }
    state.requests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === '/dashboard_config.js') {
      return route.fulfill({ contentType: 'text/javascript', body: `window.TREND_DASHBOARD_CONFIG = Object.freeze({
        projectRef: 'local-browser-test',
        supabaseUrl: location.origin,
        apiBase: location.origin + '/functions/v1/dashboard-api',
        portfolioApiBase: location.origin + '/functions/v1/portfolio-config',
        publishableKey: 'sb_publishable_local_browser_test'
      });` });
    }
    if (url.pathname.startsWith('/functions/v1/quarterly-review/') && state.reviewHandler) return state.reviewHandler(route,url,request);
    if (url.pathname.startsWith('/functions/v1/quarterly-review/')) return json(route,{pending_facts:0,pending_years:0,pending_publish:0,auto:0,manual:0,items:[],stocks:[],periods:[]});
    if (url.pathname === '/auth/v1/token') {
      if (state.badLogin) return json(route, { error_description: '测试账号无效' }, 400);
      return json(route, { access_token: 'local-test-token', refresh_token: 'local-test-refresh', expires_in: 3600 });
    }
    if (url.pathname === '/auth/v1/logout') return json(route, {});
    if (url.pathname === '/functions/v1/dashboard-api/overview') {
      if (state.overviewNetworkError) return route.abort('failed');
      if (state.overviewStatus) return json(route, { error: 'dashboard_not_ready' }, state.overviewStatus);
      return json(route, {
        version: 'local-fixed-version-1', generated_at: '2026-10-10T10:00:00Z',
        runtime: state.runtime || null, market_dates: state.marketDates || [],
        completeness: { asset_catalog_valid: true }, permissions: ['view_dashboard', 'view_fundamentals', 'edit_configuration'],
        review: state.reviewSummary,
        assets: state.incompleteCatalog ? assets.slice(0, 1) : (state.customAssets || assets),
        style_compass: state.styleCompass || [], allocation: state.allocation
      });
    }
    if (url.pathname.startsWith('/functions/v1/dashboard-api/asset/')) {
      if (state.holdDetails && url.searchParams.get("section") !== "fundamentals") await new Promise(resolve => state.detailGates.push(resolve));
      const symbol = decodeURIComponent(url.pathname.split('/').pop());
      const asset = (state.customAssets || assets).find(item => item.symbol === symbol);
      return json(route, {
        version: 'local-fixed-version-1', asset, range: url.searchParams.get('range'),
        history: state.customDetailHistory || ['2026-10-08', '2026-10-09', '2026-10-10'].map((date, index) => ({
          trade_date: date, close: (asset?.close || 30) + index + (url.searchParams.get("range") === "5y" ? 100 : 0), ma20: 29 + index,
          ma60: 28 + index, ma120: 27 + index, ma200: 26 + index,
          short_trend: '短期强势', mid_trend: '中期上升', long_trend: '长期上升',
          pe: 12, pe_percentile: 40
        })),
        pe_history: state.customPeHistory || ['2026-10-08', '2026-10-09', '2026-10-10'].map((trade_date, index) => ({trade_date, pe: 11 + index, pe_percentile: 38 + index})),
        fundamentals_access: true, fundamental_assessment: state.detailAssessment || null,
        financial_facts: [], dividend_events: state.detailDividends || [], industry_metrics: [], sources: [],
        quarterly: state.detailQuarterly || null
      });
    }
    if (url.pathname === '/functions/v1/portfolio-config') {
      const payload = JSON.parse(request.postData() || '{}');
      state.writes.push(payload);
      if (state.saveStatus) return json(route, { error: 'allocation_save_failed' }, state.saveStatus);
      if (payload.allocation_type === 'target_ratio') state.allocation = fixedAllocation(payload.values[0].value);
      return json(route, { allocation_type: payload.allocation_type, data_date: payload.data_date,
        version: state.writes.length, allocation: state.allocation });
    }
    if (['/', '/dashboard_review.js', '/dashboard_review.css', '/dashboard_auth.js', '/dashboard_quarterly.js', '/dashboard_runtime.js'].includes(url.pathname)) return route.continue();
    state.unexpected.push(`${request.method()} ${url.pathname}`);
    return route.abort();
  });
  return state;
}

async function login(page) {
  await page.goto('/');
  await expect(page.locator('#auth-gate')).toBeVisible();
  await page.locator('#auth-email').fill('local@example.test');
  await page.locator('#auth-password').fill('test-only-password');
  await page.locator('#auth-submit').click();
}

async function reviewBackend(page) {
  const summary={pending_facts:3,pending_years:1,auto:1,manual:0,pending_publish:0,published_revision:0,revision:0,worker:null};
  const source={source_document_id:'doc',document_url:'https://static.cninfo.com.cn/test.pdf',content_hash:'a'.repeat(64),source:'official-review',title:'2025年度报告',document_type:'annual_report',report_period:'2025-12-31',announcement_date:'2026-03-01'};
  let records=[0,1,2].map(i=>({id:`fact-${i}`,kind:'financial',security_id:'sid',symbol:'sh600900',name:'长江电力',report_period:'2025-12-31',metric_code:'net_profit',metric_name:i?'营业收入':'归母净利润',value:'100.123456',unit:'CNY',period_type:'annual',status:i===2?'failed':'diff',version:1,fact_hash:`hash-${i}`,source:'eastmoney',document_url:source.document_url,source_document_id:'doc',metadata_complete:true,verification_status:'candidate',fields:{},review_data:{ready:i<2,note:'已查阅年报',evidence_id:'doc',checked_at:'2026-11-23T02:14:00Z',reference:{value:'99.123456',source:'Wind',unit:'CNY',difference:'1',difference_ratio:'1.01'},reason:i===2?'本地授权失效':'数值存在差异'}}));
  records.push({...records[0],id:'auto',status:'auto',verification_status:'verified',review_data:{checked_at:'2026-11-23T02:14:00Z',reference:{value:'100.123456',source:'Wind',unit:'CNY'}}});
  const years=[{security_id:'sid',name:'长江电力',symbol:'sh600900',fiscal_year:2025,version:0,ready:true,status:'pending',positive_plans:0,unresolved:0},{security_id:'sid',name:'长江电力',symbol:'sh600900',fiscal_year:2026,version:0,ready:false,status:'pending',positive_plans:0,unresolved:0}];
  const review={writes:[],conflict:false,readError:false,records,summary};
  const state=await mockBackend(page,{reviewSummary:summary,reviewHandler:async(route,url,request)=>{
    const path=url.pathname.replace('/functions/v1/quarterly-review','');
    if(review.readError)return json(route,{error:'review_status_failed'},500);
    if(request.method()==='POST') {
      const body=JSON.parse(request.postData());review.writes.push({path,body});
      if(review.conflict)return json(route,{error:'review_version_changed'},409);
      if(path==='/confirm'){for(const update of body.items){const item=records.find(r=>r.id===update.id);item.verification_status='verified';item.status='manual';if(update.mode==='custom')item.value=update.value;}summary.pending_facts-=body.items.length;summary.manual+=body.items.length;summary.pending_publish++;}
      if(path==='/metadata'){const item=records.find(r=>r.id===body.items[0].id);Object.assign(item,body.items[0].changes);item.review_data={};item.status='pending';item.version++;}
      if(path==='/mark_ready'){for(const update of body.items){const item=records.find(r=>r.id===update.id);item.review_data={...item.review_data,ready:true,note:update.note,evidence_id:update.evidence_id};}}
      if(path==='/annual'){summary.pending_years=0;years[0].status='complete';summary.pending_publish++;}
      if(path==='/enqueue')review.active=true;
      return json(route,{result:[]});
    }
    if(path==='/summary')return json(route,summary);
    if(path==='/filters')return json(route,{stocks:[{security_id:'sid',name:'长江电力',symbol:'sh600900'}],periods:['2025-12-31']});
    if(path==='/records'){const tab=url.searchParams.get('tab');let items=records.filter(r=>tab==='pending'?r.verification_status!=='verified':tab==='auto'?r.status==='auto':r.status==='manual');const status=url.searchParams.get('status');if(status)items=items.filter(r=>r.status===status);return json(route,{items,total:items.length});}
    if(path==='/annual')return json(route,{items:years});
    if(path==='/sources')return json(route,{items:[source]});
    if(path.startsWith('/detail/'))return json(route,{item:records.find(r=>r.id===path.split('/').pop()),sources:[source],jobs:review.active?[{state:'queued'}]:[],events:[{event_type:'verification',created_at:'2026-11-23T02:14:00Z',data:{reason:'校验发现差异',original:{value:'100.123456'}}}]});
    return json(route,{items:[]});
  }});
  return {...state,review};
}

test('review workbench separates report period, shows full years, and preserves normal dashboard layout',async({page})=>{
  const state=await reviewBackend(page);await login(page);
  await expect(page.locator('#review-entry')).toHaveText('待核实 4');
  await page.locator('#review-entry').click();
  await expect(page.locator('#review-list th').filter({hasText:/^股票$/})).toBeVisible();
  await expect(page.locator('#review-list th').filter({hasText:/^报告期$/})).toBeVisible();
  await expect(page.locator('#review-list')).toContainText('2026-11-23 10:14:00');
  await page.screenshot({path:'/private/tmp/trend-review-workbench.png'});
  await page.locator('[data-detail-index="0"]').click();await expect(page.locator('#review-detail')).toContainText('2026-11-23 10:14:00');
  await expect(page.locator('#review-detail')).toContainText('100.123456');
  await page.locator('#review-detail [data-action=close]').first().click();
  await page.locator('[data-review-tab=auto]').click();await expect(page.locator('#review-list')).toContainText('自动通过');
  expect(state.review.writes).toEqual([]);
});

test('manual edit previews old and new values, cancellation preserves input, and success changes category',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);await page.locator('#review-entry').click();await page.locator('[data-detail-index="0"]').click();
  await page.locator('#review-adopt-form [name=mode]').selectOption('custom');
  await expect(page.locator('[data-action=ready]')).toBeDisabled();
  await page.locator('#review-adopt-form [name=value]').fill('110.000001');await page.locator('#review-adopt-form [name=note]').fill('核对原始年报后修正');await page.locator('#review-adopt-form [name=reviewed]').check();
  await page.locator('#review-adopt-form [type=submit]').click();await expect(page.locator('#review-confirm')).toContainText('100.123456 → 采用值 110.000001');
  await page.locator('[data-confirm=cancel]').click();expect(review.writes).toEqual([]);await expect(page.locator('#review-adopt-form [name=value]')).toHaveValue('110.000001');
  await page.locator('#review-adopt-form [type=submit]').click();await page.locator('[data-confirm=submit]').click();
  await expect(page.locator('#review-detail')).not.toBeVisible();await expect(page.locator('#review-entry')).toHaveText('待核实 3');
  expect(review.writes[0].body.items[0].value).toBe('110.000001');
  await page.locator('[data-review-tab=manual]').click();await page.locator('[data-detail-index="0"]').click();await expect(page.locator('#review-detail')).toContainText('110.000001');
});

test('batch selection excludes unchecked records and transaction conflicts keep the confirmation open',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);await page.locator('#review-entry').click();
  await expect(page.locator('[data-review-select="fact-2"]')).toBeDisabled();await page.locator('#review-select-all').check();
  await page.locator('[data-review=batch]').click();await expect(page.locator('#review-confirm')).toContainText('2 项');
  review.conflict=true;await page.locator('[data-confirm=submit]').click();await expect(page.locator('#review-confirm-error')).toContainText('数据版本已变化');
  expect(review.summary.pending_facts).toBe(3);review.conflict=false;await page.locator('[data-confirm=submit]').click();await expect(page.locator('#review-entry')).toHaveText('待核实 2');
});

test('metadata repair submits a new candidate with full source precision',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);await page.locator('#review-entry').click();await page.locator('[data-detail-index="0"]').click();await page.locator('[data-action=metadata]').click();
  await page.locator('#review-metadata-form [name=value]').fill('100.12345678901234');await page.locator('#review-metadata-form [name=note]').fill('修正原始来源转录');await page.locator('#review-metadata-form [name=reviewed]').check();
  await page.locator('#review-metadata-form [type=submit]').click();await expect(page.locator('#review-confirm')).toContainText('保存后仍需重新核实');await page.locator('[data-confirm=submit]').click();
  await expect(page.locator('#review-detail')).not.toBeVisible();expect(review.writes[0].path).toBe('/metadata');expect(review.writes[0].body.items[0].changes.value).toBe('100.12345678901234');
});

test('annual zero conclusion is explicit and waiting years do not allow completion',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);await page.locator('#review-entry').click();await page.locator('[data-review-tab=annual]').click();
  await page.locator('[data-annual-index="1"]').click();await expect(page.locator('#review-annual-form [type=submit]')).toBeDisabled();await page.locator('#review-detail [data-action=close]').click();
  await page.locator('[data-annual-index="0"]').click();await page.locator('#review-annual-form [name=conclusion]').selectOption('zero');await page.locator('#review-annual-form [name=evidence_id]').selectOption('doc');
  await page.locator('#review-annual-form [name=note]').fill('年报及全年公告明确无现金分红');await page.locator('#review-annual-form [name=reviewed]').check();await page.locator('#review-annual-form [name=scope_checked]').check();await page.locator('#review-annual-form [name=zero_confirmed]').check();
  await page.locator('#review-annual-form [type=submit]').click();await expect(page.locator('#review-confirm')).toContainText('全年无现金分红');await page.locator('[data-confirm=submit]').click();await expect(page.locator('#review-detail')).not.toBeVisible();expect(review.writes[0].body.zero_confirmed).toBe(true);
});

test('failed validation can retry while offline, and queued tasks block manual overwrites',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);await page.locator('#review-entry').click();await page.locator('[data-detail-index="2"]').click();await expect(page.locator('#review-detail')).toContainText('校验失败');await page.locator('[data-action=retry]').click();
  await expect(page.locator('[data-action=retry]')).toHaveText('已排队 / 校验中');await expect(page.locator('#review-adopt-form [type=submit]')).toBeDisabled();expect(review.writes[0].path).toBe('/enqueue');
});

test('review read failures never become a false zero or completed state',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);review.readError=true;await page.locator('#review-entry').click();await expect(page.locator('#review-list')).toContainText('读取失败');await expect(page.locator('#review-message')).toContainText('操作未完成');
  await page.locator('[data-review=close-work]').click();await page.locator('#dashboard-logout').click();await expect(page.locator('#review-entry')).not.toBeVisible();expect(review.writes).toEqual([]);
});

test('new review revision invalidates fundamentals cache even when publication version is unchanged',async({page})=>{
  const state=await mockBackend(page,{reviewSummary:{revision:1,pending_facts:0,pending_years:0,pending_publish:0}});
  await login(page);await page.locator('.tab[data-view="stock"]').click();await page.locator('#stock-body tr').click();
  await expect.poll(()=>state.requests.filter(p=>p.includes('dashboard-api/asset/600900')).length).toBe(2);
  const count=state.requests.length;state.reviewSummary.revision=2;state.reviewSummary.pending_facts=1;
  await page.evaluate(()=>window.TrendDashboardRuntime.refresh());
  await expect(page.locator('#detail-modal')).toBeVisible();
  await expect.poll(()=>state.requests.slice(count).filter(p=>p.includes('dashboard-api/asset/600900')).length).toBe(1);
  await expect(page.locator('#review-entry')).toHaveText('待核实 1');
});

test('no pending data removes the reminder and renders an explicit empty state',async({page})=>{
  const {review}=await reviewBackend(page);review.records.splice(0,3);review.summary.pending_facts=0;review.summary.pending_years=0;
  await login(page);await expect(page.locator('#review-entry')).toHaveText('季度核实');await page.locator('#review-entry').click();
  await expect(page.locator('#review-list')).toContainText('当前筛选下没有记录');await expect(page.locator('[data-review=batch]')).toBeDisabled();
});

test('marking evidence checked automatically selects that fact for the next batch',async({page})=>{
  const {review}=await reviewBackend(page);await login(page);await page.locator('#review-entry').click();await page.locator('[data-detail-index="2"]').click();
  await page.locator('#review-adopt-form [name=note]').fill('已核对年报，候选金额正确');await page.locator('#review-adopt-form [name=reviewed]').check();await page.locator('[data-action=ready]').click();
  await expect(page.locator('[data-review-select="fact-2"]')).toBeChecked();await expect(page.locator('[data-review=batch]')).toContainText('（1）');expect(review.writes[0].path).toBe('/mark_ready');
});

test('loads the exact preview bundle, logs in, reads a version, and logs out', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await mockBackend(page);
  await login(page);
  await expect(page.locator('#dashboard-source')).toContainText('local-fi');
  await expect(page.locator('#index-body tr')).toHaveCount(1);
  await expect(page.locator('#stock-body tr')).toHaveCount(1);
  for (const file of ['/', '/dashboard_config.js', '/dashboard_auth.js', '/dashboard_runtime.js']) {
    expect(state.requests).toContain(`GET ${file}`);
  }
  await page.locator('#dashboard-logout').click();
  await expect(page.locator('#auth-gate')).toBeVisible();
  await expect(page.locator('#dashboard-source')).toContainText('已退出');
  expect(errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(state.writes).toEqual([]);
});

test('filters assets and opens index and stock details with chart labels', async ({ page }) => {
  const state = await mockBackend(page, {
    detailAssessment: { report_period: '2026Q2' },
    detailDividends: [{ report_period: '2025', event_stage: '中期', cash_dividend_per_share: 0.12345 }]
  });
  await login(page);
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  await page.locator('#index-search').fill('不存在');
  await expect(page.locator('#index-body tr')).toHaveCount(0);
  await page.locator('#index-search').fill('沪深300');
  await page.locator('#index-body tr').click();
  await expect(page.locator('#detail-modal')).toBeVisible();
  await expect(page.locator('#detail-title')).toContainText('沪深300');
  await expect(page.locator('#price-chart')).toBeVisible();
  await page.locator('#price-chart').hover({ position: { x: 90, y: 80 } });
  await expect(page.locator('#dashboard-chart-tooltip')).toBeVisible();
  await page.locator('[data-range="3"]').first().click();
  await expect(page.locator('[data-range="3"]').first()).toHaveClass(/active/);
  await page.locator('[data-close="detail-modal"]').click();
  await page.locator('.tab[data-view="stock"]').click();
  await page.locator('#stock-search').fill('长江');
  await expect(page.locator('#stock-body tr')).toHaveCount(1);
  await expect(page.locator('#stock-body tr')).toContainText('1.2000');
  await expect(page.locator('#stock-body tr')).toContainText('4.00%');
  expect(await page.evaluate(() => dividendAmount(1.23456))).toBe('1.2346');
  await page.locator('#stock-body tr').click();
  await expect(page.locator('#detail-title')).toContainText('长江电力');
  await expect(page.locator('#detail-modal .metric-card').filter({ hasText: '每股分红' }).locator('b')).toHaveText('1.2000 元/股');
  await expect(page.locator('#detail-content .research-section').filter({ hasText: '分红事件' }).locator('tbody tr').first()).toContainText('0.1235');
  await expect(page.locator('#detail-content')).toBeVisible();
  await page.locator('#detail-content').evaluate(element => { element.scrollTop = element.scrollHeight; });
  await expect(page.locator('#detail-modal')).toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test('closing index and stock details stays closed when their API responses arrive', async ({ page }) => {
  const state = await mockBackend(page, { holdDetails: true });
  await login(page);
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  for (const type of ['index', 'stock']) {
    if (type === 'stock') await page.locator('.tab[data-view="stock"]').click();
    await page.locator(`#${type}-body tr`).first().click();
    await expect(page.locator('#detail-modal')).toBeVisible();
    await expect.poll(() => state.detailGates.length).toBe(1);
    await page.locator('[data-close="detail-modal"]').click();
    await expect(page.locator('#detail-modal')).not.toBeVisible();
    const response = page.waitForResponse(res => res.url().includes('/dashboard-api/asset/'));
    state.detailGates.shift()();
    await response;
    await page.waitForTimeout(50);
    await expect(page.locator('#detail-modal')).not.toBeVisible();
  }
  expect(state.unexpected).toEqual([]);
});

test('shows cloud PE history when the current trading day has no PE', async ({ page }) => {
  const staleAsset = {...assets[0], trade_date: '2026-10-10', pe: null, pe_percentile: null, valuation_status: '估值数据缺失'};
  const state = await mockBackend(page, {
    customAssets: [staleAsset, assets[1]],
    customPeHistory: [
      {trade_date: '2026-09-20', pe: 11, pe_percentile: 35},
      {trade_date: '2026-10-08', pe: 12, pe_percentile: 40}
    ],
    customDetailHistory: [
      {trade_date: '2026-10-08', close: 4090, pe: 12, pe_percentile: 40},
      {trade_date: '2026-10-09', close: 4095, pe: null, pe_percentile: null},
      {trade_date: '2026-10-10', close: 4100, pe: null, pe_percentile: null}
    ]
  });
  await login(page);
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  await page.locator('#index-body tr').click();
  await expect(page.locator('#detail-content')).toContainText('最近 PE 12.00（2026-10-08）');
  await expect(page.locator('#detail-content')).toContainText('PE 尚未更新至行情日');
  await expect(page.locator('#detail-content')).toContainText('估值数据不足');
  await page.locator('[data-range="3"]').first().click();
  await expect(page.locator('#detail-content')).toContainText('最近 PE 12.00（2026-10-08）');
  expect(state.unexpected).toEqual([]);
});

test('shows login rejection and API failure without claiming published data', async ({ page }) => {
  const state = await mockBackend(page, { badLogin: true });
  await login(page);
  await expect(page.locator('#auth-error')).toContainText('测试账号无效');
  await expect(page.locator('#auth-gate')).toBeVisible();
  state.badLogin = false;
  state.overviewStatus = 503;
  await page.locator('#auth-submit').click();
  await expect(page.locator('#dashboard-source')).toContainText('API 不可用，已回退 Mock');
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('shows missing catalogue as an error instead of a valid version', async ({ page }) => {
  const state = await mockBackend(page, { incompleteCatalog: true });
  await login(page);
  await expect(page.locator('#dashboard-source')).toContainText('资产清单不完整');
  await expect(page.locator('#dashboard-source')).toHaveClass(/error/);
  expect(state.writes).toEqual([]);
});

test('shows network failure without switching to a published source', async ({ page }) => {
  const state = await mockBackend(page, { overviewNetworkError: true });
  await login(page);
  await expect(page.locator('#dashboard-source')).toContainText('API 不可用，已回退 Mock');
  await expect(page.locator('#dashboard-source')).toHaveClass(/error/);
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test('saves test configuration, rereads it after refresh, and surfaces save failures', async ({ page }) => {
  const state = await mockBackend(page);
  await login(page);
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  await page.locator('[data-edit="target"]').click();
  await expect(page.locator('#edit-modal')).toBeVisible();
  await page.locator('[data-edit-index="0"]').fill('21');
  await page.locator('[data-edit-index="5"]').fill('4');
  await page.locator('#save-edit').click();
  await expect(page.locator('#edit-modal')).not.toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].allocation_type).toBe('target_ratio');
  expect(state.writes[0].values[0].value).toBe(21);
  await page.reload();
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  await expect(page.locator('#alloc-summary')).toContainText('海外目标 21%');
  state.saveStatus = 500;
  await page.locator('[data-edit="target"]').click();
  await page.locator('#save-edit').click();
  await expect(page.locator('#edit-error')).toContainText('allocation_save_failed');
  await expect(page.locator('#edit-modal')).toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test('keeps missing production allocation and style score visibly unavailable', async ({ page }) => {
  const state = await mockBackend(page, {
    allocation: null,
    styleCompass: [{ left: { name: '红利低波' }, right: { name: '创业板100' },
      score: null, direction: '数据不足', recommendation: '数据不足',
      recommendation_reason: '比较组历史不足' }]
  });
  await login(page);
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  await expect(page.locator('#alloc-summary')).toContainText('配置数据不足');
  await expect(page.locator('#target-stack .stack-seg')).toHaveCount(0);
  await expect(page.locator('#compass-grid .score-value').first()).toHaveText('—');
  await expect(page.locator('#compass-grid .score-dot')).toHaveCount(0);
  await page.locator('[data-edit="target"]').click();
  await expect(page.locator('[data-edit-index="0"]')).toHaveValue('');
  expect(state.unexpected).toEqual([]);
});

test('shows published dividend gaps without inventing a zero yield', async ({ page }) => {
  await mockBackend(page, { customAssets: [assets[0], {
    ...assets[1], dividend_yield: null, last_year_dividend: null,
    dividend_yield_status: '数据不足', dividend_yield_issue: '分红来源尚未完成近期核验'
  }] });
  await login(page);
  await page.locator('.tab[data-view="stock"]').click();
  const row = page.locator('#stock-body tr');
  await expect(row).toContainText('数据不足');
  await expect(row.locator('td[title="分红来源尚未完成近期核验"]')).toHaveText('数据不足');
  await expect(row).not.toContainText('0.00%');
  await row.click();
  await expect(page.locator('#detail-modal .metric-card').filter({ hasText: '股息率' }).locator('b')).toHaveText('数据不足');
  await expect(page.locator('#detail-modal .metric-card').filter({ hasText: '每股分红' }).locator('b')).toHaveText('—');
});

test('renders published quarterly figures, switches metrics, and shows source labels', async ({ page }) => {
  const cell = (value, period, minus = null) => ({
    value, status: '已核验', basis: 'single_quarter',
    source: { report_period: period, source: '巨潮资讯', announcement_date: '2026-10-28' },
    minus_source: minus && { report_period: minus, source: '巨潮资讯', announcement_date: '2026-08-20' }
  });
  const missing = { value: null, status: '未披露' };
  const metric = (label, amount = 100000000) => ({
    label, unit: 'CNY', years: [
      { quarters: [cell(amount, '2025-03-31'), cell(amount, '2025-06-30', '2025-03-31'), missing, missing],
        year_value: amount * 2, year_status: '已核验', year_quarter: 2, comparison: missing },
      { quarters: [cell(amount, '2026-03-31'), cell(-amount, '2026-06-30', '2026-03-31'), missing, missing],
        year_value: 0, year_status: '已核验', year_quarter: 2, comparison: { value: -100, status: '已计算' } }
    ]
  });
  const quarterly = {
    status: '已计算', years: [2025, 2026],
    metrics: {
      net_profit: metric('归母净利润'), deducted_net_profit: metric('扣非归母净利润'),
      revenue: metric('营业总收入'), operating_cashflow: metric('经营现金流净额'),
      roe: { ...metric('加权平均ROE'), unit: 'percent' },
      asset_liability_ratio: { ...metric('资产负债率'), unit: 'percent' }
    },
    dividends: {
      '2025': { per_share: 0.5, unimplemented: true, payout_ratio: 50, status: '已核验' },
      '2026': { per_share: null, payout_ratio: null, status: '待核对' }
    },
    top: {
      report_period: '2026Q2', announcement_date: '2026-08-20',
      dividend_year: 2026, payout_year: 2025,
      dividend: { implemented_per_share: null, approved_per_share: null, status: '待核对' },
      payout: { payout_ratio: 50, status: '已核验' },
      summary: Object.fromEntries(['net_profit', 'deducted_net_profit', 'revenue'].map(key =>
        [key, { value: 0, comparison: { value: -100, status: '已计算' } }]))
    }
  };
  quarterly.metrics.asset_liability_ratio.years[0].quarters[3] = {
    value:null, status:'数据不足', reason:'指标数据缺失'
  };
  const state = await mockBackend(page, { detailQuarterly: quarterly, holdDetails: true });
  await login(page);
  await page.locator('.tab[data-view="stock"]').click();
  await page.locator('#stock-body tr').click();
  await page.waitForFunction(() => window.__trendDashboardRuntime.fundamentalDetails.size > 0);
  await page.locator('.modal-tab[data-pane="quarter"]').click();
  await expect(page.locator('#pane-quarter')).toContainText('2025报告年度');
  await expect(page.locator('#quarter-content')).toContainText('含已批准未实施');
  await expect(page.locator('#quarter-content .quarter-bar.quarter-negative')).toHaveCount(1);
  await expect(page.locator('.quarter-matrix tbody tr').filter({hasText:'全年／已披露累计'}).locator('td').first()).toHaveCSS('font-weight', '700');
  await expect(page.locator('.quarter-matrix tbody tr').filter({hasText:'Q4'}).locator('td').last()).toHaveText('-');
  await expect(page.locator('.quarter-matrix tbody')).not.toContainText('亿元');
  await page.locator('#quarter-content [data-quarter-tip]').first().hover();
  await expect(page.locator('#detail-modal #quarter-tooltip')).toBeVisible();
  await expect(page.locator('#quarter-tooltip')).toContainText('本期依据：2025-03-31 · 巨潮资讯');
  const tooltipOnTop = await page.locator('#quarter-tooltip').evaluate(tip => tip.closest('dialog')?.open);
  expect(tooltipOnTop).toBe(true);
  await page.locator('[data-quarter-metric="asset_liability_ratio"]').click();
  await expect(page.locator('#quarter-content .quarter-line-chart')).toBeVisible();
  await expect(page.locator('.quarter-matrix tbody tr').filter({hasText:'年末资产负债率'}).locator('td').first()).toHaveText('指标数据缺失');
  await page.locator('.quarter-line-chart circle').first().hover();
  await expect(page.locator('#quarter-tooltip')).toBeVisible();
  await expect.poll(() => state.detailGates.length).toBe(1);
  state.detailGates.shift()();
  await page.waitForFunction(() => window.__trendDashboardRuntime.histories.has('600900'));
  await expect(page.locator('#pane-quarter')).toBeVisible();
  await expect(page.locator('[data-quarter-metric="asset_liability_ratio"]')).toHaveClass(/active/);
  await page.locator('[data-close="detail-modal"]').click();
  await page.evaluate(() => window.dispatchEvent(new Event('trend-dashboard-authenticated')));
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/);
  await page.locator('#stock-body tr').click();
  await page.locator('.modal-tab[data-pane="quarter"]').click();
  await expect(page.locator('#pane-quarter')).toContainText('2025报告年度');
  expect(state.unexpected).toEqual([]);
});

test('adapting one assessment does not alter a shared industry template', async ({ page }) => {
  await mockBackend(page);
  await page.goto('/');
  const [adapted, untouched] = await page.evaluate(() => [
    profileFor({ template: 'utility', dividendSafety: '稳健', fund: '稳健',
      assessment: { report_period: '2026Q2', fundamental_status: '个股评估', evidence: ['个股证据'] } }),
    profileFor({ template: 'utility', dividendSafety: '稳健', fund: '稳健', assessment: {} })
  ]);
  expect(adapted.summary).toBe('个股评估');
  expect(untouched.summary).not.toBe('个股评估');
  expect(untouched.evidence).not.toContain('个股证据');
});

test('renders API text as text instead of executable markup', async ({ page }) => {
  const injected = '<img src=x onerror="window.__injected=true">';
  const state = await mockBackend(page, { customAssets: [
    { ...assets[0], name: injected }, assets[1]
  ] });
  await login(page);
  await expect(page.locator('#index-body')).toContainText(injected);
  await expect(page.locator('#index-body img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__injected || false)).toBe(false);
  expect(state.unexpected).toEqual([]);
});

test('shows publication time, independent check state and market cutoff dates', async ({page}) => {
  await mockBackend(page, {
    runtime: {state:'pending', checked_at:'2026-10-10T11:00:00Z', current_version_id:'local-fixed-version-1', calendars:[{market:'CN', is_trading_day:false}]},
    marketDates: [{market:'CN', dates:['2026-10-09'], delayed:false}, {market:'US', dates:['2026-10-08','2026-10-09'], delayed:true}]
  });
  await login(page);
  const status = page.locator('#dashboard-publication-status');
  await expect(status).toContainText('本页发布于');
  await expect(status).toContainText('最近成功检查');
  await expect(status).toContainText('已入库待发布');
  await expect(status).toContainText('A股行情截至 2026-10-09 · 休市');
  await expect(status).toContainText('美股行情截至 2026-10-08～2026-10-09 · 源数据延迟');
  await page.locator('#dashboard-logout').click();
  await expect(status).not.toBeVisible();
});

test('redraws chart after changing its time range without JavaScript errors', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await mockBackend(page);
  await login(page);
  await page.locator('.tab[data-view="stock"]').click();
  await page.locator('#stock-body tr').click();
  await page.waitForFunction(() => window.__trendDashboardRuntime.historyRanges.get('600900') === '1y');
  await page.locator('[data-range="60"]').click();
  await page.waitForFunction(() => window.__trendDashboardRuntime.historyRanges.get('600900') === '5y');
  await page.locator('#price-chart').hover();
  await expect(page.locator('#dashboard-chart-tooltip')).toContainText(/13[0-2]\.00/);
  expect(errors).toEqual([]);
});
