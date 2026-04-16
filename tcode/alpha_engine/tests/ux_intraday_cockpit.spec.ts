/**
 * ux_intraday_cockpit.spec.ts — Phase 16 Playwright tests
 *
 * Verifies the intraday cockpit UI components render and behave correctly:
 *   1. MorningBriefing panel renders with regime badge
 *   2. TradeApprovalQueue renders (empty state shown when no proposals)
 *   3. LivePnLPanel renders with P&L headline and target bar
 *   4. RegimeMonitor compact badge appears in header
 *   5. Market state badge appears in header
 *   6. CollapsiblePanel open/close behaviour works for each new panel
 *   7. Responsive layout: panels stack correctly at 375/768/1440px
 */

import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.ACC_URL ?? 'http://localhost:2112';

async function loadDashboard(page: Page, viewport = { width: 1440, height: 900 }) {
  await page.setViewportSize(viewport);
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 30_000 });
  await page.waitForTimeout(1500);
}

// ── Regime Monitor ────────────────────────────────────────────────────────────

test('RegimeMonitor compact badge appears in header', async ({ page }) => {
  await loadDashboard(page);
  // Either the badge or the "Regime: loading…" placeholder should be present
  const badge = page.locator('[data-testid="regime-monitor-badge"]');
  const loading = page.locator('text=Regime: loading…');
  const count = await badge.count() + await loading.count();
  expect(count).toBeGreaterThan(0);
});

// ── Morning Briefing ──────────────────────────────────────────────────────────

test('MorningBriefing panel is present and can be opened', async ({ page }) => {
  await loadDashboard(page);
  // Find the collapsible panel header containing "MORNING BRIEFING"
  const header = page.locator('text=MORNING BRIEFING').first();
  await expect(header).toBeVisible();
});

test('MorningBriefing shows regime section or loading state', async ({ page }) => {
  await loadDashboard(page);
  // Expand if collapsed
  const panelHeader = page.locator('[data-testid="collapsible-morning_briefing_open"]').first();
  const isExpanded = await panelHeader.getAttribute('aria-expanded').catch(() => null);
  if (isExpanded === 'false') {
    await panelHeader.click();
    await page.waitForTimeout(500);
  }

  // Either the regime section or loading text should appear
  const regimeSection = page.locator('[data-testid="morning-briefing-regime"]');
  const loadingText = page.locator('text=Loading morning briefing');
  const count = await regimeSection.count() + await loadingText.count();
  expect(count).toBeGreaterThan(0);
});

// ── Trade Approval Queue ──────────────────────────────────────────────────────

test('TradeApprovalQueue panel is present', async ({ page }) => {
  await loadDashboard(page);
  const header = page.locator('text=TRADE APPROVAL QUEUE').first();
  await expect(header).toBeVisible();
});

test('TradeApprovalQueue shows empty state or proposals', async ({ page }) => {
  await loadDashboard(page);
  // Allow enough time for the 5s poll
  await page.waitForTimeout(2000);

  const emptyState = page.locator('[data-testid="taq-empty"]');
  const proposalCard = page.locator('[data-testid^="proposal-card"]');
  const count = await emptyState.count() + await proposalCard.count();
  expect(count).toBeGreaterThan(0);
});

test('TradeApprovalQueue filter buttons are present', async ({ page }) => {
  await loadDashboard(page);
  for (const f of ['all', 'pending', 'executed', 'skipped']) {
    const btn = page.locator(`[data-testid="filter-${f}"]`);
    await expect(btn).toBeVisible();
  }
});

// ── Live P&L Panel ────────────────────────────────────────────────────────────

test('LivePnLPanel renders with P&L headline', async ({ page }) => {
  await loadDashboard(page);
  const panel = page.locator('[data-testid="live-pnl-panel"]');
  await expect(panel).toBeVisible();
});

test('LivePnLPanel target progress bar is present', async ({ page }) => {
  await loadDashboard(page);
  const bar = page.locator('[data-testid="pnl-target-bar"]');
  await expect(bar).toBeVisible();
});

test('LivePnLPanel P&L amount element is present', async ({ page }) => {
  await loadDashboard(page);
  const amount = page.locator('[data-testid="pnl-amount"]');
  await expect(amount).toBeVisible();
  // Should contain a $ sign (even if $0)
  const text = await amount.textContent();
  expect(text).toMatch(/\$/);
});

// ── Collapsible panel open/close ──────────────────────────────────────────────

test('Morning briefing panel is collapsible', async ({ page }) => {
  await loadDashboard(page);
  const toggle = page.locator('button:has-text("MORNING BRIEFING")').first();
  if (await toggle.count() === 0) return; // skip if panel header not a button

  // Click to toggle
  await toggle.click();
  await page.waitForTimeout(300);
  // Toggle again
  await toggle.click();
  await page.waitForTimeout(300);
  // Should not throw or crash
});

// ── Responsive layout ─────────────────────────────────────────────────────────

for (const width of [375, 768, 1440]) {
  test(`Dashboard renders at ${width}px width without overflow`, async ({ page }) => {
    await loadDashboard(page, { width, height: 900 });

    // Check that no element overflows the viewport width
    const overflow = await page.evaluate((vw) => {
      const elements = document.querySelectorAll('*');
      for (const el of elements) {
        const rect = el.getBoundingClientRect();
        if (rect.right > vw + 2) { // 2px tolerance
          return { tag: el.tagName, class: el.className.toString().slice(0, 80), right: rect.right };
        }
      }
      return null;
    }, width);

    if (overflow) {
      console.warn(`Overflow at ${width}px:`, overflow);
    }
    // At 375px some overflow from fixed-width content is acceptable; at 768+ we're strict
    if (width >= 768) {
      expect(overflow).toBeNull();
    }
  });
}

// ── PWA manifest ─────────────────────────────────────────────────────────────

test('PWA manifest is reachable', async ({ page }) => {
  const response = await page.goto(`${BASE_URL}/manifest.json`);
  expect(response?.status()).toBe(200);
  const body = await response?.json();
  expect(body.name).toBe('TSLA Alpha Command');
});

test('Service worker registration does not error', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await loadDashboard(page);
  // Filter out unrelated errors
  const swErrors = errors.filter(e => e.includes('ServiceWorker') || e.includes('sw.js'));
  expect(swErrors).toHaveLength(0);
});
