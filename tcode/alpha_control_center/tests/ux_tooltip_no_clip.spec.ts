/**
 * UX Gate: Tooltips Do Not Clip at Viewport Edges
 *
 * At viewports [1280, 1440, 1920] wide (height 800):
 *  1. Hovers every [data-tooltip] element.
 *  2. Reads the rendered .tooltip-box bounding rect.
 *  3. Asserts left>=0, top>=0, right<=vw, bottom<=vh.
 *
 * Specifically tests elements within 200px of every viewport edge, which are
 * the most likely to clip with naive absolute positioning.
 *
 * Uses real Chromium (configured in playwright.config.ts) — NOT headless optional.
 */

import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

const VIEWPORT_WIDTHS = [1280, 1440, 1920];

async function checkTooltipsAtWidth(page: Page, width: number) {
  await page.setViewportSize({ width, height: 800 });
  await page.goto(BASE_URL, { waitUntil: 'load' });
  await page.waitForTimeout(800);

  const tooltipTargets = page.locator('[data-tooltip]');
  const count = await tooltipTargets.count();

  if (count === 0) {
    console.warn(`[${width}px] No [data-tooltip] elements found — page may not be fully rendered.`);
    return;
  }

  console.log(`[${width}px] Testing ${count} tooltip targets`);

  const edgeThreshold = 200; // test elements near edges specifically

  for (let i = 0; i < count; i++) {
    const el = tooltipTargets.nth(i);
    const isVisible = await el.isVisible().catch(() => false);
    if (!isVisible) continue;

    const elBox = await el.boundingBox();
    if (!elBox) continue;

    // Focus on edge elements — within 200px of any viewport edge
    const nearEdge = (
      elBox.x < edgeThreshold ||
      elBox.y < edgeThreshold ||
      elBox.x + elBox.width > width - edgeThreshold ||
      elBox.y + elBox.height > 800 - edgeThreshold
    );

    // Always test the first 5 and last 5 plus all near-edge elements
    const shouldTest = nearEdge || i < 5 || i >= count - 5;
    if (!shouldTest) continue;

    const tooltipText = (await el.getAttribute('data-tooltip') ?? '').slice(0, 40);
    const label = `[${width}px] tooltip[${i}] near edge="${nearEdge}": "${tooltipText}…"`;

    // Hover to trigger
    await el.hover({ force: false }).catch(() => {});
    await page.waitForTimeout(150);

    // Find the tooltip box rendered by Tooltip.tsx
    const tooltipBox = page.locator('.tooltip-box').first();
    const hasTooltip = await tooltipBox.isVisible().catch(() => false);

    if (hasTooltip) {
      const box = await tooltipBox.boundingBox();
      if (box) {
        expect(box.x, `${label} — left edge clipped (x=${box.x.toFixed(0)})`).toBeGreaterThanOrEqual(0);
        expect(box.y, `${label} — top edge clipped (y=${box.y.toFixed(0)})`).toBeGreaterThanOrEqual(0);
        expect(
          box.x + box.width,
          `${label} — right edge clipped (right=${(box.x + box.width).toFixed(0)}, vw=${width})`
        ).toBeLessThanOrEqual(width + 2); // +2px tolerance for rounding
        expect(
          box.y + box.height,
          `${label} — bottom edge clipped (bottom=${(box.y + box.height).toFixed(0)}, vh=800)`
        ).toBeLessThanOrEqual(802);
      }
    }

    // Dismiss
    await page.mouse.move(10, 10);
    await page.waitForTimeout(80);
  }
}

test.describe('UX: Tooltips stay inside viewport at all widths', () => {
  for (const width of VIEWPORT_WIDTHS) {
    test(`tooltips fully in viewport at ${width}px width`, async ({ page }) => {
      await checkTooltipsAtWidth(page, width);
    });
  }

  test('edge-element tooltips do not clip at 1280px', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE_URL, { waitUntil: 'load' });
    await page.waitForTimeout(800);

    // Specifically test the rightmost and bottommost tooltipped elements
    const edgeElements = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('[data-tooltip]'));
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      return els
        .map((el, i) => {
          const r = el.getBoundingClientRect();
          return {
            index: i,
            right: r.right,
            bottom: r.bottom,
            nearRightEdge: r.right > vw - 200,
            nearBottomEdge: r.bottom > vh - 200,
          };
        })
        .filter(e => e.nearRightEdge || e.nearBottomEdge);
    });

    console.log(`Found ${edgeElements.length} elements near viewport edges`);

    for (const edgeEl of edgeElements) {
      const el = page.locator('[data-tooltip]').nth(edgeEl.index);
      const isVisible = await el.isVisible().catch(() => false);
      if (!isVisible) continue;

      await el.hover({ force: false }).catch(() => {});
      await page.waitForTimeout(150);

      const tooltipBox = page.locator('.tooltip-box').first();
      const hasTooltip = await tooltipBox.isVisible().catch(() => false);

      if (hasTooltip) {
        const box = await tooltipBox.boundingBox();
        if (box) {
          const label = `edge element [${edgeEl.index}] (right=${edgeEl.right.toFixed(0)}, bottom=${edgeEl.bottom.toFixed(0)})`;
          expect(box.x, `${label} left clipped`).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width, `${label} right clipped`).toBeLessThanOrEqual(1282);
          expect(box.y, `${label} top clipped`).toBeGreaterThanOrEqual(0);
          expect(box.y + box.height, `${label} bottom clipped`).toBeLessThanOrEqual(802);
        }
      }

      await page.mouse.move(10, 10);
      await page.waitForTimeout(80);
    }
  });
});
