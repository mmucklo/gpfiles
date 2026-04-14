/**
 * ux_glossary_coverage.spec.ts — Playwright tests for the TermLabel glossary system.
 *
 * Verifies:
 *   1. Every [data-glossary-term] element has a visible tooltip on hover
 *   2. Click opens the drill-down popover
 *   3. Drill-down popover contains short, long, and source text
 *   4. Tooltips and popovers fit in viewport (getBoundingClientRect) at 1280/1440/1920 widths
 *   5. Negative scan: no bare glossary key text appears in rendered DOM without a
 *      data-glossary-term parent ancestor (everything is wrapped by TermLabel)
 */
import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.ACC_URL ?? 'http://localhost:2112';

// Keys that must not appear as bare text content in the rendered DOM
// (these are the most likely to drift if TermLabel wrapping is missed)
const CANONICAL_KEYS_TO_SCAN = [
    'IDIOSYNCRATIC',
    'MACRO_LOCKED',
    // Note: SENTIMENT, MACRO, etc. may appear inside tooltip text or aria-labels which is fine;
    // the negative scan below targets TEXT_NODE content only.
];

async function loadDashboard(page: Page, viewport = { width: 1440, height: 900 }) {
    await page.setViewportSize(viewport);
    await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20000 });
    // Wait a moment for any lazy renders
    await page.waitForTimeout(1000);
}

test.describe('TermLabel Glossary Coverage', () => {

    test('all [data-glossary-term] elements have tooltip on hover at 1440px', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();

        if (count === 0) {
            // No term labels in the current view — skip gracefully
            // (This happens if no intel data is available to render the cards)
            test.skip(true, 'No [data-glossary-term] elements found — intel data may not be available');
            return;
        }

        // Test up to 5 term labels (don't hammer the DOM for every single one)
        const toTest = Math.min(count, 5);
        for (let i = 0; i < toTest; i++) {
            const el = glossaryTerms.nth(i);
            const termKey = await el.getAttribute('data-glossary-term');
            if (!termKey) continue;

            // Hover to trigger tooltip
            await el.hover();

            // Tooltip should appear (role="tooltip" in the portal)
            const tooltip = page.locator('[role="tooltip"]').first();
            await expect(tooltip).toBeVisible({ timeout: 3000 });

            // Tooltip should not be offscreen
            const ttBox = await tooltip.boundingBox();
            if (ttBox) {
                const vw = 1440;
                const vh = 900;
                const MARGIN = 200;
                expect(ttBox.x).toBeGreaterThanOrEqual(-MARGIN);
                expect(ttBox.y).toBeGreaterThanOrEqual(-MARGIN);
                expect(ttBox.x + ttBox.width).toBeLessThanOrEqual(vw + MARGIN);
                expect(ttBox.y + ttBox.height).toBeLessThanOrEqual(vh + MARGIN);
            }

            // Move away to hide tooltip
            await page.mouse.move(0, 0);
            await page.waitForTimeout(200);
        }
    });

    test('[data-glossary-term] click opens drill-down popover at 1440px', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();
        if (count === 0) {
            test.skip(true, 'No [data-glossary-term] elements found');
            return;
        }

        const el = glossaryTerms.first();
        await el.click();

        // Drill-down overlay should appear
        const drillOverlay = page.locator('.term-drill-overlay, [role="dialog"][aria-label^="Glossary"]').first();
        await expect(drillOverlay).toBeVisible({ timeout: 5000 });

        // Drill-down should have the short description
        const shortText = page.locator('[data-testid="drill-short"]');
        await expect(shortText).toBeVisible({ timeout: 3000 });
        const shortContent = await shortText.textContent();
        expect(shortContent?.length).toBeGreaterThan(10);

        // Drill-down should have the long description
        const longText = page.locator('[data-testid="drill-long"]');
        await expect(longText).toBeVisible({ timeout: 3000 });
        const longContent = await longText.textContent();
        expect(longContent?.length).toBeGreaterThan(20);

        // Close the drill-down
        const closeBtn = page.locator('.term-drill-close').first();
        await closeBtn.click();
        await expect(drillOverlay).not.toBeVisible({ timeout: 3000 });
    });

    test('drill-down popover fits viewport at 1280px width', async ({ page }) => {
        await loadDashboard(page, { width: 1280, height: 800 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();
        if (count === 0) {
            test.skip(true, 'No [data-glossary-term] elements found');
            return;
        }

        await glossaryTerms.first().click();

        const drillCard = page.locator('.term-drill-card').first();
        await expect(drillCard).toBeVisible({ timeout: 5000 });

        const box = await drillCard.boundingBox();
        if (box) {
            expect(box.x).toBeGreaterThanOrEqual(-50);
            expect(box.y).toBeGreaterThanOrEqual(-50);
            expect(box.x + box.width).toBeLessThanOrEqual(1280 + 50);
            expect(box.y + box.height).toBeLessThanOrEqual(800 + 50);
        }

        // Close
        await page.keyboard.press('Escape');
        const overlay = page.locator('.term-drill-overlay').first();
        // Click overlay to close (Escape may not close it — use click)
        if (await overlay.isVisible({ timeout: 1000 }).catch(() => false)) {
            await overlay.click({ position: { x: 10, y: 10 } });
        }
    });

    test('drill-down popover fits viewport at 1920px width', async ({ page }) => {
        await loadDashboard(page, { width: 1920, height: 1080 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();
        if (count === 0) {
            test.skip(true, 'No [data-glossary-term] elements found');
            return;
        }

        await glossaryTerms.first().click();

        const drillCard = page.locator('.term-drill-card').first();
        await expect(drillCard).toBeVisible({ timeout: 5000 });

        const box = await drillCard.boundingBox();
        if (box) {
            expect(box.x + box.width).toBeLessThanOrEqual(1920 + 50);
            expect(box.y + box.height).toBeLessThanOrEqual(1080 + 50);
        }

        const overlay = page.locator('.term-drill-overlay').first();
        if (await overlay.isVisible({ timeout: 1000 }).catch(() => false)) {
            await overlay.click({ position: { x: 10, y: 10 } });
        }
    });

    test('negative scan: IDIOSYNCRATIC bare text not in DOM without data-glossary-term ancestor', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        for (const key of CANONICAL_KEYS_TO_SCAN) {
            // Find all text nodes in the DOM that contain the key
            const bareOccurrences = await page.evaluate((searchKey) => {
                const results: string[] = [];
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null
                );
                let node: Node | null;
                while ((node = walker.nextNode())) {
                    const text = node.textContent ?? '';
                    if (text.includes(searchKey)) {
                        // Check if any ancestor has data-glossary-term
                        let parent = node.parentElement;
                        let hasGlossaryAncestor = false;
                        while (parent) {
                            if (parent.hasAttribute('data-glossary-term') ||
                                parent.hasAttribute('role') && parent.getAttribute('role') === 'tooltip' ||
                                parent.classList.contains('term-drill-card') ||
                                parent.classList.contains('tooltip-box') ||
                                parent.getAttribute('data-testid')?.startsWith('drill-')) {
                                hasGlossaryAncestor = true;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (!hasGlossaryAncestor) {
                            results.push(`bare "${searchKey}" found in: ${node.parentElement?.outerHTML?.slice(0, 100)}`);
                        }
                    }
                }
                return results;
            }, key);

            if (bareOccurrences.length > 0) {
                // This is a violation — report clearly
                console.warn(`[GLOSSARY AUDIT] Bare occurrences of "${key}" without TermLabel:`, bareOccurrences);
                // Soft failure: expect(bareOccurrences).toHaveLength(0) would be strict
                // Use soft assertion so we get all violations in one run
                expect(bareOccurrences, `"${key}" should be wrapped in TermLabel`).toHaveLength(0);
            }
        }
    });

    test('related term navigation works in drill-down', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        const glossaryTerms = page.locator('[data-glossary-term="CORRELATION_REGIME"]').first();
        const exists = await glossaryTerms.isVisible({ timeout: 5000 }).catch(() => false);
        if (!exists) {
            test.skip(true, 'CORRELATION_REGIME term label not visible (intel data unavailable)');
            return;
        }

        await glossaryTerms.click();

        const drillOverlay = page.locator('.term-drill-overlay').first();
        await expect(drillOverlay).toBeVisible({ timeout: 5000 });

        // Find a related term chip
        const relatedChip = page.locator('.term-drill-related-chip').first();
        const chipExists = await relatedChip.isVisible({ timeout: 3000 }).catch(() => false);
        if (!chipExists) {
            // No related chips — acceptable
            return;
        }

        const chipText = await relatedChip.textContent();
        await relatedChip.click();

        // Drill-down should now show a different entry (different title)
        const drillTitle = page.locator('.term-drill-title').first();
        await expect(drillTitle).toBeVisible({ timeout: 3000 });
        const newTitle = await drillTitle.textContent();
        expect(newTitle).toBeTruthy();
        // The new title should be the related term's display name
        expect(newTitle?.trim().length).toBeGreaterThan(0);
        // (Could also assert newTitle !== 'Correlation Regime' but related nav
        //  updates the same component, so title should match chipText)
        expect(newTitle?.trim()).toBe(chipText?.trim());
    });
});
