/**
 * UX Gate: No Placeholder Text
 *
 * Loads the dashboard, waits for network idle + 3s, then asserts that rendered
 * page text does NOT contain literal placeholder strings that indicate missing
 * or loading state:  '...'  '--'  'N/A'  'Loading…'  'Loading...'
 *
 * Skeletons (role="presentation") and input placeholders are exempt — this test
 * only checks visible text content.
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

const FORBIDDEN_PATTERNS: { pattern: RegExp; description: string }[] = [
  { pattern: /(?<![A-Za-z0-9])\.{3}(?![A-Za-z0-9])/, description: '"..." (literal ellipsis as placeholder)' },
  { pattern: /(?<![A-Za-z])--(?![A-Za-z>])/, description: '"--" (double dash as placeholder)' },
  { pattern: /\bN\/A\b/, description: '"N/A" (not-available placeholder)' },
  { pattern: /Loading\.\.\.|Loading…/, description: '"Loading..." or "Loading…" (loading text)' },
  { pattern: /Fetching[…\.]{0,3}/i, description: '"Fetching…" or "Fetching" (fetching placeholder)' },
];

test.describe('UX: No placeholder text in rendered page', () => {
  test('page shows no "...", "--", "N/A", "Loading...", or "Fetching..." after 3s', async ({ page }) => {
    await page.goto(BASE_URL, { waitUntil: 'load' });
    // Wait an additional 3s so async data has time to arrive or error
    await page.waitForTimeout(3000);

    // Collect all visible text nodes (exclude skeleton placeholders, inputs, and code elements)
    const visibleTexts = await page.evaluate(() => {
      const results: string[] = [];
      const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
          acceptNode(node) {
            const el = node.parentElement;
            if (!el) return NodeFilter.FILTER_REJECT;

            // Skip invisible elements
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
              return NodeFilter.FILTER_REJECT;
            }

            // Skip skeleton elements (role=presentation, aria-hidden, .skeleton-*)
            if (
              el.getAttribute('role') === 'presentation' ||
              el.getAttribute('aria-hidden') === 'true' ||
              el.classList.contains('skeleton-line') ||
              el.classList.contains('skeleton-card') ||
              el.classList.contains('skeleton-pill') ||
              el.classList.contains('skeleton-table')
            ) {
              return NodeFilter.FILTER_REJECT;
            }

            // Skip input placeholders
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
              return NodeFilter.FILTER_REJECT;
            }

            // Skip code / pre (e.g. goroutine dumps, regex hints)
            if (el.tagName === 'CODE' || el.tagName === 'PRE') {
              return NodeFilter.FILTER_REJECT;
            }

            // Skip <option> elements (select options like "— select reason —")
            if (el.tagName === 'OPTION') {
              return NodeFilter.FILTER_REJECT;
            }

            const text = node.nodeValue?.trim() ?? '';
            if (text.length === 0) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
          },
        }
      );

      let node: Node | null;
      while ((node = walker.nextNode())) {
        const text = node.nodeValue?.trim() ?? '';
        if (text) results.push(text);
      }
      return results;
    });

    const allText = visibleTexts.join('\n');

    for (const { pattern, description } of FORBIDDEN_PATTERNS) {
      const matches = visibleTexts.filter(t => pattern.test(t));
      expect(
        matches,
        `Found forbidden placeholder ${description} in rendered text: ${JSON.stringify(matches.slice(0, 5))}`
      ).toHaveLength(0);
    }
  });
});
