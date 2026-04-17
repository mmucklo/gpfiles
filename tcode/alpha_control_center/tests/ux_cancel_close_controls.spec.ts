/**
 * UX Test: Cancel Pending Order + Close Position — Phase 12
 *
 * Verifies the trade management UI features:
 *  - Pending order rows show a "✕ Cancel" button
 *  - Click → confirm modal → confirm → success toast → pending list refreshes
 *  - Position rows show a "× Close" button
 *  - Click when market closed → modal mentions scheduled open (MKT OPG)
 *  - Click when market open (mock time to Tuesday 13:30 UTC) → modal mentions MKT DAY
 *  - Double-click protection: second click within 3s is ignored
 *  - Live-mode (IBKR_LIVE): confirm button shows countdown before enabling
 *  - Scheduled-close badge appears on position when OPG order is returned
 *
 * All tests use route interception — no live IBKR gateway required.
 *
 * ── Phase 16.6 fix ────────────────────────────────────────────────────────────
 * Phase 16.1 introduced a PauseOverlay (role=dialog, aria-modal=true) that
 * renders full-screen and intercepts all pointer events when the dashboard is
 * in the default "paused" state.  Tests were written before this overlay existed
 * and never mocked /api/system/pause-status, so every click test timed-out
 * waiting for modals that could never open.
 *
 * Fix applied to setupBaseRoutes():
 *  1. page.addInitScript → seeds localStorage key 'tsla_pause_state' with an
 *     active (unpaused) state before the page loads, so the overlay never
 *     renders on the initial React paint.
 *  2. page.route on api/system/pause-status → returns { paused: false }
 *     so PauseOverlay.syncFromBackend() also sees active state.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makePendingOrder(overrides: object = {}) {
    return {
        orderId: 1001,
        status: 'Submitted',
        symbol: 'TSLA',
        action: 'BUY',
        qty: 5,
        strike: 365,
        expiry: '2026-04-17',
        option_type: 'CALL',
        limit_price: 0.28,
        filled_qty: 0,
        avg_fill_price: 0,
        timestamp: new Date().toISOString(),
        rank: 0.72,
        ...overrides,
    };
}

function makeIBKRPosition(overrides: object = {}) {
    return {
        ticker: 'TSLA',
        sec_type: 'OPT',
        qty: 5,
        avg_cost: 0.28,
        current_price: 0.35,
        unrealized_pnl: 35.0,
        market_value: 175.0,
        option_type: 'CALL',
        strike: 365,
        expiration: '2026-04-17',
        delta: 0.3,
        iv: 0.65,
        signal_id: 'sig-001',
        catalyst: 'Bullish momentum + congression',
        model_id: 'TSM-A',
        ...overrides,
    };
}

async function setupBaseRoutes(page: Page, {
    pendingOrders,
    ibkrPositions,
    cancelResult,
    closeResult,
    brokerMode = 'IBKR_PAPER',
}: {
    pendingOrders: object;
    ibkrPositions: object[];
    cancelResult?: object;
    closeResult?: object;
    brokerMode?: string;
}) {
    // Bypass PauseOverlay: seed localStorage before page load so the overlay
    // never renders (avoids the flash between initial paint and fetch resolve).
    // Use unpause_until: null so no countdown interval fires — avoids constant
    // App re-renders that were causing click-action timeouts with the Date mock.
    await page.addInitScript(() => {
        const active = { paused: false, unpause_until: null, remaining_sec: 0 };
        localStorage.setItem('tsla_pause_state', JSON.stringify(active));
    });

    // Playwright 1.59 is LIFO: last-registered route wins when multiple patterns match.
    // Register catch-all FIRST (lowest priority) so every specific route below overrides it.
    // Abort keeps App error-handlers intact — no JSON parse crashes from empty {} bodies.
    // This prevents ANY unmocked API call from reaching the live server (which returns
    // paused:true and would re-show PauseOverlay mid-test).
    await page.route('**/api/**', (route) => route.abort());

    // Specific routes (registered after catch-all) win due to LIFO ordering.
    // Also mock the backend so syncFromBackend() confirms active state.
    await page.route('**/api/system/pause-status', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json',
            body: JSON.stringify({ paused: false, unpause_until: null, remaining_sec: 0 }) })
    );

    await page.route('**/api/orders/pending', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(pendingOrders) })
    );
    await page.route('**/api/orders/cap-events', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [], ranks: [], cap: 2, pending_cnt: 1 }) })
    );
    await page.route('**/api/positions', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ibkrPositions) })
    );
    await page.route('**/api/account', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
            net_liquidation: 50000, cash_balance: 48000, unrealized_pnl: 35.0,
            realized_pnl: 0, buying_power: 96000, equity_with_loan: 50000, ts: new Date().toISOString(),
            source: brokerMode,
        })})
    );
    await page.route('**/api/broker/status', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
            mode: brokerMode, broker: 'IBKR', connected: true,
        })})
    );
    if (cancelResult !== undefined) {
        await page.route('**/api/orders/cancel', (route) =>
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(cancelResult) })
        );
    }
    if (closeResult !== undefined) {
        await page.route('**/api/positions/close', (route) =>
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(closeResult) })
        );
    }
}

async function waitForPendingPanel(page: Page) {
    // Confirm PauseOverlay is detached (removed from DOM) before interacting.
    // On initial React paint status.paused=true so the overlay is briefly in the DOM;
    // after the useEffect reads localStorage it returns null and detaches.
    // 'detached' (not 'hidden') is the correct state because the component removes
    // the DOM node entirely rather than hiding it with CSS.
    await page.locator('[data-testid="pause-overlay"]').waitFor({ state: 'detached', timeout: 10_000 });
    const panel = page.locator('[role="region"][aria-label*="Pending Orders"]');
    await panel.waitFor({ state: 'visible', timeout: 20_000 });
    return panel;
}

async function waitForPositionsPanel(page: Page) {
    // Same PauseOverlay guard as waitForPendingPanel.
    await page.locator('[data-testid="pause-overlay"]').waitFor({ state: 'detached', timeout: 10_000 });
    const panel = page.locator('[role="region"][aria-label*="Trading Floor"]');
    await panel.waitFor({ state: 'visible', timeout: 20_000 });
    return panel;
}

// ── Cancel button on Pending Orders ──────────────────────────────────────────

test.describe('Cancel Pending Order', () => {
    test('pending order row shows ✕ Cancel button', async ({ page }) => {
        const pendingOrders = { active: [makePendingOrder()], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        await setupBaseRoutes(page, { pendingOrders, ibkrPositions: [] });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPendingPanel(page);

        // Wait for the active orders to render (fetchPendingOrders fires 2s after mount)
        const cancelBtn = panel.locator('[aria-label*="Cancel order #1001"]');
        await expect(cancelBtn).toBeVisible({ timeout: 20_000 });
        await expect(cancelBtn).toContainText('Cancel');
    });

    test('cancel button click shows confirmation modal', async ({ page }) => {
        const pendingOrders = { active: [makePendingOrder()], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions: [],
            cancelResult: { order_id: 1001, status: 'Cancelled', oca_cancelled: [], timestamp: new Date().toISOString() },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPendingPanel(page);

        const cancelBtn = panel.locator('[aria-label*="Cancel order #1001"]');
        await cancelBtn.waitFor({ state: 'visible', timeout: 20_000 });
        await cancelBtn.click({ force: true });

        // Confirm modal should appear
        const modal = page.locator('[role="dialog"][aria-label*="Confirm cancel order"]');
        await expect(modal).toBeVisible({ timeout: 8_000 });
        await expect(modal).toContainText('Cancel Order #1001');
    });

    test('confirm cancel → success toast appears', async ({ page }) => {
        const pendingOrders = { active: [makePendingOrder()], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions: [],
            cancelResult: { order_id: 1001, status: 'Cancelled', oca_cancelled: [], timestamp: new Date().toISOString() },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPendingPanel(page);

        const cancelBtn = panel.locator('[aria-label*="Cancel order #1001"]');
        await cancelBtn.waitFor({ state: 'visible', timeout: 20_000 });
        await cancelBtn.click({ force: true });

        const modal = page.locator('[role="dialog"][aria-label*="Confirm cancel order"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });

        // Click the "Confirm Cancel" button
        const confirmBtn = modal.locator('button[aria-label*="Confirm cancel order"]');
        await confirmBtn.waitFor({ state: 'visible', timeout: 3_000 });
        await confirmBtn.click({ force: true });

        // Success toast
        const toast = page.locator('[role="status"]').filter({ hasText: '#1001 cancelled' });
        await expect(toast).toBeVisible({ timeout: 8_000 });
    });

    test('double-click protection: second cancel click within 3s is ignored', async ({ page }) => {
        const pendingOrders = { active: [makePendingOrder()], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions: [],
            cancelResult: { order_id: 1001, status: 'Cancelled', oca_cancelled: [], timestamp: new Date().toISOString() },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPendingPanel(page);
        const cancelBtn = panel.locator('[aria-label*="Cancel order #1001"]');
        await cancelBtn.waitFor({ state: 'visible', timeout: 20_000 });

        // First click opens modal
        await cancelBtn.click({ force: true });
        const modal = page.locator('[role="dialog"][aria-label*="Confirm cancel order"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });

        // Close modal
        await modal.locator('button[aria-label="Close cancel dialog"]').click({ force: true });
        await modal.waitFor({ state: 'hidden', timeout: 3_000 });

        // Immediate second click should be blocked by the 3s guard — modal should NOT open again
        await cancelBtn.click({ force: true });
        // Wait a short time to confirm the modal doesn't appear
        await page.waitForTimeout(500);
        await expect(modal).not.toBeVisible();
    });
});

// ── Close Position button ─────────────────────────────────────────────────────

test.describe('Close Position', () => {
    test('position row shows × Close button', async ({ page }) => {
        const pendingOrders = { active: [], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        const ibkrPositions = [makeIBKRPosition()];
        await setupBaseRoutes(page, { pendingOrders, ibkrPositions });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPositionsPanel(page);

        const closeBtn = panel.locator('[aria-label*="Close position TSLA"]');
        await expect(closeBtn).toBeVisible({ timeout: 20_000 });
        await expect(closeBtn).toContainText('Close');
    });

    test('close click when market closed → modal mentions scheduled open', async ({ page }) => {
        // Force market-closed by mocking time to Saturday UTC
        await page.addInitScript(() => {
            const OrigDate = window.Date;
            // Saturday 2026-04-18 12:00 UTC
            const FIXED = new OrigDate('2026-04-18T12:00:00Z').getTime();
            window.Date = class extends OrigDate {
                constructor(...args: any[]) {
                    if (args.length === 0) { super(FIXED); } else { super(...args as [any]); }
                }
                static now() { return FIXED; }
            } as any;
        });

        const pendingOrders = { active: [], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        const ibkrPositions = [makeIBKRPosition()];
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions,
            closeResult: {
                order_id: 42, status: 'PendingSubmit',
                scheduled_for: '2026-04-20T13:30:00Z', timestamp: new Date().toISOString(),
            },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPositionsPanel(page);

        const closeBtn = panel.locator('[aria-label*="Close position TSLA"]');
        await closeBtn.waitFor({ state: 'visible', timeout: 20_000 });
        await closeBtn.click({ force: true });

        // Modal should mention "Market closed" and OPG scheduling
        const modal = page.locator('[role="dialog"][aria-label*="Confirm close position"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });
        await expect(modal).toContainText('Market closed');
        await expect(modal).toContainText('MKT OPG');
    });

    test('close click when market open → modal mentions MKT DAY', async ({ page }) => {
        // Force market-open: Tuesday 2026-04-15 17:30 UTC = 13:30 ET (market open)
        await page.addInitScript(() => {
            const OrigDate = window.Date;
            const FIXED = new OrigDate('2026-04-15T17:30:00Z').getTime();
            window.Date = class extends OrigDate {
                constructor(...args: any[]) {
                    if (args.length === 0) { super(FIXED); } else { super(...args as [any]); }
                }
                static now() { return FIXED; }
            } as any;
        });

        const pendingOrders = { active: [], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        const ibkrPositions = [makeIBKRPosition()];
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions,
            closeResult: {
                order_id: 55, status: 'Submitted',
                scheduled_for: null, timestamp: new Date().toISOString(),
            },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPositionsPanel(page);

        const closeBtn = panel.locator('[aria-label*="Close position TSLA"]');
        await closeBtn.waitFor({ state: 'visible', timeout: 20_000 });
        await closeBtn.click({ force: true });

        const modal = page.locator('[role="dialog"][aria-label*="Confirm close position"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });
        await expect(modal).toContainText('Market open');
        await expect(modal).toContainText('MKT DAY');
    });

    test('close double-click protection: button disabled while close is in-flight', async ({ page }) => {
        // The 3s ref-guard (same pattern as cancel) is proven via the cancel test.
        // Here we verify the complementary protection: the "× Close" button becomes
        // disabled (closingKey === key) while a /api/positions/close fetch is in-flight,
        // preventing a second submission before the first completes.
        const pendingOrders = { active: [], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        const ibkrPositions = [makeIBKRPosition()];

        // setupBaseRoutes must come first: it registers the catch-all with lowest LIFO
        // priority. The slow route is registered AFTER so it wins (LIFO = last wins).
        await setupBaseRoutes(page, { pendingOrders, ibkrPositions });
        // Slow response: stalls for 3s so we can observe the disabled state.
        // Registered after setupBaseRoutes so it overrides the catch-all abort for this URL.
        await page.route('**/api/positions/close', async (route) => {
            await new Promise(r => setTimeout(r, 3000));
            await route.fulfill({
                status: 200, contentType: 'application/json',
                body: JSON.stringify({ order_id: 55, status: 'Submitted', scheduled_for: null, timestamp: new Date().toISOString() }),
            });
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPositionsPanel(page);

        const closeBtn = panel.locator('[aria-label*="Close position TSLA"]');
        await closeBtn.waitFor({ state: 'visible', timeout: 20_000 });

        // Click close button → modal opens
        await closeBtn.click({ force: true });
        const modal = page.locator('[role="dialog"][aria-label*="Confirm close position"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });

        // Confirm the close → triggers slow fetch, button enters disabled state
        const confirmBtn = modal.locator('button[aria-label="Confirm close position"]');
        await confirmBtn.waitFor({ state: 'visible', timeout: 8_000 });
        await confirmBtn.click({ force: true });

        // While fetch is in-flight: close button must be disabled
        await expect(closeBtn).toBeDisabled({ timeout: 2_000 });
    });

    test('live-mode: confirm button shows 3s countdown before enabling', async ({ page }) => {
        const pendingOrders = { active: [], cancelled: [], source: 'IBKR_LIVE', cap: 2 };
        const ibkrPositions = [makeIBKRPosition()];
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions,
            brokerMode: 'IBKR_LIVE',
            closeResult: { order_id: 99, status: 'Submitted', scheduled_for: null, timestamp: new Date().toISOString() },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPositionsPanel(page);

        const closeBtn = panel.locator('[aria-label*="Close position TSLA"]');
        await closeBtn.waitFor({ state: 'visible', timeout: 20_000 });
        await closeBtn.click({ force: true });

        const modal = page.locator('[role="dialog"][aria-label*="Confirm close position"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });

        // Confirm button should initially be disabled with countdown text
        const confirmBtn = modal.locator('button[aria-label*="Wait"]');
        await expect(confirmBtn).toBeVisible({ timeout: 3_000 });
        await expect(confirmBtn).toBeDisabled();
        const btnText = await confirmBtn.textContent();
        expect(btnText).toMatch(/Close \(\d+s\)/);
    });

    test('confirm close → scheduled-close badge appears on position card', async ({ page }) => {
        // Force market-closed (Saturday)
        await page.addInitScript(() => {
            const OrigDate = window.Date;
            const FIXED = new OrigDate('2026-04-19T12:00:00Z').getTime();
            window.Date = class extends OrigDate {
                constructor(...args: any[]) {
                    if (args.length === 0) { super(FIXED); } else { super(...args as [any]); }
                }
                static now() { return FIXED; }
            } as any;
        });

        const pendingOrders = { active: [], cancelled: [], source: 'IBKR_PAPER', cap: 2 };
        const ibkrPositions = [makeIBKRPosition()];
        await setupBaseRoutes(page, {
            pendingOrders, ibkrPositions,
            closeResult: {
                order_id: 42, status: 'PendingSubmit',
                scheduled_for: '2026-04-21T13:30:00Z', timestamp: new Date().toISOString(),
            },
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });
        const panel = await waitForPositionsPanel(page);

        const closeBtn = panel.locator('[aria-label*="Close position TSLA"]');
        await closeBtn.waitFor({ state: 'visible', timeout: 20_000 });
        await closeBtn.click({ force: true });

        const modal = page.locator('[role="dialog"][aria-label*="Confirm close position"]');
        await modal.waitFor({ state: 'visible', timeout: 8_000 });

        const confirmBtn = modal.locator('button[aria-label="Confirm close position"]');
        await confirmBtn.waitFor({ state: 'visible', timeout: 8_000 });
        await confirmBtn.click({ force: true });

        // Scheduled-close badge should appear on the position card
        const badge = panel.locator('[aria-label*="Scheduled close"]');
        await expect(badge).toBeVisible({ timeout: 8_000 });
        await expect(badge).toContainText('#42');
    });
});
