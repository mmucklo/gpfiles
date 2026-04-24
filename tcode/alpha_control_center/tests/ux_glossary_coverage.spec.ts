/**
 * ux_glossary_coverage.spec.ts — Playwright tests for the TermLabel glossary system.
 *
 * Authoritative location: alpha_control_center/tests/
 * (A misplaced copy previously existed in alpha_engine/tests/ — this file supersedes it.)
 *
 * Verifies:
 *   1. Every [data-glossary-term] element has a visible tooltip on hover
 *   2. Click opens the drill-down popover
 *   3. Drill-down popover contains short and long description text
 *   4. Tooltips and popovers fit in viewport (getBoundingClientRect) at 1280/1440/1920 widths
 *   5. Negative scan: no bare glossary key text appears in rendered DOM without a
 *      data-glossary-term parent ancestor (everything is wrapped by TermLabel)
 *   6. Related term navigation works within the drill-down
 *
 * If no [data-glossary-term] elements are found (e.g. intel data unavailable in the test
 * environment), each test skips gracefully with a console.warn rather than failing.
 * This is intentional: the glossary feature is data-driven and may not render without
 * a live intel feed.  The skip message makes the reason explicit so CI noise is avoided
 * while still flagging regressions when data IS present.
 */
import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:2112';

// Keys that must not appear as bare text content in the rendered DOM.
// These are the most likely to drift if TermLabel wrapping is missed.
// (SENTIMENT, MACRO, etc. may legitimately appear inside tooltip/aria text — the
//  negative scan below targets TEXT_NODE content only.)
const CANONICAL_KEYS_TO_SCAN = [
    'IDIOSYNCRATIC',
    'MACRO_LOCKED',
];

async function loadDashboard(page: Page, viewport = { width: 1440, height: 900 }) {
    await page.setViewportSize(viewport);
    await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20_000 });
    // Allow time for lazy / async renders to settle
    await page.waitForTimeout(1000);
}

test.describe('TermLabel Glossary Coverage', () => {

    test('all [data-glossary-term] elements have tooltip on hover at 1440px', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();

        if (count === 0) {
            // No term labels in the current view — skip gracefully.
            // This happens when no intel data is available to render the cards.
            console.warn('[GLOSSARY AUDIT] No [data-glossary-term] elements found — intel data may not be available; skipping hover tooltip checks.');
            test.skip(true, 'No [data-glossary-term] elements found — intel data may not be available');
            return;
        }

        // Sample up to 5 term labels (avoids DOM-hammering every element)
        const toTest = Math.min(count, 5);
        for (let i = 0; i < toTest; i++) {
            const el = glossaryTerms.nth(i);
            const termKey = await el.getAttribute('data-glossary-term');
            if (!termKey) continue;

            // Hover to trigger the tooltip
            await el.hover();

            // role="tooltip" should appear in the portal
            const tooltip = page.locator('[role="tooltip"]').first();
            await expect(tooltip).toBeVisible({ timeout: 3000 });

            // Tooltip must not be offscreen (generous ±200 px margin for portals near edges)
            const ttBox = await tooltip.boundingBox();
            if (ttBox) {
                const vw = 1440;
                const vh = 900;
                const MARGIN = 200;
                expect(ttBox.x, `Tooltip for "${termKey}" left edge`).toBeGreaterThanOrEqual(-MARGIN);
                expect(ttBox.y, `Tooltip for "${termKey}" top edge`).toBeGreaterThanOrEqual(-MARGIN);
                expect(ttBox.x + ttBox.width, `Tooltip for "${termKey}" right edge`).toBeLessThanOrEqual(vw + MARGIN);
                expect(ttBox.y + ttBox.height, `Tooltip for "${termKey}" bottom edge`).toBeLessThanOrEqual(vh + MARGIN);
            }

            // Move mouse away so the next iteration starts clean
            await page.mouse.move(0, 0);
            await page.waitForTimeout(200);
        }
    });

    test('[data-glossary-term] click opens drill-down popover with short and long text at 1440px', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();
        if (count === 0) {
            console.warn('[GLOSSARY AUDIT] No [data-glossary-term] elements found; skipping popover click check.');
            test.skip(true, 'No [data-glossary-term] elements found');
            return;
        }

        const el = glossaryTerms.first();
        await el.click();

        // Drill-down overlay must appear
        const drillOverlay = page.locator('.term-drill-overlay, [role="dialog"][aria-label^="Glossary"]').first();
        await expect(drillOverlay).toBeVisible({ timeout: 5000 });

        // Short description must be present and non-trivial
        const shortText = page.locator('[data-testid="drill-short"]');
        await expect(shortText).toBeVisible({ timeout: 3000 });
        const shortContent = await shortText.textContent();
        expect(shortContent?.length, 'drill-short text should be non-trivial').toBeGreaterThan(10);

        // Long description must be present and non-trivial
        const longText = page.locator('[data-testid="drill-long"]');
        await expect(longText).toBeVisible({ timeout: 3000 });
        const longContent = await longText.textContent();
        expect(longContent?.length, 'drill-long text should be non-trivial').toBeGreaterThan(20);

        // Close button should dismiss the overlay
        const closeBtn = page.locator('.term-drill-close').first();
        await closeBtn.click();
        await expect(drillOverlay).not.toBeVisible({ timeout: 3000 });
    });

    test('drill-down popover fits viewport at 1280px width', async ({ page }) => {
        await loadDashboard(page, { width: 1280, height: 800 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();
        if (count === 0) {
            console.warn('[GLOSSARY AUDIT] No [data-glossary-term] elements found; skipping 1280px viewport check.');
            test.skip(true, 'No [data-glossary-term] elements found');
            return;
        }

        await glossaryTerms.first().click();

        const drillCard = page.locator('.term-drill-card').first();
        await expect(drillCard).toBeVisible({ timeout: 5000 });

        const box = await drillCard.boundingBox();
        if (box) {
            expect(box.x, 'Drill card left edge at 1280px').toBeGreaterThanOrEqual(-50);
            expect(box.y, 'Drill card top edge at 1280px').toBeGreaterThanOrEqual(-50);
            expect(box.x + box.width, 'Drill card right edge at 1280px').toBeLessThanOrEqual(1280 + 50);
            expect(box.y + box.height, 'Drill card bottom edge at 1280px').toBeLessThanOrEqual(800 + 50);
        }

        // Dismiss — try Escape first, fall back to clicking outside the card
        await page.keyboard.press('Escape');
        const overlay = page.locator('.term-drill-overlay').first();
        if (await overlay.isVisible({ timeout: 1000 }).catch(() => false)) {
            await overlay.click({ position: { x: 10, y: 10 } });
        }
    });

    test('drill-down popover fits viewport at 1920px width', async ({ page }) => {
        await loadDashboard(page, { width: 1920, height: 1080 });

        const glossaryTerms = page.locator('[data-glossary-term]');
        const count = await glossaryTerms.count();
        if (count === 0) {
            console.warn('[GLOSSARY AUDIT] No [data-glossary-term] elements found; skipping 1920px viewport check.');
            test.skip(true, 'No [data-glossary-term] elements found');
            return;
        }

        await glossaryTerms.first().click();

        const drillCard = page.locator('.term-drill-card').first();
        await expect(drillCard).toBeVisible({ timeout: 5000 });

        const box = await drillCard.boundingBox();
        if (box) {
            expect(box.x + box.width, 'Drill card right edge at 1920px').toBeLessThanOrEqual(1920 + 50);
            expect(box.y + box.height, 'Drill card bottom edge at 1920px').toBeLessThanOrEqual(1080 + 50);
        }

        const overlay = page.locator('.term-drill-overlay').first();
        if (await overlay.isVisible({ timeout: 1000 }).catch(() => false)) {
            await overlay.click({ position: { x: 10, y: 10 } });
        }
    });

    test('negative scan: canonical glossary keys are not bare text in DOM (must be inside TermLabel)', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        for (const key of CANONICAL_KEYS_TO_SCAN) {
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
                    if (!text.includes(searchKey)) continue;

                    // Allowed if any ancestor is a glossary-aware element
                    let parent = node.parentElement;
                    let hasGlossaryAncestor = false;
                    while (parent) {
                        if (
                            parent.hasAttribute('data-glossary-term') ||
                            (parent.getAttribute('role') === 'tooltip') ||
                            parent.classList.contains('term-drill-card') ||
                            parent.classList.contains('tooltip-box') ||
                            parent.getAttribute('data-testid')?.startsWith('drill-')
                        ) {
                            hasGlossaryAncestor = true;
                            break;
                        }
                        parent = parent.parentElement;
                    }

                    if (!hasGlossaryAncestor) {
                        results.push(
                            `bare "${searchKey}" in: ${node.parentElement?.outerHTML?.slice(0, 100)}`
                        );
                    }
                }
                return results;
            }, key);

            if (bareOccurrences.length > 0) {
                console.warn(`[GLOSSARY AUDIT] Bare occurrences of "${key}" without TermLabel ancestor:`, bareOccurrences);
            }
            expect(bareOccurrences, `"${key}" should always be wrapped in a TermLabel ([data-glossary-term] ancestor)`).toHaveLength(0);
        }
    });

    test('related term navigation works in drill-down', async ({ page }) => {
        await loadDashboard(page, { width: 1440, height: 900 });

        // This test targets a specific term known to have related-term chips.
        // If it is not visible (no intel data), skip gracefully.
        const termEl = page.locator('[data-glossary-term="CORRELATION_REGIME"]').first();
        const exists = await termEl.isVisible({ timeout: 5000 }).catch(() => false);
        if (!exists) {
            console.warn('[GLOSSARY AUDIT] CORRELATION_REGIME term label not visible (intel data unavailable); skipping related-term navigation check.');
            test.skip(true, 'CORRELATION_REGIME term label not visible — intel data unavailable');
            return;
        }

        await termEl.click();

        const drillOverlay = page.locator('.term-drill-overlay').first();
        await expect(drillOverlay).toBeVisible({ timeout: 5000 });

        // Related term chips are optional — if absent the test still passes
        const relatedChip = page.locator('.term-drill-related-chip').first();
        const chipExists = await relatedChip.isVisible({ timeout: 3000 }).catch(() => false);
        if (!chipExists) return;

        const chipText = await relatedChip.textContent();
        await relatedChip.click();

        // Drill-down title should update to reflect the related term
        const drillTitle = page.locator('.term-drill-title').first();
        await expect(drillTitle).toBeVisible({ timeout: 3000 });
        const newTitle = await drillTitle.textContent();
        expect(newTitle?.trim().length, 'Related term drill title should be non-empty').toBeGreaterThan(0);
        expect(newTitle?.trim(), 'Drill title should match the clicked chip text').toBe(chipText?.trim());
    });
});
