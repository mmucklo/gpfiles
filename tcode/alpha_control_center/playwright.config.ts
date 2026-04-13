import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for UX audit tests.
 * Runs against the live app (default: http://localhost:2112).
 * Set PLAYWRIGHT_BASE_URL env var to override.
 */
export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  retries: 1,
  fullyParallel: true,
  workers: process.env.CI ? 1 : 4,

  reporter: [['list'], ['json', { outputFile: '/tmp/ux_playwright_results.json' }]],

  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:2112',
    viewport: { width: 1280, height: 800 },
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
    screenshot: 'only-on-failure',
    video: 'off',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
