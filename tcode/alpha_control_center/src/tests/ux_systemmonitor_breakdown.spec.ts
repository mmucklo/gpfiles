/**
 * Phase 14.2 UX Tests: SystemMonitor SignalPanel breakdown rendering
 *
 * Regression guard for React error #31:
 *   Object with keys {by_chop_regime, by_model, by_score_bin, ...} rendered as React child.
 *
 * Tests:
 *   1. New Phase-14 shape renders per-model VitalsCard entries with numeric values
 *   2. Old pre-Phase-14 shape {SENTIMENT: N, MACRO: N} renders fallback card — no crash
 *   3. Missing breakdown (500 / empty) renders fallback card — no crash
 *   4. No console errors (React error #31) in any of the above cases
 */
import { test, expect } from '@playwright/test';

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173';

// ── Mock payloads ──────────────────────────────────────────────────────────────

const NEW_BREAKDOWN = {
    attribution: {
        by_model: {
            '30d': {
                SENTIMENT: { count: 42, avg_confidence: 0.78, avg_selection_score: 0.85 },
                MACRO: { count: 17, avg_confidence: 0.65, avg_selection_score: null },
                CONTRARIAN: { count: 5, avg_confidence: 0.55, avg_selection_score: 0.60 },
            },
        },
        by_chop_regime: {},
        by_score_bin: {},
        windows: ['30d'],
        generated_at: '2026-04-14T10:00:00Z',
        note: 'rolling attribution',
    },
};

const OLD_BREAKDOWN = { SENTIMENT: 123, MACRO: 45 };

// ── Helpers ────────────────────────────────────────────────────────────────────

async function setupBaseStubs(page: import('@playwright/test').Page) {
    // Stub everything except /api/metrics/signals/breakdown so tests can set that independently
    await page.route('**/api/metrics/signals', async route => {
        await route.fulfill({ json: [5, 3, 7, 4, 6], status: 200 });
    });
    await page.route('**/api/metrics/vitals', async route => {
        await route.fulfill({ json: { total_signals: 1234, uptime_hours: 48 }, status: 200 });
    });
    await page.route('**/api/signals/all', async route => {
        await route.fulfill({ json: [], status: 200 });
    });
    await page.route('**/api/**', async route => {
        const url = route.request().url();
        if (url.includes('/api/metrics/signals/breakdown')) {
            // let caller set this
            await route.continue();
        } else {
            await route.fulfill({ json: {}, status: 200 });
        }
    });
}

// ── Tests ──────────────────────────────────────────────────────────────────────

test.describe('Phase 14.2: SignalPanel breakdown rendering', () => {

    test('new attribution shape renders per-model VitalsCard entries with numeric values', async ({ page }) => {
        const errors: string[] = [];
        page.on('console', msg => {
            if (msg.type() === 'error') errors.push(msg.text());
        });

        await page.route('**/api/metrics/signals/breakdown', async route => {
            await route.fulfill({ json: NEW_BREAKDOWN, status: 200 });
        });
        await setupBaseStubs(page);
        await page.goto(BASE);

        // Wait for the SignalPanel to load
        await page.waitForSelector('.sparkline-container, .vitals-grid', { timeout: 15000 });

        // Assert that at least one VitalsCard with a known model name is rendered
        // VitalsCards render their label as text inside the grid
        const vitalsGrid = page.locator('.vitals-grid').first();
        await expect(vitalsGrid).toBeVisible();

        const gridText = await vitalsGrid.textContent();
        expect(gridText).toMatch(/SENTIMENT|MACRO|CONTRARIAN/);

        // Assert values are numeric (not "[object Object]")
        const valueEls = vitalsGrid.locator('.vitals-value, [class*="value"]');
        const count = await valueEls.count();
        for (let i = 0; i < count; i++) {
            const text = await valueEls.nth(i).textContent();
            if (text) {
                expect(text).not.toContain('[object');
                expect(text).not.toContain('Object');
            }
        }

        // No React error #31 in console
        const reactErrors = errors.filter(e => e.includes('Minified React error') || e.includes('Error #31') || e.includes('Objects are not valid as a React child'));
        expect(reactErrors).toHaveLength(0);
    });

    test('old flat shape {SENTIMENT: N, MACRO: N} renders fallback — no crash, no [object Object]', async ({ page }) => {
        const errors: string[] = [];
        page.on('console', msg => {
            if (msg.type() === 'error') errors.push(msg.text());
        });

        await page.route('**/api/metrics/signals/breakdown', async route => {
            await route.fulfill({ json: OLD_BREAKDOWN, status: 200 });
        });
        await setupBaseStubs(page);
        await page.goto(BASE);

        await page.waitForSelector('.vitals-grid', { timeout: 15000 });
        const vitalsGrid = page.locator('.vitals-grid').first();
        await expect(vitalsGrid).toBeVisible();

        const gridText = await vitalsGrid.textContent();
        // Must not render raw object representation
        expect(gridText).not.toContain('[object');
        expect(gridText).not.toContain('Object');

        // No React error #31
        const reactErrors = errors.filter(e => e.includes('Minified React error') || e.includes('Objects are not valid as a React child'));
        expect(reactErrors).toHaveLength(0);
    });

    test('missing breakdown (500) renders fallback card — no crash', async ({ page }) => {
        const errors: string[] = [];
        page.on('console', msg => {
            if (msg.type() === 'error') errors.push(msg.text());
        });

        await page.route('**/api/metrics/signals/breakdown', async route => {
            await route.fulfill({ status: 500, body: 'Internal Server Error' });
        });
        await setupBaseStubs(page);
        await page.goto(BASE);

        await page.waitForSelector('.vitals-grid', { timeout: 15000 });
        const vitalsGrid = page.locator('.vitals-grid').first();
        await expect(vitalsGrid).toBeVisible();

        // No React error #31
        const reactErrors = errors.filter(e => e.includes('Minified React error') || e.includes('Objects are not valid as a React child'));
        expect(reactErrors).toHaveLength(0);
    });

    test('no RootErrorBoundary "Dashboard render error" banner in any scenario', async ({ page }) => {
        await page.route('**/api/metrics/signals/breakdown', async route => {
            await route.fulfill({ json: NEW_BREAKDOWN, status: 200 });
        });
        await setupBaseStubs(page);
        await page.goto(BASE);

        await page.waitForTimeout(3000); // let all fetches settle

        // RootErrorBoundary renders a banner with "reload" text on crash
        const errorBanner = page.locator('text=Dashboard render error');
        await expect(errorBanner).not.toBeVisible();
    });
});
