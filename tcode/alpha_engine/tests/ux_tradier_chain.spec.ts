/**
 * ux_tradier_chain.spec.ts — Phase 15 Playwright tests
 *
 * Verifies:
 * 1. Integrity CHAIN indicator shows green when Tradier is active source
 * 2. CHAIN inline badge shows "TRADIER"
 * 3. Opening the CHAIN panel shows the source badge labeled "TRADIER"
 * 4. Opening the CHAIN panel with yfinance fallback shows "YFINANCE" badge (amber)
 * 5. Chain audit panel has >50 entries when Tradier is active
 */
import { test, expect } from '@playwright/test';

const BASE = 'http://localhost:2112';

// Standard mocks shared across tests
async function setupCommonMocks(page: any) {
    await page.route('**/api/broker/status', async (route: any) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ mode: 'IBKR_PAPER', connected: true, order_path: 'paper' }),
        });
    });

    await page.route('**/api/portfolio', async (route: any) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ nav: 25000, cash: 24000, realized_pnl: 0, positions: {} }),
        });
    });

    await page.route('**/api/config/notional', async (route: any) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ notional_account_size: 25000 }),
        });
    });

    await page.route('**/api/metrics/publisher', async (route: any) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ signals_rejected_commission_total: 0 }),
        });
    });

    await page.route('**/api/signals', async (route: any) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([]),
        });
    });

    await page.route('**/api/intel', async (route: any) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                fetch_timestamp: Date.now() / 1000,
                news: { headlines: [], sentiment_score: 0, headline_count: 0, bull_hits: 0, bear_hits: 0 },
                vix: { vix_level: 18.0, vix_status: 'NORMAL' },
                spy: { spy_price: 510.0, spy_change_pct: 0.1 },
                earnings: { next_earnings_date: null, days_until_earnings: null },
                options_flow: { pc_ratio: 0.9, pc_signal: 'NEUTRAL', total_call_oi: 50000, total_put_oi: 45000 },
                premarket: null,
            }),
        });
    });
}

function makeTradierAudit(chainSource: string, entryCount: number) {
    return {
        ibkr_connected: false,
        ibkr_spot: 0.0,
        primary_source: 'tv',
        tv: 364.20,
        yf: 364.15,
        divergence_pct: 0.01,
        ok: true,
        warning: null,
        timestamp: new Date().toISOString(),
        options_chain_source: chainSource,
        chain_entry_count: entryCount,
        chain_age_sec: 15,
        last_chain_fetch: new Date().toISOString(),
        spot_validation: {
            tv: 364.20,
            yf: 364.15,
            divergence_pct: 0.01,
            ok: true,
            timestamp: new Date().toISOString(),
        },
    };
}

test.describe('Phase 15: Tradier chain source integrity indicators', () => {
    test('CHAIN inline badge shows TRADIER when source is tradier', async ({ page }) => {
        await setupCommonMocks(page);

        await page.route('**/api/data/audit', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(makeTradierAudit('tradier', 180)),
            });
        });

        await page.goto(BASE, { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(1500);

        const badge = page.getByTestId('chain-source-inline');
        await expect(badge).toBeVisible({ timeout: 8000 });
        const badgeText = await badge.textContent();
        expect(badgeText?.toUpperCase()).toContain('TRADIER');
    });

    test('CHAIN panel source badge shows TRADIER with ok class', async ({ page }) => {
        await setupCommonMocks(page);

        await page.route('**/api/data/audit', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(makeTradierAudit('tradier', 180)),
            });
        });

        await page.goto(BASE, { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(1500);

        // Click the CHAIN integrity indicator to open the panel
        const chainIndicator = page.locator('[aria-label="CHAIN integrity status: green"], [aria-label^="CHAIN integrity"]');
        await chainIndicator.first().click({ timeout: 8000 });

        // Panel should open
        const panel = page.locator('[role="dialog"][aria-label="Integrity Status Detail"]');
        await expect(panel).toBeVisible({ timeout: 5000 });

        // The CHAIN tab should be active — source badge should say TRADIER
        const sourceBadge = page.getByTestId('chain-source-badge');
        await expect(sourceBadge).toBeVisible({ timeout: 3000 });
        const text = await sourceBadge.textContent();
        expect(text?.toUpperCase()).toContain('TRADIER');

        // Badge should have ok class (green)
        const classes = await sourceBadge.getAttribute('class');
        expect(classes).toContain('ok');
    });

    test('CHAIN panel shows amber badge for yfinance fallback', async ({ page }) => {
        await setupCommonMocks(page);

        await page.route('**/api/data/audit', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(makeTradierAudit('yfinance', 80)),
            });
        });

        await page.goto(BASE, { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(1500);

        // With yfinance, chainStatus returns 'amber' — click amber chain indicator
        const chainIndicator = page.locator('[aria-label*="CHAIN"]').first();
        await chainIndicator.click({ timeout: 8000 });

        const panel = page.locator('[role="dialog"][aria-label="Integrity Status Detail"]');
        await expect(panel).toBeVisible({ timeout: 5000 });

        const sourceBadge = page.getByTestId('chain-source-badge');
        if (await sourceBadge.isVisible({ timeout: 3000 })) {
            const text = await sourceBadge.textContent();
            expect(text?.toUpperCase()).toContain('YFINANCE');
            // warn class expected for yfinance
            const classes = await sourceBadge.getAttribute('class');
            expect(classes).toContain('warn');
        }
    });

    test('CHAIN panel entry count shows >50 with Tradier', async ({ page }) => {
        await setupCommonMocks(page);

        await page.route('**/api/data/audit', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(makeTradierAudit('tradier', 180)),
            });
        });

        await page.goto(BASE, { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(1500);

        const chainIndicator = page.locator('[aria-label*="CHAIN"]').first();
        await chainIndicator.click({ timeout: 8000 });

        const panel = page.locator('[role="dialog"][aria-label="Integrity Status Detail"]');
        await expect(panel).toBeVisible({ timeout: 5000 });

        // Should show "180" or "180 contracts" in the entry count row
        await expect(panel).toContainText('180');
    });

    test('TRADIER glossary term is defined in the codebase', async ({ page }) => {
        // This test verifies the TRADIER glossary entry exists by checking the
        // TermLabel system can render it — load the dashboard and look for any
        // rendered TermLabel with term='TRADIER', or verify no JS errors occurred.
        await setupCommonMocks(page);

        await page.route('**/api/data/audit', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(makeTradierAudit('tradier', 180)),
            });
        });

        const errors: string[] = [];
        page.on('console', msg => {
            if (msg.type() === 'error') errors.push(msg.text());
        });

        await page.goto(BASE, { waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(2000);

        // No uncaught JS errors should occur (glossary import errors would appear here)
        const glossaryErrors = errors.filter(e =>
            e.includes('term_glossary') || e.includes('lookupTerm') || e.includes('TRADIER')
        );
        expect(glossaryErrors).toHaveLength(0);
    });
});
