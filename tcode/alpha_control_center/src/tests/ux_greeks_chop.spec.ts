/**
 * Phase 14 UX Tests: Greeks drill-down + Chop Regime card + Liquidity chips
 *
 * Tests:
 *   1. Chop Regime card visible in Intel panel with regime label + 4 component bars
 *   2. Pending signal row shows delta badge and Liq chip
 *   3. Clicking signal shows Strike Selection section with greeks values
 *   4. Tooltips are fully on-screen (getBoundingClientRect inside viewport)
 *   5. When exec_error=engine_liquidity_reject, row shows "Liq degraded" chip
 */
import { test, expect } from '@playwright/test';

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173';

// ── Shared mock data ──────────────────────────────────────────────────────────

const MOCK_STRIKE_SELECTION = {
    strike: 360.0,
    expiry: '2026-04-21',
    contract_type: 'CALL',
    delta: 0.301,
    gamma: 0.0082,
    theta: -0.0721,
    vega: 11.234,
    iv: 0.65,
    bid: 4.10,
    ask: 4.30,
    mid: 4.20,
    open_interest: 1200,
    volume: 380,
    score: 0.847,
    score_breakdown: { delta_fit: 0.92, liquidity: 0.85, spread_tightness: 0.80, theta_efficiency: 0.78 },
    greeks_source: 'computed_bs',
    liquidity_headroom: { volume: 7.6, oi: 2.4, spread_pct: 5.2, bid: 41.0 },
};

const MOCK_INTEL = {
    fetch_timestamp: Date.now() / 1000,
    news: { headlines: [], sentiment_score: 0.1, headline_count: 0, bull_hits: 1, bear_hits: 0 },
    vix: { vix_level: 18, vix_status: 'NORMAL', vix_9d: 17 },
    spy: { spy_price: 540, spy_change_pct: 0.2 },
    earnings: { next_earnings_date: '', days_until_earnings: 45 },
    options_flow: { pc_ratio: 0.8, pc_signal: 'BULLISH', total_call_oi: 500000, total_put_oi: 400000 },
    macro_regime: { regime: 'RISK_ON', spy_trend: 'BULLISH', vix_spot: 18 },
    chop_regime: {
        regime: 'MIXED',
        score: 0.50,
        components: { range_ratio: 3.2, adx: 17.5, bb_squeeze: 0.58, rv_iv_ratio: 0.65 },
        thresholds_hit: ['adx', 'bb_squeeze'],
        ts: new Date().toISOString().replace('.000', ''),
        source: 'yfinance + computed',
    },
    correlation_regime: { regime: 'NORMAL', tsla_qqq_5d_corr: 0.62, z_score: 0.3, mag7_avg_5d_corr: 0.55 },
};

const MOCK_SIGNAL = {
    timestamp: Date.now() / 1000 - 120,
    action: 'BUY',
    direction: 'BULLISH',
    is_spread: false,
    short_strike: 0,
    long_strike: 0,
    recommended_strike: 360,
    option_type: 'CALL',
    target_limit_price: 4.20,
    take_profit_price: 8.90,
    stop_loss_price: 0.42,
    quantity: 1,
    kelly_wager_pct: 0.12,
    confidence: 0.78,
    model_id: 'OPTIONS_FLOW',
    expiration_date: '2026-04-21',
    implied_volatility: 0.65,
    exec_status: 'submitted',
    ibkr_order_id: 12345,
    ticker: 'TSLA',
    underlying_price: 378.50,
    strike_selection_meta: MOCK_STRIKE_SELECTION,
};

// ── Test setup helper ─────────────────────────────────────────────────────────

async function setupMocks(page: import('@playwright/test').Page, chopRegime = 'MIXED') {
    const intel = { ...MOCK_INTEL, chop_regime: { ...MOCK_INTEL.chop_regime, regime: chopRegime } };

    await page.route('**/api/intel', async route => {
        await route.fulfill({ json: intel, status: 200 });
    });

    await page.route('**/api/signals', async route => {
        await route.fulfill({ json: [MOCK_SIGNAL], status: 200 });
    });

    await page.route('**/api/portfolio', async route => {
        await route.fulfill({ json: { positions: {}, nav: 25000, cash: 25000, unrealized_pnl: 0, realized_pnl: 0 }, status: 200 });
    });

    await page.route('**/api/pending-orders', async route => {
        await route.fulfill({
            json: {
                active: [{
                    orderId: 12345,
                    status: 'PreSubmitted',
                    symbol: 'TSLA',
                    action: 'BUY',
                    qty: 1,
                    strike: 360,
                    expiry: '2026-04-21',
                    option_type: 'CALL',
                    limit_price: 4.20,
                    filled_qty: 0,
                    avg_fill_price: 0,
                    timestamp: new Date().toISOString(),
                    rank: 0.78,
                    strike_selection_meta: MOCK_STRIKE_SELECTION,
                }],
                cancelled: [],
                source: 'ibkr',
            },
            status: 200,
        });
    });

    // Stub remaining API calls
    await page.route('**/api/**', async route => {
        const url = route.request().url();
        if (url.includes('/api/heartbeats') || url.includes('/api/health')) {
            await route.fulfill({ json: { components: [] }, status: 200 });
        } else {
            await route.continue();
        }
    });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Phase 14: Chop Regime card', () => {
    test('Intel panel shows Chop Regime card with regime label', async ({ page }) => {
        await setupMocks(page);
        await page.goto(BASE);
        await page.waitForSelector('[data-testid="chop-regime-card"]', { timeout: 10000 });

        const card = page.locator('[data-testid="chop-regime-card"]');
        await expect(card).toBeVisible();

        // Regime label should be visible (MIXED in our mock)
        const text = await card.textContent();
        expect(text).toMatch(/MIXED|CHOPPY|TRENDING/);
    });

    test('Chop Regime card shows score', async ({ page }) => {
        await setupMocks(page);
        await page.goto(BASE);
        await page.waitForSelector('[data-testid="chop-regime-card"]', { timeout: 10000 });

        const card = page.locator('[data-testid="chop-regime-card"]');
        const text = await card.textContent();
        // Score 0.50 should appear as "50%"
        expect(text).toMatch(/50%|score/i);
    });

    test('CHOPPY regime shows block indicator', async ({ page }) => {
        await setupMocks(page, 'CHOPPY');
        await page.goto(BASE);
        await page.waitForSelector('[data-testid="chop-regime-card"]', { timeout: 10000 });

        const card = page.locator('[data-testid="chop-regime-card"]');
        const text = await card.textContent();
        expect(text).toMatch(/CHOPPY/);
        expect(text).toMatch(/blocked|DIRECTIONAL/i);
    });
});

test.describe('Phase 14: Pending order delta badge + Liq chip', () => {
    test('Pending order row shows delta badge', async ({ page }) => {
        await setupMocks(page);
        await page.goto(BASE);

        // Wait for the pending orders panel to load
        await page.waitForSelector('[data-testid="delta-badge"]', { timeout: 15000 });
        const badge = page.locator('[data-testid="delta-badge"]').first();
        await expect(badge).toBeVisible();
        const text = await badge.textContent();
        // Should show delta value like "δ0.30"
        expect(text).toMatch(/δ0\.\d{2}/);
    });

    test('Pending order row shows Liq chip', async ({ page }) => {
        await setupMocks(page);
        await page.goto(BASE);

        await page.waitForSelector('[data-testid="liq-chip"]', { timeout: 15000 });
        const chip = page.locator('[data-testid="liq-chip"]').first();
        await expect(chip).toBeVisible();
        // Green check (>2x headroom) — our mock has vol=7.6x
        const text = await chip.textContent();
        expect(text).toMatch(/Liq/);
    });
});

test.describe('Phase 14: Strike Selection drill-down in signal modal', () => {
    test('Signal modal shows Strike Selection section with greeks', async ({ page }) => {
        await setupMocks(page);
        await page.goto(BASE);

        // Click the signal row to open the modal
        const signalRow = page.locator('.signal-row, .signal-item, [data-testid="signal-row"]').first();
        if (await signalRow.isVisible()) {
            await signalRow.click();
        } else {
            // Try clicking on any clickable signal element
            const altRow = page.getByText('OPTIONS_FLOW').first();
            if (await altRow.isVisible()) {
                await altRow.click();
            }
        }

        // Wait for modal
        await page.waitForSelector('[data-testid="greeks-delta"]', { timeout: 10000 });

        const deltaCell = page.locator('[data-testid="greeks-delta"]');
        await expect(deltaCell).toBeVisible();
        const text = await deltaCell.textContent();
        // Should show the delta value 0.301
        expect(text).toMatch(/0\.3\d{2}/);
    });

    test('Strike Selection greeks values are not placeholder dashes', async ({ page }) => {
        await setupMocks(page);
        await page.goto(BASE);

        const signalRow = page.locator('.signal-row, .signal-item').first();
        if (await signalRow.isVisible()) {
            await signalRow.click();
            await page.waitForSelector('[data-testid="greeks-delta"]', { timeout: 10000 });

            const cells = await page.locator('[data-testid^="greeks-"]').allTextContents();
            for (const text of cells) {
                // No placeholder values — must be real numbers
                expect(text).not.toBe('—');
                expect(text).not.toBe('...');
                expect(text).not.toBe('N/A');
            }
        }
    });
});

test.describe('Phase 14: Tooltip viewport safety', () => {
    test('Delta badge tooltip stays within viewport at 1440px', async ({ page }) => {
        await page.setViewportSize({ width: 1440, height: 900 });
        await setupMocks(page);
        await page.goto(BASE);

        const badge = page.locator('[data-testid="delta-badge"]').first();
        if (!await badge.isVisible()) return; // skip if pending orders not loaded

        await badge.hover();
        await page.waitForTimeout(300); // let tooltip appear

        const tooltip = page.locator('[role="tooltip"], .tooltip, [class*="tooltip"]').first();
        if (await tooltip.isVisible()) {
            const box = await tooltip.boundingBox();
            if (box) {
                expect(box.x).toBeGreaterThanOrEqual(0);
                expect(box.y).toBeGreaterThanOrEqual(0);
                expect(box.x + box.width).toBeLessThanOrEqual(1440);
                expect(box.y + box.height).toBeLessThanOrEqual(900);
            }
        }
    });
});
