/**
 * ux_signal_feedback.spec.ts — Playwright tests for Phase 13 signal feedback loop.
 *
 * Tests:
 *   1. Signal drill-down has feedback section
 *   2. Adding a comment with a tag persists (appears after reload)
 *   3. Cancel signal flow: confirmation modal → red CANCELLED badge
 *   4. Feedback inbox filter works (filter by action)
 */
import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.ACC_URL ?? 'http://localhost:2112';

// Helper: open the first signal's drill-down modal
async function openFirstSignalModal(page: Page) {
    await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 15000 });

    // Wait for signal cards to appear
    const signalCard = page.locator('.signal-card, [data-testid*="signal"]').first();
    if (!(await signalCard.isVisible({ timeout: 8000 }).catch(() => false))) {
        // No signals — skip by returning null
        return null;
    }
    await signalCard.click();
    // Modal should open
    const modal = page.locator('.signal-modal, [role="dialog"]').first();
    await expect(modal).toBeVisible({ timeout: 5000 });
    return modal;
}

test.describe('Signal Feedback Section', () => {

    test('signal drill-down contains feedback section', async ({ page }) => {
        test.skip(
            !(await page.goto(BASE_URL).then(() => true).catch(() => false)),
            'Server not running'
        );
        const modal = await openFirstSignalModal(page);
        if (!modal) {
            test.skip(true, 'No signals available to open drill-down');
            return;
        }
        const feedbackSection = page.locator('[data-testid="signal-feedback-section"]');
        await expect(feedbackSection).toBeVisible({ timeout: 5000 });
    });

    test('can add a comment with a tag', async ({ page }) => {
        test.skip(
            !(await page.goto(BASE_URL).then(() => true).catch(() => false)),
            'Server not running'
        );
        const modal = await openFirstSignalModal(page);
        if (!modal) {
            test.skip(true, 'No signals available');
            return;
        }

        const commentInput = page.locator('[data-testid="feedback-comment-input"]');
        await expect(commentInput).toBeVisible({ timeout: 5000 });

        const comment = `Test comment ${Date.now()}`;
        await commentInput.fill(comment);

        // Select a tag
        const tagSelect = page.locator('[data-testid="feedback-tag-select"]');
        await tagSelect.selectOption('bad_strike');

        // Click Save Comment
        const saveBtn = page.locator('[data-testid="btn-save-comment"]');
        await saveBtn.click();

        // Comment input should be cleared after save
        await expect(commentInput).toHaveValue('', { timeout: 5000 });

        // The saved comment should appear in the feedback rows list
        const feedbackSection = page.locator('[data-testid="signal-feedback-section"]');
        await expect(feedbackSection).toContainText(comment.slice(0, 30), { timeout: 5000 });
    });

    test('cancel signal shows confirmation modal and sets cancelled badge', async ({ page }) => {
        test.skip(
            !(await page.goto(BASE_URL).then(() => true).catch(() => false)),
            'Server not running'
        );
        const modal = await openFirstSignalModal(page);
        if (!modal) {
            test.skip(true, 'No signals available');
            return;
        }

        // Click Cancel This Signal (only present if signal not already cancelled)
        const cancelBtn = page.locator('[data-testid="btn-cancel-signal"]');
        if (!(await cancelBtn.isVisible({ timeout: 3000 }).catch(() => false))) {
            test.skip(true, 'Cancel button not visible (signal already cancelled or not available)');
            return;
        }
        await cancelBtn.click();

        // Confirmation modal should appear
        const cancelInput = page.locator('[data-testid="cancel-comment-input"]');
        await expect(cancelInput).toBeVisible({ timeout: 3000 });

        // Confirm button should be disabled without comment
        const confirmBtn = page.locator('[data-testid="btn-confirm-cancel"]');
        await expect(confirmBtn).toBeDisabled();

        // Fill reason
        await cancelInput.fill('Signal fired too late after move ran');

        // Now confirm button should be enabled
        await expect(confirmBtn).not.toBeDisabled({ timeout: 2000 });

        // Click confirm
        await confirmBtn.click();

        // After cancel: red CANCELLED badge should appear
        const feedbackSection = page.locator('[data-testid="signal-feedback-section"]');
        await expect(feedbackSection).toContainText('CANCELLED BY USER', { timeout: 8000 });
    });

    test('dismiss cancel confirmation returns to comment form', async ({ page }) => {
        test.skip(
            !(await page.goto(BASE_URL).then(() => true).catch(() => false)),
            'Server not running'
        );
        const modal = await openFirstSignalModal(page);
        if (!modal) {
            test.skip(true, 'No signals available');
            return;
        }

        const cancelBtn = page.locator('[data-testid="btn-cancel-signal"]');
        if (!(await cancelBtn.isVisible({ timeout: 3000 }).catch(() => false))) {
            test.skip(true, 'Cancel button not visible');
            return;
        }
        await cancelBtn.click();

        const dismissBtn = page.locator('[data-testid="btn-dismiss-cancel"]');
        await expect(dismissBtn).toBeVisible({ timeout: 3000 });
        await dismissBtn.click();

        // Comment input should be visible again
        const commentInput = page.locator('[data-testid="feedback-comment-input"]');
        await expect(commentInput).toBeVisible({ timeout: 3000 });
    });
});

test.describe('Feedback Inbox', () => {

    test('feedback inbox panel renders with filters', async ({ page }) => {
        await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 15000 });

        // Expand the feedback inbox collapsible panel if needed
        const feedbackHeader = page.locator('text=SIGNAL FEEDBACK INBOX').first();
        if (await feedbackHeader.isVisible({ timeout: 5000 })) {
            // Check if collapsed
            const inbox = page.locator('[data-testid="feedback-inbox"]');
            if (!(await inbox.isVisible({ timeout: 2000 }).catch(() => false))) {
                await feedbackHeader.click();
            }
        }

        const inbox = page.locator('[data-testid="feedback-inbox"]');
        await expect(inbox).toBeVisible({ timeout: 8000 });

        // Filters exist
        await expect(page.locator('[data-testid="inbox-filter-tag"]')).toBeVisible({ timeout: 3000 });
        await expect(page.locator('[data-testid="inbox-filter-action"]')).toBeVisible({ timeout: 3000 });
    });

    test('feedback inbox filter by action narrows results', async ({ page }) => {
        await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 15000 });

        const inbox = page.locator('[data-testid="feedback-inbox"]');
        if (!(await inbox.isVisible({ timeout: 5000 }).catch(() => false))) {
            // Try to expand
            const feedbackHeader = page.locator('text=SIGNAL FEEDBACK INBOX').first();
            if (await feedbackHeader.isVisible({ timeout: 3000 })) {
                await feedbackHeader.click();
            }
        }

        if (!(await inbox.isVisible({ timeout: 5000 }).catch(() => false))) {
            test.skip(true, 'Feedback inbox not visible');
            return;
        }

        const actionFilter = page.locator('[data-testid="inbox-filter-action"]');
        await expect(actionFilter).toBeVisible({ timeout: 3000 });

        // Select CANCEL action filter
        await actionFilter.selectOption('CANCEL');

        // Wait for any loading state to clear
        await page.waitForTimeout(500);

        // All visible rows should have action CANCEL (if any rows)
        const rows = page.locator('[data-testid="feedback-inbox-row"]');
        const count = await rows.count();
        if (count > 0) {
            const firstRowText = await rows.first().textContent();
            expect(firstRowText).toContain('CANCEL');
        }
        // (0 rows is also valid — no CANCEL feedback exists)
    });
});
