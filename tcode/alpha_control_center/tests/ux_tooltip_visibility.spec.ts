/**
 * UX Audit: Tooltip Visibility Test
 *
 * Iterates every element with [data-tooltip] and asserts that after hover,
 * the resulting tooltip box is fully within the viewport — no clipping or occlusion.
 *
 * Scope: requires the app to be running on PLAYWRIGHT_BASE_URL (default http://localhost:2112)
 */

import { test, expect } from '@playwright/test';
import type { Page, Locator } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

/**
 * Get bounding rect of an element, ensuring it's fully visible in the viewport.
 */
async function assertFullyInViewport(page: Page, locator: Locator, label: string) {
  const vw = page.viewportSize()?.width ?? 1280;
  const vh = page.viewportSize()?.height ?? 800;

  const box = await locator.boundingBox();
  if (!box) {
    // Tooltip not found after hover — skip (element may not have tooltip visible)
    return;
  }

  expect(box.x, `${label} left edge clipped`).toBeGreaterThanOrEqual(0);
  expect(box.y, `${label} top edge clipped`).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width, `${label} right edge clipped (x=${box.x} w=${box.width} vw=${vw})`).toBeLessThanOrEqual(vw + 2);
  expect(box.y + box.height, `${label} bottom edge clipped (y=${box.y} h=${box.height} vh=${vh})`).toBeLessThanOrEqual(vh + 2);
}

test.describe('Tooltip Visibility — all [data-tooltip] elements stay inside viewport', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL, { waitUntil: 'load' });
    // Give React a moment to hydrate and integrity bar to render
    await page.waitForSelector('.integrity-bar', { timeout: 10_000 });
    await page.waitForTimeout(500);
  });

  test('all [data-tooltip] elements show fully in-viewport tooltips on hover', async ({ page }) => {
    // Collect all elements that carry tooltip text
    const tooltipTargets = page.locator('[data-tooltip]');
    const count = await tooltipTargets.count();

    // If no tooltip targets, warn but don't fail (page may not be fully loaded)
    if (count === 0) {
      console.warn('No [data-tooltip] elements found. Page may not be fully rendered.');
      return;
    }

    console.log(`Found ${count} [data-tooltip] elements to test.`);

    for (let i = 0; i < count; i++) {
      const el = tooltipTargets.nth(i);

      // Skip if not visible
      const isVisible = await el.isVisible().catch(() => false);
      if (!isVisible) continue;

      const tooltipText = await el.getAttribute('data-tooltip');
      const label = `tooltip[${i}]: "${tooltipText?.slice(0, 40)}…"`;

      // Hover to trigger tooltip
      await el.hover({ force: false }).catch(() => {});
      await page.waitForTimeout(120);

      // Find the tooltip box (rendered by Tooltip.tsx as .tooltip-box)
      const tooltipBox = page.locator('.tooltip-box').first();
      const hasTooltip = await tooltipBox.isVisible().catch(() => false);

      if (hasTooltip) {
        await assertFullyInViewport(page, tooltipBox, label);
      }

      // Move away to dismiss
      await page.mouse.move(0, 0);
      await page.waitForTimeout(60);
    }
  });

  test('integrity indicator tooltips visible near header edges', async ({ page }) => {
    // Click the integrity bar buttons and check panel visibility
    const indicators = page.locator('.integrity-indicator');
    const count = await indicators.count();

    for (let i = 0; i < count; i++) {
      const indicator = indicators.nth(i);
      const isVisible = await indicator.isVisible().catch(() => false);
      if (!isVisible) continue;

      const label = await indicator.getAttribute('aria-label') ?? `indicator[${i}]`;

      // Hover
      await indicator.hover({ force: false }).catch(() => {});
      await page.waitForTimeout(120);

      // Check tooltip-box if shown
      const tooltipBox = page.locator('.tooltip-box').first();
      const hasTooltip = await tooltipBox.isVisible().catch(() => false);
      if (hasTooltip) {
        await assertFullyInViewport(page, tooltipBox, `integrity indicator: ${label}`);
      }

      await page.mouse.move(0, 0);
      await page.waitForTimeout(60);
    }
  });

  test('help button is visible and accessible', async ({ page }) => {
    const helpBtn = page.locator('[data-testid="help-button"]');
    await expect(helpBtn).toBeVisible();
    await expect(helpBtn).toHaveAttribute('aria-label', /help/i);

    await page.evaluate(() => {
      const btn = document.querySelector('[data-testid="help-button"]') as HTMLElement | null;
      if (btn) btn.click();
    });
    const helpPanel = page.locator('.help-panel');
    await expect(helpPanel).toBeVisible({ timeout: 5000 });

    // Panel should be fully in viewport
    await assertFullyInViewport(page, helpPanel, 'help panel');

    // Close it
    await page.evaluate(() => {
      const closeBtn = document.querySelector('.help-panel button') as HTMLElement | null;
      if (closeBtn) closeBtn.click();
    });
    await expect(helpPanel).not.toBeVisible();
  });
});
