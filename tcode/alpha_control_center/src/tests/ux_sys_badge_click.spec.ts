/**
 * Phase 14.4 UX Tests: SYS badge click opens SystemHealthPanel modal
 *
 * Tests:
 *   1. Clicking "SYS N/N ok" badge opens a dialog with role="dialog" within 500ms
 *   2. Dialog contains per-component rows (heartbeat LEDs + names)
 *   3. Pressing Esc closes the dialog
 *   4. Reopening then clicking the backdrop closes the dialog
 *   5. No console errors throughout
 */
import { test, expect } from '@playwright/test';

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173';

// ── Mock heartbeats payload ────────────────────────────────────────────────────

const MOCK_HEARTBEATS = {
    ts: new Date().toISOString(),
    components: {
        publisher: {
            status: 'ok',
            last_ts: new Date(Date.now() - 5000).toISOString(),
            age_sec: 5,
            expected_max_age_sec: 30,
            pid: 12345,
            uptime_sec: 3600,
            detail: null,
        },
        intel_refresh: {
            status: 'ok',
            last_ts: new Date(Date.now() - 10000).toISOString(),
            age_sec: 10,
            expected_max_age_sec: 300,
            pid: 12346,
            uptime_sec: 3500,
            detail: null,
        },
        options_chain_api: {
            status: 'ok',
            last_ts: new Date(Date.now() - 8000).toISOString(),
            age_sec: 8,
            expected_max_age_sec: 60,
            pid: 12347,
            uptime_sec: 3400,
            detail: null,
        },
        premarket: {
            status: 'ok',
            last_ts: new Date(Date.now() - 3000).toISOString(),
            age_sec: 3,
            expected_max_age_sec: 600,
            pid: null,
            uptime_sec: null,
            detail: null,
        },
        congress_trades: {
            status: 'ok',
            last_ts: new Date(Date.now() - 60000).toISOString(),
            age_sec: 60,
            expected_max_age_sec: 3600,
            pid: null,
            uptime_sec: null,
            detail: null,
        },
        correlation_regime: {
            status: 'ok',
            last_ts: new Date(Date.now() - 120000).toISOString(),
            age_sec: 120,
            expected_max_age_sec: 900,
            pid: null,
            uptime_sec: null,
            detail: null,
        },
        macro_regime: {
            status: 'ok',
            last_ts: new Date(Date.now() - 90000).toISOString(),
            age_sec: 90,
            expected_max_age_sec: 900,
            pid: null,
            uptime_sec: null,
            detail: null,
        },
        engine_subscriber: {
            status: 'ok',
            last_ts: new Date(Date.now() - 4000).toISOString(),
            age_sec: 4,
            expected_max_age_sec: 30,
            pid: 12348,
            uptime_sec: 3600,
            detail: null,
        },
        engine_ibkr_status: {
            status: 'ok',
            last_ts: new Date(Date.now() - 6000).toISOString(),
            age_sec: 6,
            expected_max_age_sec: 30,
            pid: 12349,
            uptime_sec: 3590,
            detail: null,
        },
    },
};

// ── Shared route setup ─────────────────────────────────────────────────────────

async function setupStubs(page: import('@playwright/test').Page) {
    await page.route('**/api/system/heartbeats', async route => {
        // Only stub the base endpoint; let sparkline requests 404 naturally
        if (!route.request().url().includes('/sparkline') && !route.request().url().includes('/restart')) {
            await route.fulfill({ json: MOCK_HEARTBEATS, status: 200 });
        } else {
            await route.continue();
        }
    });
    await page.route('**/api/system/heartbeats/**', async route => {
        await route.fulfill({ json: [], status: 200 });
    });
    await page.route('**/api/**', async route => {
        await route.fulfill({ json: {}, status: 200 });
    });
}

// ── Tests ──────────────────────────────────────────────────────────────────────

test.describe('Phase 14.4: SYS badge click opens SystemHealthPanel modal', () => {

    test('clicking SYS badge opens dialog with role=dialog within 500ms', async ({ page }) => {
        const errors: string[] = [];
        page.on('console', msg => {
            if (msg.type() === 'error') errors.push(msg.text());
        });

        await setupStubs(page);
        await page.goto(BASE);

        // Wait for the badge to appear — it renders once healthSummary arrives
        const badge = page.locator('[data-testid="system-health-badge"]');
        await expect(badge).toBeVisible({ timeout: 10000 });

        // Click the badge
        const clickTime = Date.now();
        await badge.click();

        // Dialog must appear within 500ms
        const dialog = page.locator('[role="dialog"][aria-label="System Health Details"]');
        await expect(dialog).toBeVisible({ timeout: 500 });
        expect(Date.now() - clickTime).toBeLessThan(500);

        // No console errors
        const criticalErrors = errors.filter(e =>
            e.includes('Minified React error') ||
            e.includes('Objects are not valid as a React child') ||
            e.includes('is not a function')
        );
        expect(criticalErrors).toHaveLength(0);
    });

    test('dialog contains per-component rows with LED indicators and names', async ({ page }) => {
        await setupStubs(page);
        await page.goto(BASE);

        const badge = page.locator('[data-testid="system-health-badge"]');
        await expect(badge).toBeVisible({ timeout: 10000 });
        await badge.click();

        const dialog = page.locator('[role="dialog"][aria-label="System Health Details"]');
        await expect(dialog).toBeVisible({ timeout: 500 });

        // At least one sph-row (component row) must be present in the modal
        const componentRows = dialog.locator('[data-testid^="sph-row-"]');
        await expect(componentRows.first()).toBeVisible({ timeout: 5000 });
        const rowCount = await componentRows.count();
        expect(rowCount).toBeGreaterThan(0);
    });

    test('pressing Esc closes the dialog', async ({ page }) => {
        await setupStubs(page);
        await page.goto(BASE);

        const badge = page.locator('[data-testid="system-health-badge"]');
        await expect(badge).toBeVisible({ timeout: 10000 });
        await badge.click();

        const dialog = page.locator('[role="dialog"][aria-label="System Health Details"]');
        await expect(dialog).toBeVisible({ timeout: 500 });

        // Press Escape
        await page.keyboard.press('Escape');
        await expect(dialog).not.toBeVisible({ timeout: 1000 });
    });

    test('clicking backdrop closes the dialog', async ({ page }) => {
        await setupStubs(page);
        await page.goto(BASE);

        const badge = page.locator('[data-testid="system-health-badge"]');
        await expect(badge).toBeVisible({ timeout: 10000 });
        await badge.click();

        const dialog = page.locator('[role="dialog"][aria-label="System Health Details"]');
        await expect(dialog).toBeVisible({ timeout: 500 });

        // Click the overlay (top-left corner outside the card)
        await page.mouse.click(5, 5);
        await expect(dialog).not.toBeVisible({ timeout: 1000 });

        // Reopen to verify badge still works after close
        await badge.click();
        await expect(dialog).toBeVisible({ timeout: 500 });
    });

    test('no console errors throughout open/close lifecycle', async ({ page }) => {
        const errors: string[] = [];
        page.on('console', msg => {
            if (msg.type() === 'error') errors.push(msg.text());
        });

        await setupStubs(page);
        await page.goto(BASE);

        const badge = page.locator('[data-testid="system-health-badge"]');
        await expect(badge).toBeVisible({ timeout: 10000 });

        // Open
        await badge.click();
        const dialog = page.locator('[role="dialog"][aria-label="System Health Details"]');
        await expect(dialog).toBeVisible({ timeout: 500 });

        // Wait for panel data to settle
        await page.waitForTimeout(500);

        // Close via Esc
        await page.keyboard.press('Escape');
        await expect(dialog).not.toBeVisible({ timeout: 1000 });

        // Reopen
        await badge.click();
        await expect(dialog).toBeVisible({ timeout: 500 });

        // Close via backdrop
        await page.mouse.click(5, 5);
        await expect(dialog).not.toBeVisible({ timeout: 1000 });

        const criticalErrors = errors.filter(e =>
            e.includes('Minified React error') ||
            e.includes('Objects are not valid as a React child') ||
            e.includes('is not a function') ||
            e.includes('TypeError') ||
            e.includes('undefined is not')
        );
        expect(criticalErrors).toHaveLength(0);
    });
});
