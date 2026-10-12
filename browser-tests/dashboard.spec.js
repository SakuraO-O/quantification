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
    dividend_yield: 0.035, last_year_dividend: 1.2, dividend_safety_status: '稳健',
    fundamental_status: '稳健', report_period: '2026Q2'
  }
];

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockBackend(page, options = {}) {
  const state = { allocation: fixedAllocation(), writes: [], unexpected: [], requests: [], ...options };
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
        completeness: { asset_catalog_valid: true }, permissions: ['view_dashboard', 'view_fundamentals', 'edit_configuration'],
        assets: state.incompleteCatalog ? assets.slice(0, 1) : (state.customAssets || assets),
        style_compass: state.styleCompass || [], allocation: state.allocation
      });
    }
    if (url.pathname.startsWith('/functions/v1/dashboard-api/asset/')) {
      const symbol = decodeURIComponent(url.pathname.split('/').pop());
      const asset = assets.find(item => item.symbol === symbol);
      return json(route, {
        version: 'local-fixed-version-1', asset, range: url.searchParams.get('range'),
        history: ['2026-10-08', '2026-10-09', '2026-10-10'].map((date, index) => ({
          trade_date: date, close: (asset?.close || 30) + index, ma20: 29 + index,
          ma60: 28 + index, ma120: 27 + index, ma200: 26 + index,
          short_trend: '短期强势', mid_trend: '中期上升', long_trend: '长期上升',
          pe: 12, pe_percentile: 40
        })),
        fundamentals_access: true, fundamental_assessment: null,
        financial_facts: [], dividend_events: [], industry_metrics: [], sources: []
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
    if (['/', '/dashboard_auth.js', '/dashboard_runtime.js'].includes(url.pathname)) return route.continue();
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
  const state = await mockBackend(page);
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
  await page.locator('#stock-body tr').click();
  await expect(page.locator('#detail-title')).toContainText('长江电力');
  await expect(page.locator('#detail-content')).toBeVisible();
  await page.locator('#detail-content').evaluate(element => { element.scrollTop = element.scrollHeight; });
  await expect(page.locator('#detail-modal')).toBeVisible();
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
