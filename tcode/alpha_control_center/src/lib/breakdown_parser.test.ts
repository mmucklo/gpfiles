/**
 * Unit tests for breakdown_parser.ts
 * Regression guard for Phase 14.2: React error #31 caused by rendering an
 * attribution object as a React child via the old `as number` cast.
 *
 * Run with: npx vitest run src/lib/breakdown_parser.test.ts
 */

import { describe, it, expect } from 'vitest';
import { parseBreakdownByModel } from './breakdown_parser';

// ── new Phase-14 shape ─────────────────────────────────────────────────────────

const NEW_SHAPE = {
    attribution: {
        by_model: {
            '30d': {
                SENTIMENT: { count: 42, avg_confidence: 0.78, avg_selection_score: 0.85 },
                MACRO: { count: 17, avg_confidence: 0.65, avg_selection_score: null },
                CONTRARIAN: { count: 5, avg_confidence: 0.55, avg_selection_score: 0.60 },
            },
            '60d': {
                SENTIMENT: { count: 90, avg_confidence: 0.76, avg_selection_score: 0.84 },
            },
        },
        by_chop_regime: {},
        by_score_bin: {},
        windows: ['30d', '60d'],
        generated_at: '2026-04-14T10:00:00Z',
        note: 'rolling attribution',
    },
};

// ── old pre-Phase-14 shape ────────────────────────────────────────────────────

const OLD_SHAPE = { SENTIMENT: 123, MACRO: 45 };

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('parseBreakdownByModel — new Phase-14 shape', () => {
    it('returns per-model counts for the default 30d window', () => {
        const result = parseBreakdownByModel(NEW_SHAPE);
        expect(result).toEqual({ SENTIMENT: 42, MACRO: 17, CONTRARIAN: 5 });
    });

    it('every value in the result is a number', () => {
        const result = parseBreakdownByModel(NEW_SHAPE);
        for (const v of Object.values(result)) {
            expect(typeof v).toBe('number');
        }
    });

    it('returns correct counts for an explicit 60d window', () => {
        const result = parseBreakdownByModel(NEW_SHAPE, '60d');
        expect(result).toEqual({ SENTIMENT: 90 });
    });
});

describe('parseBreakdownByModel — guard cases (must never crash, never return objects)', () => {
    it('returns {} for null', () => {
        expect(parseBreakdownByModel(null)).toEqual({});
    });

    it('returns {} for undefined', () => {
        expect(parseBreakdownByModel(undefined)).toEqual({});
    });

    it('returns {} for empty object {}', () => {
        expect(parseBreakdownByModel({})).toEqual({});
    });

    it('returns {} for old flat shape {SENTIMENT: 123, MACRO: 45} (no attribution key)', () => {
        // Old shape has no `attribution` key → parseBreakdownByModel returns {}
        // Caller renders "Signals 30d: 0" fallback card — no crash, no [object Object]
        const result = parseBreakdownByModel(OLD_SHAPE);
        expect(result).toEqual({});
    });

    it('returns {} when attribution exists but by_model is missing', () => {
        const partial = { attribution: { by_score_bin: {}, generated_at: '...' } };
        const result = parseBreakdownByModel(partial);
        expect(result).toEqual({});
    });

    it('returns {} when by_model exists but requested window is missing', () => {
        const partial = { attribution: { by_model: { '60d': { SENTIMENT: { count: 10 } } } } };
        const result = parseBreakdownByModel(partial);  // defaults to '30d'
        expect(result).toEqual({});
    });

    it('handles missing count field gracefully — returns 0 for that model', () => {
        const noCount = {
            attribution: {
                by_model: {
                    '30d': {
                        SENTIMENT: { avg_confidence: 0.78 },  // no count
                        MACRO: { count: 5 },
                    },
                },
            },
        };
        const result = parseBreakdownByModel(noCount);
        expect(result['SENTIMENT']).toBe(0);
        expect(result['MACRO']).toBe(5);
    });

    it('never returns a non-number value for any key', () => {
        // Feed it a malformed shape where count is a string or object
        const malformed = {
            attribution: {
                by_model: {
                    '30d': {
                        MODEL_A: { count: '42' as unknown as number },   // string — should coerce to 0
                        MODEL_B: { count: { nested: true } as unknown as number },  // object — should be 0
                    },
                },
            },
        };
        const result = parseBreakdownByModel(malformed);
        for (const v of Object.values(result)) {
            expect(typeof v).toBe('number');
        }
    });
});
