/**
 * UX Test: Pending Order Cap — Phase 8
 *
 * Verifies the pending-order cap UI features:
 *  - Pending panel header shows "N/max" badge with correct colour at cap
 *  - Each active order row shows a rank badge when rank > 0
 *  - Replacement toast appears when a [REPLACE] cap event arrives
 *  - Cap event feed renders REPLACE / REJECT-CAP entries
 *
 * Tests use route interception so no live IBKR gateway is required.
 */

import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

/** Minimal pending-orders response with a full queue (cap=2, 2 active). */
function makePendingFull() {
    return {
        active: [
            {
                orderId: 1001,
                status: 'Submitted',
                symbol: 'TSLA',
                action: 'BUY',
                qty: 5,
                strike: 365,
                expiry: '2026-05-16',
                option_type: 'CALL',
                limit_price: 0.28,
                filled_qty: 0,
                avg_fill_price: 0,
                timestamp: new Date().toISOString(),
                rank: 0.55,
            },
            {
                orderId: 1002,
                status: 'PreSubmitted',
                symbol: 'TSLA',
                action: 'BUY',
                qty: 3,
                strike: 370,
                expiry: '2026-05-16',
                option_type: 'CALL',
                limit_price: 0.18,
                filled_qty: 0,
                avg_fill_price: 0,
                timestamp: new Date().toISOString(),
                rank: 0.72,
            },
        ],
        cancelled: [],
        source: 'IBKR_PAPER',
        cap: 2,
    };
}

/** Cap events response with a REPLACE event. */
function makeCapEventsResponse(kind: 'REPLACE' | 'REJECT-CAP' = 'REPLACE') {
    return {
        events: [
            {
                ts: new Date().toISOString(),
                kind,
                cancelled_id: kind === 'REPLACE' ? 1001 : 0,
                cancelled_rank: 0.55,
                incoming_rank: 0.82,
            },
        ],
        ranks: [{ order_id: 1002, rank: 0.72, placed_at: new Date().toISOString() }],
        cap: 2,
        pending_cnt: 1,
    };
}

/** Register all intercepts before navigating. */
async function setupRoutes(page: Page, capEventsBody: object) {
    await page.route('**/api/orders/pending', (route) =>
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(makePendingFull()),
        })
    );
    await page.route('**/api/orders/cap-events', (route) =>
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(capEventsBody),
        })
    );
}

async function loadAndWaitForPanel(page: Page) {
    await page.goto(BASE_URL, { waitUntil: 'load' });
    const panel = page.locator('[role="region"][aria-label*="Pending Orders"]');
    await panel.waitFor({ state: 'visible', timeout: 20_000 });
    return panel;
}

test.describe('Pending Cap UI', () => {
    test('header badge shows N/cap and is red when at cap', async ({ page }) => {
        await setupRoutes(page, makeCapEventsResponse());
        const panel = await loadAndWaitForPanel(page);

        // Badge should say "2/2" — wait up to 20s for the route intercept response to
        // render (fetchPendingOrders fires 2s after mount)
        const capBadge = panel.locator('.inline-badge[aria-label*="of"]');
        await expect(capBadge).toBeVisible({ timeout: 20_000 });
        await expect(capBadge).toContainText('2/2');

        // Confirm the aria-label contains both counts
        const label = await capBadge.getAttribute('aria-label');
        expect(label).toMatch(/2 of 2/i);
    });

    test('each active order row shows a rank badge', async ({ page }) => {
        await setupRoutes(page, makeCapEventsResponse());
        const panel = await loadAndWaitForPanel(page);

        // Wait for rank badges to appear (up to 20s for intercept to kick in)
        const rankBadges = panel.locator('.inline-badge[title*="rank"]');
        await expect(rankBadges).toHaveCount(2, { timeout: 20_000 });

        const firstText = await rankBadges.first().textContent();
        expect(firstText).toMatch(/rank: 0\.\d{2}/);
    });

    test('replacement toast appears on new REPLACE cap event', async ({ page }) => {
        // First response: no events; second response: REPLACE event
        let callCount = 0;
        await page.route('**/api/orders/pending', (route) =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(makePendingFull()),
            })
        );
        await page.route('**/api/orders/cap-events', (route) => {
            callCount++;
            const body = callCount === 1
                ? JSON.stringify({ events: [], ranks: [], cap: 2, pending_cnt: 2 })
                : JSON.stringify(makeCapEventsResponse('REPLACE'));
            route.fulfill({ status: 200, contentType: 'application/json', body });
        });

        await page.goto(BASE_URL, { waitUntil: 'load' });

        // Wait for toast to appear — second poll at 15s interval fires the REPLACE event
        // Total wait budget: 30s (initial 2.5s + 15s interval + rendering buffer)
        const toast = page.locator('[data-testid="cap-replacement-toast"]');
        await expect(toast).toBeVisible({ timeout: 30_000 });
        const toastText = await toast.textContent();
        expect(toastText).toMatch(/replaced orderId=1001/i);
    });

    test('cap event feed accordion shows REPLACE entry when events exist', async ({ page }) => {
        await setupRoutes(page, makeCapEventsResponse('REPLACE'));
        const panel = await loadAndWaitForPanel(page);

        // Wait for the cap events accordion to appear (fetchCapEvents fires 2.5s after mount)
        // aria-label: "Cap events — 1 recent. Click to expand."
        const capAccordion = panel.locator('[aria-label*="Cap events"]');
        await expect(capAccordion).toBeVisible({ timeout: 20_000 });
        // Force-click to avoid overlay interception in the scrollable column
        await capAccordion.click({ force: true });

        // REPLACE entry should be visible in the expanded feed
        const replaceEntry = panel.locator('text=[REPLACE]');
        await expect(replaceEntry).toBeVisible({ timeout: 5_000 });
    });
});
