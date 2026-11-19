/** Read-only production acceptance. Uses and then revokes one temporary owner session. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {chromium, expect} from '@playwright/test';

const sandbox = {window:{}};
vm.runInNewContext(readFileSync('codex/dashboard_config.js','utf8'), sandbox);
const config = sandbox.window.TREND_DASHBOARD_CONFIG;
const pageUrl = 'https://sakurao-o.github.io/quantification/';
const secret = process.env.SUPABASE_SECRET_KEY;
assert(secret, 'Missing production server key');
assert.equal(config.supabaseUrl, process.env.SUPABASE_URL?.replace(/\/$/, ''));
const revision = process.env.GITHUB_SHA || 'acceptance';
for (const file of ['dashboard_auth.js', 'dashboard_quarterly.js', 'dashboard_runtime.js']) {
  const response = await fetch(`${pageUrl}${file}?verify=${revision}`, {signal:AbortSignal.timeout(30000)});
  assert(response.ok, `Deployed asset unavailable: ${file}`);
  const digest = value => createHash('sha256').update(value).digest('hex');
  assert.equal(digest(Buffer.from(await response.arrayBuffer())), digest(readFileSync(`codex/${file}`)), `Deployed asset differs from tested commit: ${file}`);
}
const adminHeaders = {apikey:secret, 'Content-Type':'application/json'};
if (!secret.startsWith('sb_secret_')) adminHeaders.Authorization = `Bearer ${secret}`;
async function authRequest(path, body, headers = adminHeaders) {
  const response = await fetch(config.supabaseUrl + path, {
    method:body ? 'POST':'GET', headers, body:body ? JSON.stringify(body):undefined,
    signal:AbortSignal.timeout(30000)
  });
  assert(response.ok, `Auth acceptance request failed (${response.status}) at ${path.split('?')[0]}`);
  return response.status === 204 ? null : response.json();
}

const {users} = await authRequest('/auth/v1/admin/users?page=1&per_page=100');
const owners = users.filter(user => user.app_metadata?.role === 'editor');
assert.equal(owners.length, 1, 'Production acceptance requires one configured owner');
const link = await authRequest('/auth/v1/admin/generate_link', {type:'magiclink',email:owners[0].email});
const session = await authRequest('/auth/v1/verify', {type:'magiclink',token_hash:link.hashed_token}, {
  apikey:config.publishableKey,'Content-Type':'application/json'
});
assert(session?.access_token, 'Temporary session was not issued');
// Mask credentials before browser use. No auth response or trace is uploaded.
for (const value of [session.access_token, session.refresh_token, link.hashed_token]) {
  if (value && process.env.GITHUB_ACTIONS) console.log(`::add-mask::${value}`);
}
let browser;
try {
  browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  await context.addInitScript(session => {
    sessionStorage.setItem('trend-dashboard-access-token',session.access_token);
    sessionStorage.setItem('trend-dashboard-refresh-token',session.refresh_token);
    sessionStorage.setItem('trend-dashboard-token-expires-at', String(Date.now()+session.expires_in*1000));
  }, session);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const allowed = new Set([new URL(pageUrl).origin, new URL(config.supabaseUrl).origin]);
  let releaseHistory;
  const historyGate = new Promise(resolve => {releaseHistory=resolve;});
  let holdHistory = true;
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    assert(allowed.has(url.origin), 'Unexpected browser destination');
    // Acceptance never modifies portfolio or financial facts.
    if (!['GET','OPTIONS'].includes(request.method())) return route.abort();
    if (holdHistory && url.pathname.includes('/asset/') && url.searchParams.get('section') === 'history') await historyGate;
    if (url.origin === new URL(pageUrl).origin && url.pathname.endsWith('.js')) {
      url.searchParams.set('verify',revision);
      return route.continue({url:url.toString()});
    }
    return route.continue();
  });
  const overviewResponse = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/dashboard-api/overview'));
  await page.goto(`${pageUrl}?verify=${revision}`, {waitUntil:'domcontentloaded'});
  const overview = await (await overviewResponse).json();
  assert.equal(overview.completeness.asset_catalog_valid, true);
  const counts = {
    index:overview.assets.filter(asset => asset.asset_type === '指数').length,
    stock:overview.assets.filter(asset => asset.asset_type === '股票').length
  };
  await expect(page.locator('#dashboard-source')).toHaveClass(/api/, {timeout:90000});
  await expect(page.locator('#index-body tr')).toHaveCount(counts.index);
  await page.locator('.tab[data-view="stock"]').click();
  await expect(page.locator('#stock-body tr')).toHaveCount(counts.stock);
  await expect(page.locator('#dashboard-publication-status')).toContainText('最近成功检查');
  await page.locator('#stock-body tr').filter({hasText:'中国海油'}).click();
  await page.locator('.modal-tab[data-pane="quarter"]').click();
  await expect(page.locator('#quarter-content .quarter-bar').first()).toBeVisible({timeout:90000});
  await page.locator('#quarter-content [data-quarter-tip]').first().hover();
  await expect(page.locator('#detail-modal #quarter-tooltip')).toBeVisible();
  await expect(page.locator('.quarter-annual-total td').first()).toHaveCSS('font-weight','700');
  await expect(page.locator('.quarter-matrix tbody')).not.toContainText('亿元');
  await expect(page.locator('.quarter-matrix thead th').first()).toContainText('亿元');
  const quarterly = await page.evaluate(() => window.__trendDashboardRuntime.fundamentalDetails.values().next().value.quarterly);
  await expect(page.locator('.quarter-matrix thead th')).toHaveCount(quarterly.years.length + 1);
  if (quarterly.metrics.net_profit.years.at(-1).quarters[3].status === '未披露') {
    await expect(page.locator('.quarter-matrix tbody tr').filter({hasText:'Q4'}).locator('td').last()).toHaveText('-');
  }
  for (const key of ['roe','asset_liability_ratio']) {
    await page.locator(`[data-quarter-metric="${key}"]`).click();
    const cells = page.locator('.quarter-annual-total td');
    for (const [index,year] of quarterly.metrics[key].years.entries()) {
      const cell = year.quarters[3];
      if (cell.value == null) {
        const expected = cell.status === '未披露' ? '-' : cell.reason || cell.status || '数据不足';
        await expect(cells.nth(index)).toHaveText(expected);
      }
    }
  }
  holdHistory = false;
  releaseHistory();
  await page.waitForFunction(() => window.__trendDashboardRuntime.historyRanges.get('sh600938') === '1y', null, {timeout:90000});
  await expect(page.locator('#pane-quarter')).toBeVisible();
  await page.locator('.modal-tab[data-pane="price"]').click();
  await page.locator('[data-range="60"]').click();
  await page.waitForFunction(() => window.__trendDashboardRuntime.historyRanges.get('sh600938') === '5y', null, {timeout:90000});
  await page.locator('#price-chart').hover();
  await expect(page.locator('#dashboard-chart-tooltip')).toBeVisible();
  assert.deepEqual(errors, []);
  const result = await page.evaluate(() => ({
    version:window.__trendDashboardRuntime.version,
    five_year_rows:window.__trendDashboardRuntime.histories.get('sh600938').history.length,
    status:document.querySelector('#dashboard-publication-status').textContent
  }));
  console.log(JSON.stringify({production_acceptance:'passed',...result}));
} finally {
  if (browser) await browser.close();
  const response = await fetch(config.supabaseUrl+'/auth/v1/logout?scope=local', {
    method:'POST',headers:{apikey:config.publishableKey,Authorization:`Bearer ${session.access_token}`},
    signal:AbortSignal.timeout(30000)
  });
  assert(response.ok, 'Temporary production session cleanup failed');
  console.log('Temporary production session revoked');
}
