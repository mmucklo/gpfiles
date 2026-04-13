/**
 * UX Test: Signal Economics Display
 *
 * Verifies that signal cards show max profit, max loss, R:R, and breakeven.
 * Verifies that the signal drill-down modal shows a full Trade Economics ledger.
 *
 * Expected values are computed from the canonical signal in the spec:
 *   MACRO BULLISH CALL $365 @ entry=$0.28, TP=$0.36, SL=$0.20, qty=10
 * These match the live signals returned by /api/signals/all.
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

// Commission constants (IBKR Pro) — must match signal_economics.ts
const FEE_PER_CONTRACT = 0.65;
const MIN_PER_LEG = 1.00;
const MULT = 100;

function legCommission(qty: number): number {
    return Math.max(FEE_PER_CONTRACT * qty, MIN_PER_LEG);
}

// Canonical signal from spec + live API:
// MACRO BULLISH CALL $365, entry=$0.28, TP=$0.36, SL=$0.20, qty=10
const ENTRY = 0.28;
const TP    = 0.36;
const SL    = 0.20;
const QTY   = 10;
const RT    = legCommission(QTY) * 2;              // $13.00

const EXPECTED_MAX_PROFIT = (TP - ENTRY) * MULT * QTY - RT;       // $67.00
const EXPECTED_MAX_LOSS   = (ENTRY - SL) * MULT * QTY + RT;       // $93.00
const EXPECTED_BREAKEVEN  = ENTRY + (RT / QTY / MULT);             // $0.293
const EXPECTED_RR         = EXPECTED_MAX_PROFIT / EXPECTED_MAX_LOSS; // ~0.72

/** Wait for app to fully load signal data.
 *  If no conviction signals appear within 10s, the test is skipped — the
 *  publisher must be running for these tests to be meaningful.
 */
async function waitForSignals(page: Parameters<typeof test>[1] extends (...args: infer P) => any ? P[0] : never) {
    await page.goto(BASE_URL, { waitUntil: 'load' });
    // Economics row only appears for live BULLISH signals with computable economics.
    const econRow = page.locator('[data-testid="signal-economics-row"]');
    const found = await econRow.waitFor({ state: 'visible', timeout: 10_000 })
        .then(() => true)
        .catch(() => false);
    if (!found) {
        // No live signals in engine — publisher not running. Skip test rather than fail.
        test.skip(true, 'No live conviction signals in engine — start the publisher to run these tests');
    }
}

// ============================================================
//  Card Display tests
// ============================================================
test.describe('Signal Economics — Card Display', () => {
    test.beforeEach(async ({ page }) => {
        await waitForSignals(page);
    });

    test('economics row is visible on every non-stale signal card', async ({ page }) => {
        const econRows = page.locator('[data-testid="signal-economics-row"]');
        const count = await econRows.count();
        expect(count, 'At least one signal card should show economics row').toBeGreaterThan(0);
        await expect(econRows.first()).toBeVisible();
    });

    test('max profit value present and within $1 of expected', async ({ page }) => {
        const maxProfit = page.locator('[data-testid="card-max-profit"]').first();
        await expect(maxProfit).toBeVisible({ timeout: 15_000 });
        const text = await maxProfit.textContent();
        expect(text, 'max profit element should have text').toBeTruthy();
        // Format: "$67 (at 0.36)" — extract leading dollar amount (drop any −)
        const numMatch = text!.replace(/[−\-]/g, '').match(/\$?([\d,]+\.?\d*)/);
        expect(numMatch, `max profit text "${text}" should contain a dollar amount`).toBeTruthy();
        const value = parseFloat(numMatch![1].replace(',', ''));
        expect(value, `max profit ${value} should be close to expected ${EXPECTED_MAX_PROFIT}`)
            .toBeGreaterThan(0);
        expect(Math.abs(value - EXPECTED_MAX_PROFIT)).toBeLessThanOrEqual(1.0);
    });

    test('max loss value present and within $1 of expected', async ({ page }) => {
        const maxLoss = page.locator('[data-testid="card-max-loss"]').first();
        await expect(maxLoss).toBeVisible({ timeout: 15_000 });
        const text = await maxLoss.textContent();
        expect(text, 'max loss element should have text').toBeTruthy();
        const numMatch = text!.replace(/[−\-]/g, '').match(/\$?([\d,]+\.?\d*)/);
        expect(numMatch, `max loss text "${text}" should contain a dollar amount`).toBeTruthy();
        const value = parseFloat(numMatch![1].replace(',', ''));
        expect(value, 'max loss should be positive').toBeGreaterThan(0);
        expect(Math.abs(value - EXPECTED_MAX_LOSS)).toBeLessThanOrEqual(1.0);
    });

    test('R:R value present in "1 : X.XX" format', async ({ page }) => {
        const rr = page.locator('[data-testid="card-rr"]').first();
        await expect(rr).toBeVisible({ timeout: 15_000 });
        const text = await rr.textContent();
        expect(text, 'R:R should have text').toBeTruthy();
        expect(text, `R:R "${text}" should be in "1 : X.XX" format`).toMatch(/1\s*:\s*\d+\.\d+/);
        const rrMatch = text!.match(/1\s*:\s*([\d.]+)/);
        expect(rrMatch, 'R:R format match').toBeTruthy();
        const rrVal = parseFloat(rrMatch![1]);
        expect(Math.abs(rrVal - EXPECTED_RR)).toBeLessThanOrEqual(0.05);
    });

    test('breakeven value present and within $0.01 of expected', async ({ page }) => {
        const be = page.locator('[data-testid="card-breakeven"]').first();
        await expect(be).toBeVisible({ timeout: 15_000 });
        const text = await be.textContent();
        expect(text, 'breakeven should have text').toBeTruthy();
        const numMatch = text!.match(/\$?([\d.]+)/);
        expect(numMatch, `breakeven text "${text}" should be a price`).toBeTruthy();
        const value = parseFloat(numMatch![1]);
        expect(Math.abs(value - EXPECTED_BREAKEVEN)).toBeLessThanOrEqual(0.01);
    });

    test('max profit pill has green color class', async ({ page }) => {
        const maxProfit = page.locator('[data-testid="card-max-profit"]').first();
        await expect(maxProfit).toBeVisible({ timeout: 15_000 });
        await expect(maxProfit).toHaveClass(/green/);
    });

    test('max loss pill has red color class', async ({ page }) => {
        const maxLoss = page.locator('[data-testid="card-max-loss"]').first();
        await expect(maxLoss).toBeVisible({ timeout: 15_000 });
        await expect(maxLoss).toHaveClass(/red/);
    });
});

const MOCK_SIGNAL_BODY = JSON.stringify([{
    timestamp: Math.floor(Date.now() / 1000) - 30,
    action: 'BUY',
    direction: 'BULLISH',
    is_spread: false,
    short_strike: 0,
    long_strike: 0,
    recommended_strike: 365,
    option_type: 'CALL',
    target_limit_price: ENTRY,
    take_profit_price: TP,
    stop_loss_price: SL,
    quantity: QTY,
    kelly_wager_pct: 0.05,
    confidence: 0.82,
    model_id: 'MACRO',
    strategy_code: 'STRAT-002',
    ticker: 'TSLA',
    underlying_price: 349.0,
}]);

// ============================================================
//  Drill-Down Modal tests
// ============================================================
test.describe('Signal Economics — Drill-Down Modal', () => {
    test.beforeEach(async ({ page }) => {
        // Mock signals API so DOM stays stable while we interact with it
        await page.route('**/api/signals/all', (route) =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: MOCK_SIGNAL_BODY,
            })
        );

        await page.goto(BASE_URL, { waitUntil: 'load' });
        await page.waitForSelector('[data-testid="signal-economics-row"]', { timeout: 30_000 });

        // Use evaluate to click the card — avoids Playwright stability checks
        // which can fail when the app is re-rendering signal data
        await page.evaluate(() => {
            const card = document.querySelector('.signal-card') as HTMLElement | null;
            if (card) card.click();
        });

        // Wait for Trade Economics section in the modal
        await page.waitForSelector('.trade-economics-section', { timeout: 20_000 });
        await page.waitForSelector('[data-testid="econ-max-profit"]', { timeout: 10_000 });
    });

    test('Trade Economics section visible with title', async ({ page }) => {
        const econSection = page.locator('.trade-economics-section');
        await expect(econSection).toBeVisible();
        await expect(econSection.locator('.trade-economics-title')).toContainText('TRADE ECONOMICS');
    });

    test('modal max profit within $0.01 of expected', async ({ page }) => {
        const el = page.locator('[data-testid="econ-max-profit"]');
        await expect(el).toBeVisible();
        const text = await el.textContent();
        const numMatch = text!.replace(/[−\-]/g, '').match(/\$([\d,]+\.?\d*)/);
        expect(numMatch, `econ-max-profit text "${text}" should contain a dollar amount`).toBeTruthy();
        const value = parseFloat(numMatch![1].replace(',', ''));
        expect(Math.abs(value - EXPECTED_MAX_PROFIT)).toBeLessThanOrEqual(0.01);
    });

    test('modal max loss within $0.01 of expected', async ({ page }) => {
        const el = page.locator('[data-testid="econ-max-loss"]');
        await expect(el).toBeVisible();
        const text = await el.textContent();
        const numMatch = text!.replace(/[−\-]/g, '').match(/\$([\d,]+\.?\d*)/);
        expect(numMatch, `econ-max-loss text "${text}" should contain a dollar amount`).toBeTruthy();
        const value = parseFloat(numMatch![1].replace(',', ''));
        expect(Math.abs(value - EXPECTED_MAX_LOSS)).toBeLessThanOrEqual(0.01);
    });

    test('modal breakeven within $0.01 of expected', async ({ page }) => {
        const el = page.locator('[data-testid="econ-breakeven"]');
        await expect(el).toBeVisible();
        const text = await el.textContent();
        const numMatch = text!.match(/\$([\d.]+)/);
        expect(numMatch, `econ-breakeven text "${text}" should be a price`).toBeTruthy();
        const value = parseFloat(numMatch![1]);
        expect(Math.abs(value - EXPECTED_BREAKEVEN)).toBeLessThanOrEqual(0.01);
    });

    test('modal R:R within 0.05 of expected', async ({ page }) => {
        const el = page.locator('[data-testid="econ-rr"]');
        await expect(el).toBeVisible();
        const text = await el.textContent();
        expect(text, `R:R "${text}" should be in "1 : X.XX" format`).toMatch(/1\s*:\s*\d+\.\d+/);
        const rrMatch = text!.match(/1\s*:\s*([\d.]+)/);
        expect(rrMatch, 'R:R format match').toBeTruthy();
        const rrVal = parseFloat(rrMatch![1]);
        expect(Math.abs(rrVal - EXPECTED_RR)).toBeLessThanOrEqual(0.05);
    });

    test('modal shows theoretical max info', async ({ page }) => {
        const econSection = page.locator('.trade-economics-section');
        const text = await econSection.textContent();
        // Should show either "unlimited"/"∞" (for CALL) or a dollar amount (for PUT)
        const hasUnlimited = /unlimited|∞/.test(text!);
        const hasTheoreticalMax = /Theoretical max/i.test(text!);
        expect(hasTheoreticalMax || hasUnlimited,
            'Trade Economics section should show theoretical max info').toBeTruthy();
    });
});
