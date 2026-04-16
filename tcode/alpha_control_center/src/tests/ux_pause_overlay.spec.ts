/**
 * Phase 16.1 UX Tests: PauseOverlay — pause gate dashboard integration
 *
 * Tests:
 *   1. On load: overlay visible with "PAUSED" label when backend returns paused
 *   2. Click ACTIVATE (10m) → overlay disappears, countdown visible in header
 *   3. Click Pause button in header → overlay reappears immediately
 *   4. Duration chip selection changes the duration shown on ACTIVATE button
 *   5. Overlay has correct aria attributes (role=dialog, aria-modal, aria-label)
 *   6. No console errors throughout
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173';

// ── Mock payloads ──────────────────────────────────────────────────────────────

const PAUSED_STATUS = {
  paused: true,
  unpause_until: null,
  remaining_sec: 0,
};

const ACTIVE_10M_STATUS = {
  paused: false,
  unpause_until: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
  remaining_sec: 600,
};

// ── Route setup helpers ────────────────────────────────────────────────────────

async function setupPausedRoutes(page: Page) {
  // Pause status → paused
  await page.route('**/api/system/pause-status', route =>
    route.fulfill({ json: PAUSED_STATUS })
  );
  // All other API calls → empty ok
  await page.route('**/api/**', route => route.fulfill({ json: {}, status: 200 }));
}

async function setupActiveRoutes(page: Page) {
  // Pause status → active 10m
  await page.route('**/api/system/pause-status', route =>
    route.fulfill({ json: ACTIVE_10M_STATUS })
  );
  // Unpause POST → active
  await page.route('**/api/system/unpause', route =>
    route.fulfill({ json: ACTIVE_10M_STATUS })
  );
  // Pause POST → paused
  await page.route('**/api/system/pause', route =>
    route.fulfill({ json: PAUSED_STATUS })
  );
  await page.route('**/api/**', route => route.fulfill({ json: {}, status: 200 }));
}

async function setupUnpauseRoute(page: Page, response = ACTIVE_10M_STATUS) {
  await page.route('**/api/system/unpause', route =>
    route.fulfill({ json: response })
  );
  await page.route('**/api/system/pause', route =>
    route.fulfill({ json: PAUSED_STATUS })
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────────

test.describe('Phase 16.1: PauseOverlay', () => {

  test('overlay is visible on load when backend returns paused', async ({ page }) => {
    await setupPausedRoutes(page);
    await page.goto(BASE);

    const overlay = page.locator('[data-testid="pause-overlay"]');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    // Should show "PAUSED" text
    await expect(overlay).toContainText('PAUSED');
  });

  test('overlay has correct accessibility attributes', async ({ page }) => {
    await setupPausedRoutes(page);
    await page.goto(BASE);

    const overlay = page.locator('[data-testid="pause-overlay"]');
    await expect(overlay).toBeVisible({ timeout: 5000 });
    await expect(overlay).toHaveAttribute('role', 'dialog');
    await expect(overlay).toHaveAttribute('aria-modal', 'true');
    await expect(overlay).toHaveAttribute('aria-label', 'Publisher Paused');
  });

  test('ACTIVATE button is present with default 10m duration', async ({ page }) => {
    await setupPausedRoutes(page);
    await page.goto(BASE);

    const btn = page.locator('[data-testid="pause-activate-btn"]');
    await expect(btn).toBeVisible({ timeout: 5000 });
    await expect(btn).toContainText('10m');
  });

  test('duration chips are visible: 10m, 30m, 1h, 2h', async ({ page }) => {
    await setupPausedRoutes(page);
    await page.goto(BASE);

    await expect(page.locator('[data-testid="pause-duration-10m"]')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-testid="pause-duration-30m"]')).toBeVisible();
    await expect(page.locator('[data-testid="pause-duration-1h"]')).toBeVisible();
    await expect(page.locator('[data-testid="pause-duration-2h"]')).toBeVisible();
  });

  test('selecting 30m chip updates ACTIVATE button text', async ({ page }) => {
    await setupPausedRoutes(page);
    await page.goto(BASE);

    const chip30m = page.locator('[data-testid="pause-duration-30m"]');
    await expect(chip30m).toBeVisible({ timeout: 5000 });
    await chip30m.click();

    const btn = page.locator('[data-testid="pause-activate-btn"]');
    await expect(btn).toContainText('30m');
  });

  test('clicking ACTIVATE → overlay disappears, countdown appears in header', async ({ page }) => {
    // Start paused, then simulate unpause response
    await page.route('**/api/system/pause-status', route =>
      route.fulfill({ json: PAUSED_STATUS })
    );
    await setupUnpauseRoute(page);
    await page.route('**/api/**', route => route.fulfill({ json: {}, status: 200 }));

    await page.goto(BASE);

    const overlay = page.locator('[data-testid="pause-overlay"]');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    // Click ACTIVATE
    const activateBtn = page.locator('[data-testid="pause-activate-btn"]');
    await activateBtn.click();

    // Overlay should disappear
    await expect(overlay).not.toBeVisible({ timeout: 3000 });

    // Countdown should appear in header
    const countdown = page.locator('[data-testid="pause-countdown"]');
    await expect(countdown).toBeVisible({ timeout: 2000 });
    await expect(countdown).toContainText('ACTIVE');
  });

  test('clicking Pause button in header → overlay reappears', async ({ page }) => {
    // Start active
    await setupActiveRoutes(page);
    await page.goto(BASE);

    // Overlay should NOT be visible (active state)
    const overlay = page.locator('[data-testid="pause-overlay"]');
    await expect(overlay).not.toBeVisible({ timeout: 3000 });

    // Countdown should be visible
    const countdown = page.locator('[data-testid="pause-countdown"]');
    await expect(countdown).toBeVisible({ timeout: 5000 });

    // Click Pause button in header
    const pauseBtn = page.locator('[data-testid="pause-header-btn"]');
    await expect(pauseBtn).toBeVisible({ timeout: 2000 });
    await pauseBtn.click();

    // Overlay should reappear
    await expect(overlay).toBeVisible({ timeout: 3000 });
    await expect(overlay).toContainText('PAUSED');
  });

  test('no critical console errors throughout activate/pause cycle', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.route('**/api/system/pause-status', route =>
      route.fulfill({ json: PAUSED_STATUS })
    );
    await setupUnpauseRoute(page);
    await page.route('**/api/system/pause', route =>
      route.fulfill({ json: PAUSED_STATUS })
    );
    await page.route('**/api/**', route => route.fulfill({ json: {}, status: 200 }));

    await page.goto(BASE);

    // Activate
    const activateBtn = page.locator('[data-testid="pause-activate-btn"]');
    await expect(activateBtn).toBeVisible({ timeout: 5000 });
    await activateBtn.click();

    // Wait for countdown
    const countdown = page.locator('[data-testid="pause-countdown"]');
    await expect(countdown).toBeVisible({ timeout: 3000 });

    // Pause via header button
    const pauseBtn = page.locator('[data-testid="pause-header-btn"]');
    await pauseBtn.click();

    const overlay = page.locator('[data-testid="pause-overlay"]');
    await expect(overlay).toBeVisible({ timeout: 3000 });

    const criticalErrors = errors.filter(e =>
      e.includes('Minified React error') ||
      e.includes('Objects are not valid as a React child') ||
      e.includes('is not a function') ||
      e.includes('TypeError') ||
      e.includes('Cannot read properties of undefined')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});
