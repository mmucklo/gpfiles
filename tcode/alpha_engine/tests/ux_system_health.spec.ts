/**
 * ux_system_health.spec.ts — Playwright tests for SystemHealthPanel (Phase 13.6).
 *
 * Verifies:
 *   1. All-ok mock → header badge green, panel shows all green LEDs
 *   2. Publisher stale (5min) → header badge red, publisher row red, drill-down shows details
 *   3. Click drill-down → popover renders with sparkline + restart button
 *   4. Click restart → confirmation modal with 3s countdown
 *   5. All component-name labels have data-glossary-term attributes (TermLabel coverage)
 */
import { test, expect, Page, Route } from '@playwright/test';

const BASE_URL = process.env.ACC_URL ?? 'http://localhost:2112';

const NOW_TS = '2026-04-14T13:31:00Z';

function makeAllOkPayload() {
  return {
    ts: NOW_TS,
    components: {
      publisher:          { status: 'ok', last_ts: '2026-04-14 13:30:58', age_sec: 2,   expected_max_age_sec: 30,   pid: 1771075, uptime_sec: 60500, detail: null },
      intel_refresh:      { status: 'ok', last_ts: '2026-04-14 13:30:50', age_sec: 10,  expected_max_age_sec: 300,  pid: 1771075, uptime_sec: 60500, detail: null },
      options_chain_api:  { status: 'ok', last_ts: '2026-04-14 13:30:45', age_sec: 15,  expected_max_age_sec: 120,  pid: 1771075, uptime_sec: 60500, detail: null },
      premarket:          { status: 'ok', last_ts: null,                  age_sec: null, expected_max_age_sec: 120,  pid: null,    uptime_sec: null,  detail: 'skipped:off-hours' },
      congress_trades:    { status: 'ok', last_ts: '2026-04-14 12:00:00', age_sec: 5460, expected_max_age_sec: 3600, pid: 1771075, uptime_sec: 60500, detail: null },
      correlation_regime: { status: 'ok', last_ts: '2026-04-14 12:00:00', age_sec: 5460, expected_max_age_sec: 3600, pid: 1771075, uptime_sec: 60500, detail: null },
      macro_regime:       { status: 'ok', last_ts: '2026-04-14 13:30:55', age_sec: 5,   expected_max_age_sec: 300,  pid: 1771075, uptime_sec: 60500, detail: null },
      engine_subscriber:  { status: 'ok', last_ts: '2026-04-14 13:30:30', age_sec: 30,  expected_max_age_sec: 90,   pid: 9001,    uptime_sec: 60500, detail: null },
      engine_ibkr_status: { status: 'ok', last_ts: '2026-04-14 13:30:00', age_sec: 60,  expected_max_age_sec: 180,  pid: 9001,    uptime_sec: 60500, detail: null },
    },
  };
}

function makePublisherDeadPayload() {
  const payload = makeAllOkPayload();
  payload.components.publisher = {
    status: 'error',
    last_ts: '2026-04-14 13:25:58',
    age_sec: 302,
    expected_max_age_sec: 30,
    pid: null,
    uptime_sec: null,
    detail: 'no_heartbeat_received',
  };
  return payload;
}

const ALL_OK_SPARKLINE: object[] = [
  { ts: '2026-04-14 13:30:58', status: 'ok', detail: null, pid: 1771075, uptime_sec: 60500 },
  { ts: '2026-04-14 13:30:45', status: 'ok', detail: null, pid: 1771075, uptime_sec: 60480 },
];

async function mockHeartbeatsRoute(page: Page, payload: object) {
  await page.route('**/api/system/heartbeats', (route: Route) => {
    if (route.request().url().includes('/sparkline') || route.request().url().includes('/restart')) {
      route.continue();
      return;
    }
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

async function mockSparklineRoute(page: Page, component: string, rows: object[]) {
  await page.route(`**/api/system/heartbeats/${component}/sparkline`, (route: Route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(rows),
    });
  });
}

async function loadDashboard(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(1500);
}

test.describe('SystemHealthPanel', () => {

  test('all-ok: header badge is green, all rows are green LEDs', async ({ page }) => {
    await mockHeartbeatsRoute(page, makeAllOkPayload());
    await loadDashboard(page);

    // Header badge exists
    const badge = page.locator('[data-testid="system-health-badge"]');
    await expect(badge).toBeVisible({ timeout: 5000 });

    // Badge text includes "ok" (not error/degraded)
    const badgeText = await badge.textContent();
    expect(badgeText).toMatch(/9\s*\/\s*9\s*ok/i);

    // All component rows are present
    for (const comp of ['publisher', 'intel_refresh', 'engine_subscriber']) {
      const row = page.locator(`[data-testid="sph-row-${comp}"]`);
      await expect(row).toBeVisible();
      // Row should not have red/degraded class
      const cls = await row.getAttribute('class') ?? '';
      expect(cls).not.toContain('error');
      expect(cls).not.toContain('degraded');
    }
  });

  test('publisher stale 5min: header badge red, publisher row error', async ({ page }) => {
    await mockHeartbeatsRoute(page, makePublisherDeadPayload());
    await mockSparklineRoute(page, 'publisher', []);
    await loadDashboard(page);

    // Header badge should be red/pulsing
    const badge = page.locator('[data-testid="system-health-badge"]');
    await expect(badge).toBeVisible({ timeout: 5000 });
    const badgeText = await badge.textContent();
    expect(badgeText?.toLowerCase()).toMatch(/error/i);

    // Publisher row should have error class
    const publisherRow = page.locator('[data-testid="sph-row-publisher"]');
    await expect(publisherRow).toBeVisible();
    const cls = await publisherRow.getAttribute('class') ?? '';
    expect(cls).toContain('error');

    // Age shown as something like "5m ago"
    const rowText = await publisherRow.textContent();
    expect(rowText).toMatch(/\d+[smh]/);
  });

  test('click publisher row: drill-down popover opens with details', async ({ page }) => {
    await mockHeartbeatsRoute(page, makePublisherDeadPayload());
    await mockSparklineRoute(page, 'publisher', ALL_OK_SPARKLINE);
    await loadDashboard(page);

    // Click the publisher row
    const publisherRow = page.locator('[data-testid="sph-row-publisher"]');
    await expect(publisherRow).toBeVisible({ timeout: 5000 });
    await publisherRow.click();

    // Drill-down dialog should appear
    const dialog = page.locator('[role="dialog"][aria-label*="publisher"]');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // Should show expected cadence info
    const dialogText = await dialog.textContent();
    expect(dialogText).toMatch(/30s/);  // expected cadence
    expect(dialogText).toMatch(/error/i);

    // Sparkline label should appear
    await expect(dialog.locator('.sph-sparkline-label')).toBeVisible();

    // Restart button should be visible (publisher is restartable)
    const restartBtn = dialog.locator('[data-testid="restart-service-btn"]');
    await expect(restartBtn).toBeVisible();
  });

  test('click restart button: confirmation modal with 3s countdown', async ({ page }) => {
    await mockHeartbeatsRoute(page, makePublisherDeadPayload());
    await mockSparklineRoute(page, 'publisher', []);
    await loadDashboard(page);

    // Open publisher drill-down
    const publisherRow = page.locator('[data-testid="sph-row-publisher"]');
    await publisherRow.click();
    const dialog = page.locator('[role="dialog"][aria-label*="publisher"]');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // Click restart button
    const restartBtn = dialog.locator('[data-testid="restart-service-btn"]');
    await restartBtn.click();

    // Confirmation modal should appear
    const confirmModal = page.locator('[data-testid="restart-confirm-btn"]');
    await expect(confirmModal).toBeVisible({ timeout: 3000 });

    // Initially disabled with countdown
    const isDisabled = await confirmModal.isDisabled();
    expect(isDisabled).toBe(true);

    // Button text should show countdown number
    const btnText = await confirmModal.textContent();
    expect(btnText).toMatch(/Restart \(\d\)/);

    // Wait for countdown to complete (3.5s)
    await page.waitForTimeout(3500);
    const isDisabledAfter = await confirmModal.isDisabled();
    expect(isDisabledAfter).toBe(false);
  });

  test('component labels have data-glossary-term attributes', async ({ page }) => {
    await mockHeartbeatsRoute(page, makeAllOkPayload());
    await loadDashboard(page);

    // Each component row should contain a TermLabel (data-glossary-term attribute)
    const EXPECTED_TERMS = ['PUBLISHER', 'INTEL_REFRESH', 'ENGINE_SUBSCRIBER', 'IBKR_GATEWAY'];
    for (const term of EXPECTED_TERMS) {
      const el = page.locator(`[data-glossary-term="${term}"]`);
      await expect(el).toBeVisible({ timeout: 5000 });
    }
  });

});
