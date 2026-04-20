/**
 * Phase 18 UX Color Conformance Tests
 *
 * Tastytrade color law:
 * - #00C853 (green) = positive P&L ONLY
 * - #FF1744 (red)   = negative P&L ONLY (or critical circuit-breaker state)
 * - #FFB300 (amber) = warnings, system status
 * - #58a6ff (blue)  = info, system health, neutral
 *
 * Verifies:
 * - P&L amount uses correct green/red
 * - System health badge never uses red/green
 * - Guardrail bars use blue → amber → orange progression (not green/red for low/mid)
 * - Status bar P&L coloring matches sign of value
 * - Breakdown table P&L cells colored correctly
 */

import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

async function gotoAndWait(page: Page) {
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForSelector('[data-testid="status-bar"]', { timeout: 15_000 });
  await page.waitForTimeout(1500);
}

// Normalize rgb() / rgba() to hex for comparison
function rgbToHex(rgb: string): string {
  const m = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return rgb.toLowerCase();
  const r = parseInt(m[1]).toString(16).padStart(2, '0');
  const g = parseInt(m[2]).toString(16).padStart(2, '0');
  const b = parseInt(m[3]).toString(16).padStart(2, '0');
  return `#${r}${g}${b}`;
}

const GREEN_RE = /#00c853/i;
const RED_RE   = /#ff1744/i;

// ── P&L Color Correctness ─────────────────────────────────────────────────────

test.describe('P&L color correctness', () => {
  test('status bar P&L amount has green color when positive', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);

    const pnl = page.locator('[data-testid="sb-pnl-amount"]');
    await expect(pnl).toBeVisible();

    // Determine sign from CSS class rather than text (avoids +$0 false-positive)
    const classes = await pnl.evaluate(el => el.className);
    const isProfit = classes.includes('profit');
    const isLoss   = classes.includes('loss');

    if (isProfit) {
      const color = await pnl.evaluate(el => getComputedStyle(el).color);
      expect(rgbToHex(color), 'Profit P&L should use green (#00C853)').toMatch(GREEN_RE);
    } else if (isLoss) {
      const color = await pnl.evaluate(el => getComputedStyle(el).color);
      expect(rgbToHex(color), 'Loss P&L should use red (#FF1744)').toMatch(RED_RE);
    }
    // Zero class → neutral gray → no assertion needed
  });

  test('main P&L panel amount uses green for positive values', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);
    await page.waitForTimeout(2000);

    // Use first() to avoid strict-mode error when multiple .pnl-amount elements exist
    const pnlAmount = page.locator('.pnl-amount').first();
    const visible = await pnlAmount.count();
    if (visible === 0) return; // panel not rendered

    const classes = await pnlAmount.evaluate(el => el.className);
    const hasPositive = classes.includes('positive');
    const hasNegative = classes.includes('negative');
    const hasZero     = classes.includes('zero');

    // Should have exactly one sign class applied
    const classCount = [hasPositive, hasNegative, hasZero].filter(Boolean).length;
    expect(classCount, 'pnl-amount should have exactly one sign class').toBe(1);

    if (hasPositive) {
      const color = await pnlAmount.evaluate(el => getComputedStyle(el).color);
      expect(rgbToHex(color), 'Positive pnl-amount should be green').toMatch(GREEN_RE);
    }
    if (hasNegative) {
      const color = await pnlAmount.evaluate(el => getComputedStyle(el).color);
      expect(rgbToHex(color), 'Negative pnl-amount should be red').toMatch(RED_RE);
    }
    // Zero class → neutral gray → no assertion needed
  });

  test('breakdown table P&L cells use green/red correctly', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);
    await page.waitForTimeout(2000);

    const posCells = page.locator('.td-pnl.pos');
    const negCells = page.locator('.td-pnl.neg');

    const posCount = await posCells.count();
    const negCount = await negCells.count();

    for (let i = 0; i < posCount; i++) {
      const color = await posCells.nth(i).evaluate(el => getComputedStyle(el).color);
      const hex = rgbToHex(color);
      expect(hex, `Positive breakdown cell ${i} should be green`).toMatch(GREEN_RE);
    }

    for (let i = 0; i < negCount; i++) {
      const color = await negCells.nth(i).evaluate(el => getComputedStyle(el).color);
      const hex = rgbToHex(color);
      expect(hex, `Negative breakdown cell ${i} should be red`).toMatch(RED_RE);
    }
  });

  test('positions table P&L column uses green/red correctly', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);
    await page.waitForTimeout(2000);

    const profitCells = page.locator('.pnl-profit');
    const lossCells   = page.locator('.pnl-loss');

    for (let i = 0; i < await profitCells.count(); i++) {
      const color = await profitCells.nth(i).evaluate(el => getComputedStyle(el).color);
      const hex = rgbToHex(color);
      expect(hex, `pnl-profit cell ${i} should be green`).toMatch(GREEN_RE);
    }

    for (let i = 0; i < await lossCells.count(); i++) {
      const color = await lossCells.nth(i).evaluate(el => getComputedStyle(el).color);
      const hex = rgbToHex(color);
      expect(hex, `pnl-loss cell ${i} should be red`).toMatch(RED_RE);
    }
  });
});

// ── System Health Color Discipline ────────────────────────────────────────────

test.describe('System health badge color discipline', () => {
  test('health badge does not use green or red', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);

    const badge = page.locator('[data-testid="sb-health-badge"]').first();
    await expect(badge).toBeVisible();

    // Wait for health data to load (avoids flaky initial-render color check)
    await page.waitForTimeout(2000);

    const color = await badge.evaluate(el => getComputedStyle(el).color);
    const hex = rgbToHex(color);

    expect(hex, 'Health badge should NOT use P&L green').not.toMatch(GREEN_RE);
    expect(hex, 'Health badge should NOT use P&L red').not.toMatch(RED_RE);
  });

  test('mode badge does not use green or red for system state', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);

    const badge = page.locator('[data-testid="sb-mode-badge"]').first();
    await expect(badge).toBeVisible();

    // Wait for broker status to load (avoids checking default loading state)
    await page.waitForTimeout(2000);

    const color = await badge.evaluate(el => getComputedStyle(el).color);
    const hex = rgbToHex(color);

    // Mode badge should use blue or amber, not the P&L green/red
    expect(hex, 'Mode badge should NOT use P&L green').not.toMatch(GREEN_RE);
    // NOTE: LIVE mode does use red as a safety warning — only check non-live here
    const classes = await badge.evaluate(el => el.className);
    if (!classes.includes('live')) {
      expect(hex, 'Non-live mode badge should NOT use P&L red').not.toMatch(RED_RE);
    }
  });
});

// ── Guardrail Bar Color Progression ──────────────────────────────────────────

test.describe('Guardrail bar color progression', () => {
  test('guardrail fill element exists and is not green', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);
    await page.waitForTimeout(3000);

    const fills = page.locator('[data-testid="guardrail-loss-bar"]');
    const count = await fills.count();

    if (count === 0) return; // no data in test env — non-critical

    for (let i = 0; i < count; i++) {
      const fill = fills.nth(i);
      const bgColor = await fill.evaluate(el => getComputedStyle(el).backgroundColor);
      const hex = rgbToHex(bgColor);

      // Guardrail bars should NEVER use P&L green — they use blue/amber/orange
      expect(hex, `Guardrail bar ${i} should not use P&L green`).not.toMatch(GREEN_RE);
    }
  });

  test('warn-class guardrail bar uses amber color', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);
    await page.waitForTimeout(3000);

    const warnBars = page.locator('[data-testid="guardrail-loss-bar"].warn');
    const count = await warnBars.count();

    if (count === 0) return; // may not be in warn state — non-critical

    const bgColor = await warnBars.first().evaluate(el => getComputedStyle(el).backgroundColor);
    const hex = rgbToHex(bgColor);
    // Amber = #FFB300
    expect(hex, 'Warn guardrail should use amber').toMatch(/#ffb3/i);
  });
});

// ── Regression: no P&L colors leaked into non-P&L UI ─────────────────────────

test.describe('Color isolation — no green/red leakage into system UI', () => {
  test('tab bar active indicator is blue, not green/red', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);

    const activeTab = page.locator('[data-testid="tab-bar"] [role="tab"][aria-selected="true"]');
    const count = await activeTab.count();
    if (count === 0) return;

    // The active tab indicator uses border-bottom-color
    const borderColor = await activeTab.evaluate(el => getComputedStyle(el).borderBottomColor);
    const hex = rgbToHex(borderColor);

    // Should be blue (#58a6ff) not green/red
    expect(hex, 'Active tab border should not be P&L green').not.toMatch(GREEN_RE);
    expect(hex, 'Active tab border should not be P&L red').not.toMatch(RED_RE);
  });

  test('status bar background is dark, not pure black', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoAndWait(page);

    const bar = page.locator('[data-testid="status-bar"]');
    const bgColor = await bar.evaluate(el => getComputedStyle(el).backgroundColor);

    // Must not be pure black (#000000)
    const hex = rgbToHex(bgColor);
    expect(hex, 'Status bar background must not be pure black').not.toBe('#000000');
  });
});
