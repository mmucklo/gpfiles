/**
 * ux_rejected_signals.spec.ts — Phase 14.3
 *
 * Playwright UI tests for the rejection drill-down feature.
 *
 * Test matrix:
 * 1. Open dashboard → click header rejection badge → RejectedSignalsPanel opens with table
 * 2. Filter by reason=LIQUIDITY_REJECT → only those rows shown
 * 3. Click a row → drill-down modal opens with all 5 sections
 * 4. Verify no placeholder text (…, --, N/A) when data is present
 * 5. Hover TermLabel → tooltip appears; click → popover shows glossary entry
 * 6. Tooltips/popovers fully in viewport at 1280, 1440, 1920
 * 7. Click "Comment on this rejection" → opens feedback form inline within modal
 */

import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.TEST_BASE_URL || 'http://localhost:2112';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function mockRejections(page: Page) {
  // Intercept /api/signals/rejections/summary to always return data
  await page.route('/api/signals/rejections/summary*', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 5,
        by_reason: { STRIKE_SELECT_FAIL: 3, LIQUIDITY_REJECT: 2 },
        by_model: { SENTIMENT: 3, MACRO: 2 },
        by_archetype: { DIRECTIONAL_STRONG: 3, MOMENTUM_BREAKOUT: 2 },
        since: '2026-04-14 00:00:00',
      }),
    });
  });

  // Intercept list endpoint
  await page.route('/api/signals/rejections?*', route => {
    const url = new URL(route.request().url());
    const reasonFilter = url.searchParams.get('reason');

    const allItems = [
      {
        id: 101, ts: '2026-04-14 10:23:45', model_id: 'SENTIMENT', model: 'SENTIMENT',
        direction: 'BULLISH', confidence: 0.82,
        option_type: 'CALL', opt_type: 'CALL', expiration_date: '2026-04-24', expiry: '2026-04-24',
        archetype: 'DIRECTIONAL_STRONG', chop_regime_at_rejection: 'CHOPPY',
        reason_code: 'STRIKE_SELECT_FAIL', reason: 'STRIKE_SELECT_FAIL',
        reason_detail: 'All 47 candidate strikes rejected: delta_band=23, liquidity=18, theta_cap=6',
        spot_at_rejection: 362.50,
      },
      {
        id: 102, ts: '2026-04-14 09:15:22', model_id: 'MACRO', model: 'MACRO',
        direction: 'BEARISH', confidence: 0.65,
        option_type: 'PUT', opt_type: 'PUT', expiration_date: '2026-04-24', expiry: '2026-04-24',
        archetype: 'MOMENTUM_BREAKOUT', chop_regime_at_rejection: null,
        reason_code: 'LIQUIDITY_REJECT', reason: 'LIQUIDITY_REJECT',
        reason_detail: 'volume=3 < MIN_OPTION_VOLUME_TODAY=50; bid=$0.03 < $0.10',
        spot_at_rejection: 361.00,
      },
    ];

    const filtered = reasonFilter
      ? allItems.filter(i => i.reason_code === reasonFilter)
      : allItems;

    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total_count: filtered.length, items: filtered, has_more: false }),
    });
  });

  // Intercept detail endpoint
  await page.route('/api/signals/rejections/101', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 101, ts: '2026-04-14 10:23:45',
        model_id: 'SENTIMENT', model: 'SENTIMENT',
        direction: 'BULLISH', confidence: 0.82,
        ticker: 'TSLA', option_type: 'CALL', opt_type: 'CALL',
        expiration_date: '2026-04-24', expiry: '2026-04-24',
        archetype: 'DIRECTIONAL_STRONG',
        chop_regime_at_rejection: 'CHOPPY',
        spot_at_rejection: 362.50,
        reason_code: 'STRIKE_SELECT_FAIL', reason: 'STRIKE_SELECT_FAIL',
        reason_detail: 'All 47 candidate strikes rejected: delta_band=23, liquidity=18, theta_cap=6',
        chain_snapshot: [
          { strike: 360, option_type: 'CALL', delta: 0.55, gamma: 0.009, theta: -0.14,
            vega: 0.18, volume: 2, open_interest: 80, bid: 5.20, ask: 5.60,
            is_candidate: true, candidate_filter_killed: 'LIQUIDITY: volume=2 < 50' },
          { strike: 365, option_type: 'CALL', delta: 0.42, gamma: 0.007, theta: -0.11,
            vega: 0.14, volume: 3, open_interest: 120, bid: 4.50, ask: 4.80,
            is_candidate: true, candidate_filter_killed: 'LIQUIDITY: volume=3 < 50' },
        ],
        strike_selector_breakdown: [
          { strike: 360, option_type: 'CALL', score: null, delta: 0.55,
            filter_killed: 'LIQUIDITY', filter_reason: 'volume=2 < MIN_OPTION_VOLUME_TODAY=50' },
          { strike: 365, option_type: 'CALL', score: null, delta: 0.42,
            filter_killed: 'LIQUIDITY', filter_reason: 'volume=3 < MIN_OPTION_VOLUME_TODAY=50' },
        ],
        regime_context: { macro_regime: 'RISK_OFF', correlation_regime: 'NORMAL' },
        target_strike_attempted: 365.0,
      }),
    });
  });

  await page.route('/api/signals/rejections/102', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 102, ts: '2026-04-14 09:15:22',
        model_id: 'MACRO', model: 'MACRO',
        direction: 'BEARISH', confidence: 0.65,
        ticker: 'TSLA', option_type: 'PUT', opt_type: 'PUT',
        expiration_date: '2026-04-24', expiry: '2026-04-24',
        archetype: 'MOMENTUM_BREAKOUT', chop_regime_at_rejection: null,
        spot_at_rejection: 361.00,
        reason_code: 'LIQUIDITY_REJECT', reason: 'LIQUIDITY_REJECT',
        reason_detail: 'volume=3 < MIN_OPTION_VOLUME_TODAY=50; bid=$0.03 < MIN_ABSOLUTE_BID=$0.10',
        chain_snapshot: null,
        strike_selector_breakdown: null,
        regime_context: { macro_regime: 'NEUTRAL', correlation_regime: 'NORMAL' },
        target_strike_attempted: null,
      }),
    });
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Rejection Drill-Down — Phase 14.3', () => {

  test.beforeEach(async ({ page }) => {
    await mockRejections(page);
    await page.goto(BASE_URL);
    // Wait for dashboard to be minimally loaded
    await page.waitForLoadState('networkidle', { timeout: 15000 });
  });

  test('Badge click opens RejectedSignalsPanel with table', async ({ page }) => {
    // Rejection badge should be visible (mocked summary returns total=5)
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await expect(badge).toBeVisible({ timeout: 10000 });
    await badge.click();

    // Panel should appear
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await expect(panel).toBeVisible();

    // Table rows should be present
    const rows = panel.locator('table tbody tr');
    await expect(rows).toHaveCount(2, { timeout: 5000 });
  });

  test('Filter by LIQUIDITY_REJECT shows only those rows', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await expect(panel).toBeVisible();

    // Click the LIQUIDITY_REJECT filter pill
    const pill = panel.getByRole('button', { name: 'LIQUIDITY_REJECT' });
    await pill.click();

    // Only 1 row should remain
    const rows = panel.locator('table tbody tr');
    await expect(rows).toHaveCount(1, { timeout: 5000 });
    await expect(rows.first()).toContainText('LIQUIDITY_REJECT');
  });

  test('Row click opens drill-down modal with 5 sections', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await expect(panel).toBeVisible();

    // Click first row (id=101)
    await panel.locator('table tbody tr').first().click();

    // Drill-down modal should appear
    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Verify all 5 section headers exist
    await expect(modal.getByText(/A\. Signal Meta/)).toBeVisible();
    await expect(modal.getByText(/B\. Why It Was Rejected/)).toBeVisible();
    await expect(modal.getByText(/C\. Market Context/)).toBeVisible();
    await expect(modal.getByText(/D\. Chain Snapshot/)).toBeVisible();
    await expect(modal.getByText(/E\. Actions/)).toBeVisible();
  });

  test('No placeholder text (…, N/A, --) when data is present', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await panel.locator('table tbody tr').first().click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Wait for content to load
    await expect(modal.getByText('STRIKE_SELECT_FAIL')).toBeVisible({ timeout: 5000 });

    const text = await modal.textContent();
    // These placeholders should not appear when real data is present
    expect(text).not.toMatch(/^\.{3}$/m);           // "..." as standalone text
    expect(text).not.toMatch(/^N\/A$/m);              // "N/A" as standalone
    expect(text).not.toMatch(/^--$/m);               // "--" as standalone
  });

  test('STRIKE_SELECT_FAIL modal shows candidate strike breakdown table', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await panel.locator('table tbody tr').first().click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible();

    // Breakdown table should be visible (2 candidate strikes)
    await expect(modal.getByText(/candidate strike evaluation/i)).toBeVisible();
    const breakdownRows = modal.locator('table').nth(1).locator('tbody tr');
    await expect(breakdownRows).toHaveCount(2);
  });

  test('Chain snapshot table visible and sortable', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await panel.locator('table tbody tr').first().click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible();

    // Chain snapshot section shows 2 rows
    const snapSection = modal.getByText(/D\. Chain Snapshot/);
    await expect(snapSection).toBeVisible();

    // Click to sort by OI header
    await modal.getByText('OI').first().click();
    // Table still renders correctly after sort
    const snapRows = modal.locator('table').last().locator('tbody tr');
    await expect(snapRows).toHaveCount(2);
  });

  test('LIQUIDITY_REJECT modal shows null chain snapshot with banner', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });

    // Click second row (id=102, LIQUIDITY_REJECT, no chain_snapshot)
    await panel.locator('table tbody tr').nth(1).click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible();

    // Chain snapshot should show the pre-14.3 banner
    await expect(modal.getByText(/chain snapshot not captured/i)).toBeVisible({ timeout: 5000 });
  });

  test('TermLabel hover shows tooltip within viewport at 1440px', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 });

    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await expect(panel).toBeVisible();
    await panel.locator('table tbody tr').first().click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible();

    // Hover on a TermLabel within the modal
    const termLabel = modal.locator('[data-glossary-term]').first();
    await termLabel.hover();

    const tooltip = page.locator('[role="tooltip"]');
    await expect(tooltip).toBeVisible({ timeout: 3000 });

    // Verify tooltip is fully within viewport
    const tooltipBox = await tooltip.boundingBox();
    const viewport = page.viewportSize()!;
    expect(tooltipBox!.x).toBeGreaterThanOrEqual(0);
    expect(tooltipBox!.y).toBeGreaterThanOrEqual(0);
    expect(tooltipBox!.x + tooltipBox!.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(tooltipBox!.y + tooltipBox!.height).toBeLessThanOrEqual(viewport.height + 1);
  });

  test('TermLabel click opens glossary popover with full entry', async ({ page }) => {
    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await panel.locator('table tbody tr').first().click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible();

    // Click a TermLabel
    const termLabel = modal.locator('[data-glossary-term]').first();
    await termLabel.click();

    // Popover/drill-down card should appear
    const popover = page.locator('.term-drill-card');
    await expect(popover).toBeVisible({ timeout: 3000 });
    await expect(popover.locator('[data-testid="drill-short"]')).toBeVisible();
  });

  test('Comment button opens inline feedback form', async ({ page }) => {
    // Mock the feedback POST endpoint
    await page.route('/api/signals/feedback', route => {
      if (route.request().method() === 'POST') {
        route.fulfill({ status: 200, body: JSON.stringify({ ok: true }) });
      } else {
        route.fulfill({ status: 200, body: JSON.stringify({ rows: [] }) });
      }
    });

    const badge = page.getByRole('button', { name: /rejected signal/i });
    await badge.click();
    const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
    await panel.locator('table tbody tr').first().click();

    const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
    await expect(modal).toBeVisible();

    // Click "Comment on this rejection"
    await modal.getByRole('button', { name: /comment on this rejection/i }).click();

    // Textarea should appear
    await expect(modal.locator('textarea')).toBeVisible({ timeout: 2000 });
    await expect(modal.locator('select')).toBeVisible();  // tag selector

    // Type a comment and save
    await modal.locator('textarea').fill('Test comment for Phase 14.3 drill-down');
    await modal.getByRole('button', { name: /save comment/i }).click();

    // Success message
    await expect(modal.getByText('Comment saved')).toBeVisible({ timeout: 3000 });
  });

  test.describe('Viewport at 1280px', () => {
    test('Tooltips in viewport at 1280', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto(BASE_URL);
      await page.waitForLoadState('networkidle', { timeout: 15000 });

      const badge = page.getByRole('button', { name: /rejected signal/i });
      await badge.click();
      const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
      await panel.locator('table tbody tr').first().click();

      const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
      await expect(modal).toBeVisible();

      const termLabel = modal.locator('[data-glossary-term]').first();
      await termLabel.hover();

      const tooltip = page.locator('[role="tooltip"]');
      await expect(tooltip).toBeVisible({ timeout: 3000 });

      const tooltipBox = await tooltip.boundingBox();
      const viewport = page.viewportSize()!;
      expect(tooltipBox!.x).toBeGreaterThanOrEqual(0);
      expect(tooltipBox!.x + tooltipBox!.width).toBeLessThanOrEqual(viewport.width + 1);
      expect(tooltipBox!.y + tooltipBox!.height).toBeLessThanOrEqual(viewport.height + 1);
    });
  });

  test.describe('Viewport at 1920px', () => {
    test('Tooltips in viewport at 1920', async ({ page }) => {
      await page.setViewportSize({ width: 1920, height: 1080 });
      await page.goto(BASE_URL);
      await page.waitForLoadState('networkidle', { timeout: 15000 });

      const badge = page.getByRole('button', { name: /rejected signal/i });
      await badge.click();
      const panel = page.getByRole('dialog', { name: /rejected signals panel/i });
      await panel.locator('table tbody tr').first().click();

      const modal = page.getByRole('dialog', { name: /rejection drill-down/i });
      await expect(modal).toBeVisible();

      const termLabel = modal.locator('[data-glossary-term]').first();
      await termLabel.hover();

      const tooltip = page.locator('[role="tooltip"]');
      await expect(tooltip).toBeVisible({ timeout: 3000 });

      const tooltipBox = await tooltip.boundingBox();
      expect(tooltipBox!.x).toBeGreaterThanOrEqual(0);
      expect(tooltipBox!.x + tooltipBox!.width).toBeLessThanOrEqual(1921);
      expect(tooltipBox!.y + tooltipBox!.height).toBeLessThanOrEqual(1081);
    });
  });

});
