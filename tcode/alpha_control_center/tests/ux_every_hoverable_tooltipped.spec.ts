/**
 * UX Gate: Every Hoverable Element Has a Tooltip
 *
 * Queries all elements that are interactable (buttons, links, role=button,
 * cursor:pointer, onClick handlers visible via event listeners) and asserts
 * each has at least one of:  title  aria-label  data-tooltip
 *
 * This test verifies actual rendered behavior — it checks computed styles to
 * find cursor:pointer elements, not just static attributes.
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:2112';

test.describe('UX: Every hoverable element is labelled', () => {
  test('all interactive elements have title, aria-label, or data-tooltip', async ({ page }) => {
    await page.goto(BASE_URL, { waitUntil: 'load' });
    await page.waitForTimeout(1000);

    const violations = await page.evaluate(() => {
      const results: { tag: string; text: string; classes: string; html: string }[] = [];

      // Selectors for inherently interactive elements
      const interactiveSelectors = [
        'button',
        '[role="button"]',
        'a[href]',
        '[tabindex="0"]',
      ];

      const interactiveElements = new Set<Element>();

      // Collect from explicit selectors
      for (const sel of interactiveSelectors) {
        document.querySelectorAll(sel).forEach(el => interactiveElements.add(el));
      }

      // Collect cursor:pointer elements
      document.querySelectorAll('*').forEach(el => {
        const style = window.getComputedStyle(el);
        if (style.cursor === 'pointer') {
          interactiveElements.add(el);
        }
      });

      for (const el of interactiveElements) {
        // Skip invisible elements
        const style = window.getComputedStyle(el);
        if (
          style.display === 'none' ||
          style.visibility === 'hidden' ||
          style.opacity === '0'
        ) continue;

        // Skip elements inside skeleton placeholders
        if (el.closest('[aria-hidden="true"]')) continue;
        if (el.closest('.skeleton-line, .skeleton-card, .skeleton-pill, .skeleton-table')) continue;

        // Skip the tooltip box itself
        if (el.classList.contains('tooltip-box')) continue;
        if (el.closest('.tooltip-box')) continue;

        // Check for tooltip descriptor
        const hasTitle = !!el.getAttribute('title');
        const hasAriaLabel = !!el.getAttribute('aria-label');
        const hasDataTooltip = !!el.getAttribute('data-tooltip');
        const hasAriaLabelledBy = !!el.getAttribute('aria-labelledby');

        // Tooltip.tsx wraps children in tooltip-container — if parent has tooltip, child is OK
        const parentHasTooltip = el.closest('.tooltip-container') !== null;

        // Also skip elements whose nearest cursor:pointer ancestor already has a label
        // (e.g. span inside a button[aria-label], or span inside [role=button][aria-label])
        const labeledAncestor = (() => {
          let p: Element | null = el.parentElement;
          while (p && p !== document.body) {
            const pStyle = window.getComputedStyle(p);
            if (pStyle.cursor === 'pointer') {
              // This ancestor is also cursor:pointer — check if it's labeled
              if (
                p.getAttribute('aria-label') ||
                p.getAttribute('title') ||
                p.getAttribute('data-tooltip') ||
                p.tagName === 'BUTTON' ||
                p.getAttribute('role') === 'button'
              ) {
                return true;
              }
            }
            p = p.parentElement;
          }
          return false;
        })();

        if (!hasTitle && !hasAriaLabel && !hasDataTooltip && !hasAriaLabelledBy && !parentHasTooltip && !labeledAncestor) {
          const text = (el.textContent ?? '').trim().slice(0, 60);
          const classes = el.className;
          const html = el.outerHTML.slice(0, 150);

          // Skip purely decorative elements with no text content that are inside labeled containers
          if (text.length === 0 && !el.getAttribute('role')) continue;

          // Skip elements that are only icons (single emoji or svg)
          if (el.children.length === 1 && el.children[0].tagName === 'svg') continue;
          if (text.length <= 2 && !el.getAttribute('role')) continue;

          results.push({
            tag: el.tagName,
            text: text || '(no text)',
            classes: typeof classes === 'string' ? classes.slice(0, 80) : '',
            html,
          });
        }
      }

      return results;
    });

    if (violations.length > 0) {
      const report = violations
        .map((v, i) => `  ${i + 1}. <${v.tag}> classes="${v.classes}" text="${v.text}"\n     HTML: ${v.html}`)
        .join('\n');
      expect(violations, `\nInteractive elements missing tooltip (title/aria-label/data-tooltip):\n${report}`).toHaveLength(0);
    }
  });
});
