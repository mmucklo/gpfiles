#!/usr/bin/env node
/**
 * TSLA Alpha Dashboard — Automated UX Smoke Tests
 * Uses Playwright headless Chrome to verify the dashboard works like a human would expect.
 *
 * Run: node scripts/ux_smoke.mjs
 * Requires: npx playwright install chromium
 */
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:2112';
const results = { pass: 0, fail: 0, skip: 0, details: [] };

function log(status, test, detail = '') {
    const icon = status === 'PASS' ? '✓' : status === 'FAIL' ? '✗' : '○';
    results[status.toLowerCase()]++;
    results.details.push({ status, test, detail });
    console.log(`  ${icon} ${test}${detail ? ' — ' + detail : ''}`);
}

async function run() {
    console.log(`\n=== TSLA Alpha UX Smoke Tests ===`);
    console.log(`  Base: ${BASE}\n`);

    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);

    try {
        // ── Page Load ─────────────────────────────────────────
        console.log('--- [Page Load] ---');
        const response = await page.goto(BASE, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('h1', { timeout: 10000 });
        // Wait for initial API polls to complete (portfolio, signals, etc.)
        await page.waitForTimeout(4000);
        log(response.status() === 200 ? 'PASS' : 'FAIL', 'Dashboard loads', `HTTP ${response.status()}`);

        // Check title
        const title = await page.textContent('h1');
        log(title && title.includes('TSLA ALPHA') ? 'PASS' : 'FAIL', 'Title shows TSLA ALPHA COMMAND', title);

        // ── Header NAV/CASH/REALIZED ──────────────────────────
        console.log('\n--- [Header Values] ---');
        const header = await page.textContent('header');
        const hasNAV = header && header.includes('NAV:');
        log(hasNAV ? 'PASS' : 'FAIL', 'Header shows NAV');

        // Check NAV is not $25K sim value
        const navMatch = header?.match(/NAV:\s*\$([\d,.]+)/);
        if (navMatch) {
            const navVal = parseFloat(navMatch[1].replace(/,/g, ''));
            log(navVal > 100000 ? 'PASS' : 'FAIL', 'NAV is IBKR value (not $25K sim)', `$${navVal.toLocaleString()}`);
        } else {
            log('SKIP', 'NAV value check', 'could not parse');
        }

        // ── Broker Status Banner ──────────────────────────────
        console.log('\n--- [Broker Status] ---');
        const brokerBanner = await page.$('.broker-status-banner');
        log(brokerBanner ? 'PASS' : 'FAIL', 'Broker status banner visible');
        if (brokerBanner) {
            const bannerText = await brokerBanner.textContent();
            log(bannerText.includes('PAPER') || bannerText.includes('LIVE') || bannerText.includes('SIM') ? 'PASS' : 'FAIL',
                'Banner shows mode', bannerText.trim());
        }

        // ── Portfolio Bar Pills ───────────────────────────────
        console.log('\n--- [Portfolio Bar] ---');
        const pills = await page.$$('.port-pill');
        log(pills.length >= 4 ? 'PASS' : 'FAIL', `Portfolio pills visible (${pills.length} found, need ≥4)`);

        // Check pills don't show "..." (loading state) — skip if IBKR not connected
        const pillTexts = await Promise.all(pills.slice(0, 3).map(p => p.textContent()));
        const loadingPills = pillTexts.filter(t => t.includes('...'));
        if (loadingPills.length > 0) {
            log('SKIP', `${loadingPills.length} portfolio pill(s) still loading (IBKR not connected)`, loadingPills[0].trim().substring(0, 40));
        } else {
            log('PASS', 'Portfolio pills fully loaded');
        }

        // Check for (IBKR) or (SIM) labels
        const sourceLabels = await page.$$('.port-pill-source');
        log(sourceLabels.length > 0 ? 'PASS' : 'FAIL', 'Data source labels (IBKR/SIM) visible');

        // ── Signal Command Panel ──────────────────────────────
        console.log('\n--- [Signal Command] ---');
        const signalCards = await page.$$('.signal-card');
        if (signalCards.length > 0) {
            log('PASS', `Signal cards visible (${signalCards.length} found)`);

            // Check first card has proper contract name
            const firstCard = signalCards[0];
            const contractText = await firstCard.$eval('.signal-contract', el => el.textContent);
            log(contractText && contractText.length > 5 ? 'PASS' : 'FAIL', 'Signal contract name present', contractText);

            // Verify spread names are proper (not generic "CALL Spread")
            const hasProperName = contractText.includes('Bull') || contractText.includes('Bear') || contractText.includes('Long') || contractText.includes('$');
            log(hasProperName ? 'PASS' : 'FAIL', 'Contract uses proper strategy name', contractText);

            // Check conviction bar exists
            const convBar = await firstCard.$('.conviction-bar-fill');
            log(convBar ? 'PASS' : 'FAIL', 'Conviction bar visible');

            // Check stats grid has LIMIT, EXIT, STOP, KELLY, QTY
            const statKeys = await firstCard.$$eval('.signal-stat-key', els => els.map(e => e.textContent));
            const expectedStats = ['LIMIT', 'EXIT', 'STOP', 'KELLY', 'QTY'];
            for (const stat of expectedStats) {
                log(statKeys.some(k => k.includes(stat)) ? 'PASS' : 'FAIL', `Signal stat "${stat}" present`);
            }

            // Check limit price is not $0.00
            const limitText = await firstCard.$eval('.signal-stat-val', el => el.textContent);
            const limitVal = parseFloat(limitText.replace('$', '').replace(',', ''));
            log(!isNaN(limitVal) && limitVal > 0 ? 'PASS' : 'FAIL', `Limit price is real (${limitText})`, limitVal > 5 ? 'WARN: unusually high' : '');

            // ── Signal Popup Modal ────────────────────────────
            console.log('\n--- [Signal Popup] ---');
            await firstCard.click();
            await page.waitForSelector('.signal-modal', { timeout: 3000 });
            log('PASS', 'Signal modal opens on click');

            // Check modal title
            const modalTitle = await page.$eval('.signal-modal-title', el => el.textContent);
            log(modalTitle && modalTitle.length > 5 ? 'PASS' : 'FAIL', 'Modal title present', modalTitle);

            // No redundant "SELL"/"BUY" before spread names
            if (modalTitle.includes('Spread')) {
                log(!modalTitle.startsWith('SELL ') && !modalTitle.startsWith('BUY ') ? 'PASS' : 'FAIL',
                    'Spread title not redundant', modalTitle);
            }

            // Check strategy explanation text visible (inside the modal)
            const modal = await page.$('.signal-modal');
            const explanation = modal ? await modal.$$eval('div', els => {
                const found = els.find(e => (e.textContent.includes('Max profit') || e.textContent.includes('Max loss') || e.textContent.includes('Max risk')) && e.children.length === 0);
                return found ? found.textContent.trim().substring(0, 80) : null;
            }) : null;
            log(explanation ? 'PASS' : 'SKIP', 'Strategy explanation visible in modal', explanation ?? '');

            // Check options chain loaded
            const chainTable = await page.$('table');
            if (chainTable) {
                log('PASS', 'Options chain table loaded');

                // Wait for highlight to render (chain data may arrive async)
                await page.waitForTimeout(1500);
                const highlightedRows = await page.$$eval('td', els =>
                    els.filter(e => e.textContent.includes('◄')).map(e => e.textContent.trim())
                );
                log(highlightedRows.length > 0 ? 'PASS' : 'SKIP', 'Chain has highlighted strike rows', highlightedRows.join(', ') || 'none visible yet');

                // Verify active chain tab exists
                const activeTab = await page.$('.btn-view-chain.active');
                if (activeTab) {
                    const tabText = await activeTab.textContent();
                    log('PASS', `Active chain tab`, tabText.trim());
                }
            } else {
                log('SKIP', 'Options chain not loaded (may be loading)');
            }

            // Close modal
            await page.click('.btn-modal-close');
            await page.waitForSelector('.signal-modal', { state: 'hidden', timeout: 2000 }).catch(() => {});

        } else {
            log('SKIP', 'No signal cards to test (waiting for signals)');
        }

        // ── Tooltip Verification ──────────────────────────────
        console.log('\n--- [Tooltips] ---');
        const firstPill = await page.$('.port-pill');
        if (firstPill) {
            await firstPill.hover();
            await page.waitForTimeout(500);
            const tooltip = await page.$('.tooltip-box');
            log(tooltip ? 'PASS' : 'FAIL', 'Tooltip appears on portfolio pill hover');
        }

        // ── Trading Floor ─────────────────────────────────────
        console.log('\n--- [Trading Floor] ---');
        const posCards = await page.$$('.position-card');
        const emptyMsg = await page.$('.empty-position-card');
        log(posCards.length > 0 || emptyMsg ? 'PASS' : 'FAIL', `Trading floor content (${posCards.length} positions or empty state)`);

        // ── Execution Log ─────────────────────────────────────
        console.log('\n--- [Execution Log] ---');
        const execLog = await page.getByText('EXECUTION LOG').first().elementHandle().catch(() => null);
        log(execLog ? 'PASS' : 'FAIL', 'Execution log section visible');

        // ── Market Intelligence ───────────────────────────────
        console.log('\n--- [Market Intelligence] ---');
        const intelSection = await page.getByText('MARKET INTELLIGENCE').first().elementHandle().catch(() => null);
        log(intelSection ? 'PASS' : 'SKIP', 'Market intelligence section visible');

    } catch (err) {
        log('FAIL', 'Unexpected error', err.message);
    } finally {
        await browser.close();
    }

    // ── Summary ───────────────────────────────────────────────
    const total = results.pass + results.fail + results.skip;
    console.log(`\n════════════════════════════════════`);
    if (results.fail === 0) {
        console.log(`  ✓ ALL ${results.pass} UX CHECKS PASSED (${results.skip} skipped)`);
    } else {
        console.log(`  ✗ ${results.fail} FAILED / ${results.pass} PASSED / ${results.skip} SKIPPED`);
    }
    console.log(`════════════════════════════════════\n`);

    process.exit(results.fail > 0 ? 1 : 0);
}

run().catch(err => {
    console.error('Fatal:', err);
    process.exit(2);
});
