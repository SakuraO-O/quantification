const { defineConfig } = require('@playwright/test');

const port = 8767;
const baseURL = `http://127.0.0.1:${port}`;

module.exports = defineConfig({
  testDir: './browser-tests',
  timeout: 20_000,
  expect: { timeout: 5_000 },
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    browserName: 'chromium',
    ...(process.env.PLAYWRIGHT_USE_BUNDLED_CHROMIUM === '1' ? {} : { channel: 'chrome' }),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  webServer: {
    command: `python3 -B scripts/serve_dashboard.py --port ${port}`,
    url: `${baseURL}/`,
    reuseExistingServer: false,
    timeout: 15_000
  }
});
