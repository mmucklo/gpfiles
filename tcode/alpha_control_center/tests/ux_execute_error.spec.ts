/**
 * Phase 18.1 — Execute error surfacing tests
 *
 * Verifies:
 * - API 500 → red persistent toast with error message
 * - Proposal card shows "FAILED" overlay after execute error
 * - API success → green toast with confirmation
 * - Dismiss button clears error toast
 *
 * Route-interception pattern follows ux_cancel_close_controls.spec.ts:
 *   - Abort all unmatched API calls to prevent the live server returning paused:true
 *   - Seed localStorage pause state + mock pause-status so PauseOverlay never renders
 *   - Register catch-all FIRST, specific routes after (LIFO: last wins)
 */

import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

const PROPOSAL_ID = 'test-exec-err-001';

const PENDING_PROPOSAL = {
  id: PROPOSAL_ID,
  ts_created: new Date(Date.now() - 5000).toISOString(),
  ts_expires: new Date(Date.now() + 55000).toISOString(),
  status: 'pending',
  strategy: 'GAMMA_SCALP',
  direction: 'BULLISH',
  legs: [{ strike: 400, type: 'CALL', action: 'BUY', quantity: 1, fill_price: null }],
  entry_price: 5.0,
  stop_price: 3.0,
  target_price: 8.0,
  kelly_fraction: 0.05,
  quantity: 1,
  confidence: 0.72,
  regime_snapshot: { regime: 'BULLISH_TREND', confidence: 0.8 },
  signals_contributing: ['GAMMA_SCALP'],
};

const QUEUE_RESPONSE = {
  proposals: [PENDING_PROPOSAL],
  stats: { pending: 1, executed: 0, skipped: 0, expired: 0 },
  updated_at: new Date().toISOString(),
};

const FAILED_PROPOSAL = { ...PENDING_PROPOSAL, status: 'execute_failed' };

async function setupRoutes(
  page: Page,
  executeResult: { status: number; body: object },
  queueProposals = QUEUE_RESPONSE,
) {
  // Bypass PauseOverlay: seed localStorage before page load.
  await page.addInitScript(() => {
    const active = { paused: false, unpause_until: null, remaining_sec: 0 };
    localStorage.setItem('tsla_pause_state', JSON.stringify(active));
  });

  // Catch-all registered FIRST (lowest LIFO priority) — aborts unmocked calls
  // so live server can't return paused:true and re-trigger the overlay.
  await page.route('**/api/**', (route) => route.abort());

  // Specific routes override the catch-all (LIFO: last-registered wins).
  await page.route('**/api/system/pause-status', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ paused: false, unpause_until: null, remaining_sec: 0 }),
    })
  );
  await page.route('**/api/broker/status', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ mode: 'IBKR_PAPER', broker: 'IBKR', connected: true }),
    })
  );
  await page.route('**/api/account', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        net_liquidation: 50000, cash_balance: 48000, unrealized_pnl: 0,
        realized_pnl: 0, buying_power: 96000, equity_with_loan: 50000,
        ts: new Date().toISOString(), source: 'IBKR_PAPER',
      }),
    })
  );
  await page.route('**/api/trades/proposed', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(queueProposals),
    })
  );
  await page.route(`**/api/trades/proposed/${PROPOSAL_ID}/execute`, (route) =>
    route.fulfill({
      status: executeResult.status,
      contentType: 'application/json',
      body: JSON.stringify(executeResult.body),
    })
  );
}

async function gotoAndWait(page: Page) {
  await page.goto(BASE, { waitUntil: 'load' });
  // Wait for PauseOverlay to detach (React re-renders after reading localStorage)
  await page.locator('[data-testid="pause-overlay"]').waitFor({ state: 'detached', timeout: 10_000 });
  await page.waitForSelector('[data-testid="status-bar"]', { timeout: 10_000 });
  await page.waitForTimeout(1000);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test('execute API 500 → red toast with error message', async ({ page }) => {
  await setupRoutes(page, {
    status: 500,
    body: { ok: false, error: 'expiry is empty — proposal missing expiration_date' },
  });

  await gotoAndWait(page);

  const execBtn = page.locator(`[data-testid="execute-btn-${PROPOSAL_ID}"]`);
  await execBtn.waitFor({ timeout: 8000 });
  await execBtn.click();

  const toast = page.locator('[data-testid="execute-toast"]');
  await toast.waitFor({ timeout: 8000 });
  await expect(toast).toBeVisible();

  const toastText = await toast.textContent();
  expect(toastText).toContain('expiry is empty');

  // Error toast has a dismiss button (not auto-dismissed)
  await expect(toast.locator('button[aria-label="Dismiss"]')).toBeVisible();
});

test('dismiss button clears error toast', async ({ page }) => {
  await setupRoutes(page, {
    status: 500,
    body: { ok: false, error: 'strike is 0 — proposal missing recommended_strike' },
  });

  await gotoAndWait(page);

  const execBtn = page.locator(`[data-testid="execute-btn-${PROPOSAL_ID}"]`);
  await execBtn.waitFor({ timeout: 8000 });
  await execBtn.click();

  const toast = page.locator('[data-testid="execute-toast"]');
  await toast.waitFor({ timeout: 8000 });

  await toast.locator('button[aria-label="Dismiss"]').click();
  await expect(toast).not.toBeVisible();
});

test('execute_failed status → FAILED overlay on proposal card', async ({ page }) => {
  await setupRoutes(
    page,
    { status: 200, body: { ok: true } }, // won't be called — proposal already failed
    { ...QUEUE_RESPONSE, proposals: [FAILED_PROPOSAL] },
  );

  await gotoAndWait(page);

  const card = page.locator(`[data-testid="proposal-card-${PROPOSAL_ID}"]`);
  await card.waitFor({ timeout: 8000 });

  const overlay = card.locator('.proposal-status-overlay.execute_failed');
  await expect(overlay).toBeVisible();
  await expect(overlay).toContainText('FAILED');
});

test('execute success → green toast with confirmation', async ({ page }) => {
  await setupRoutes(page, {
    status: 200,
    body: { ok: true, status: 'execute', order_result: { parent_order_id: 99001 } },
  });

  await gotoAndWait(page);

  const execBtn = page.locator(`[data-testid="execute-btn-${PROPOSAL_ID}"]`);
  await execBtn.waitFor({ timeout: 8000 });
  await execBtn.click();

  const toast = page.locator('[data-testid="execute-toast"]');
  await toast.waitFor({ timeout: 8000 });
  await expect(toast).toBeVisible();

  const toastText = await toast.textContent();
  expect(toastText).toContain('Order submitted');

  // Success toasts auto-dismiss — no persistent dismiss button
  await expect(toast.locator('button[aria-label="Dismiss"]')).not.toBeVisible();
});
