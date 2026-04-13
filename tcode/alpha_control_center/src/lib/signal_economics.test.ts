/**
 * Unit tests for signal_economics.ts
 * Run with: npx vitest run src/lib/signal_economics.test.ts
 */

import { describe, it, expect } from 'vitest';
import {
    computeEconomics,
    computeLegCommission,
    computeRoundTripCommission,
    formatRR,
    rrColorClass,
    IBKR_OPTION_FEE_PER_CONTRACT,
    IBKR_OPTION_MIN_PER_LEG,
} from './signal_economics';

const DELTA = 0.01; // $0.01 tolerance

// ============================================================
//  Commission helpers
// ============================================================
describe('computeLegCommission', () => {
    it('uses per-contract fee when above minimum', () => {
        // 10 contracts × $0.65 = $6.50 > $1.00 minimum
        expect(computeLegCommission(10)).toBeCloseTo(6.50, 2);
    });

    it('applies minimum per leg when qty is 1', () => {
        // 1 contract × $0.65 = $0.65 < $1.00 minimum → $1.00
        expect(computeLegCommission(1)).toBeCloseTo(1.00, 2);
    });

    it('applies minimum per leg when qty is 0', () => {
        expect(computeLegCommission(0)).toBeCloseTo(1.00, 2);
    });
});

describe('computeRoundTripCommission', () => {
    it('2-leg round trip for 10 contracts', () => {
        // 2 × max($0.65×10, $1.00) = 2 × $6.50 = $13.00
        expect(computeRoundTripCommission(10, 2)).toBeCloseTo(13.00, 2);
    });

    it('4-leg round trip for spread with 5 contracts', () => {
        // 4 × max($0.65×5, $1.00) = 4 × $3.25 = $13.00
        expect(computeRoundTripCommission(5, 4)).toBeCloseTo(13.00, 2);
    });

    it('4-leg round trip with 1 contract applies minimum per leg', () => {
        // 4 × max($0.65×1, $1.00) = 4 × $1.00 = $4.00
        expect(computeRoundTripCommission(1, 4)).toBeCloseTo(4.00, 2);
    });
});

// ============================================================
//  Long call (primary example from spec)
// ============================================================
describe('Long call — MACRO BULLISH CALL $365 @ $0.28 entry, 10 contracts', () => {
    const signal = {
        action: 'BUY',
        is_spread: false,
        option_type: 'CALL',
        target_limit_price: 0.28,
        take_profit_price: 0.36,
        stop_loss_price: 0.20,
        quantity: 10,
        recommended_strike: 365,
    };
    const eco = computeEconomics(signal);

    it('cost basis = $286.50', () => {
        // $0.28 × 10 × 100 + $6.50 = $280 + $6.50 = $286.50
        expect(eco.cost_basis).toBeCloseTo(286.50, DELTA);
    });

    it('open commission = $6.50', () => {
        expect(eco.commission_open).toBeCloseTo(6.50, 2);
    });

    it('round-trip commission = $13.00', () => {
        expect(eco.round_trip_commission).toBeCloseTo(13.00, 2);
    });

    it('max profit at TP = $67.00', () => {
        // ($0.36 - $0.28) × 10 × 100 - $13 = $80 - $13 = $67
        expect(eco.max_profit_at_tp).toBeCloseTo(67.00, DELTA);
    });

    it('max loss at SL = $93.00', () => {
        // ($0.28 - $0.20) × 10 × 100 + $13 = $80 + $13 = $93
        expect(eco.max_loss_at_sl).toBeCloseTo(93.00, DELTA);
    });

    it('breakeven = $0.293', () => {
        // $0.28 + ($13 / 10 / 100) = $0.28 + $0.013 = $0.293
        expect(eco.breakeven).toBeCloseTo(0.293, 3);
    });

    it('R:R = 0.72 (67/93)', () => {
        expect(eco.risk_reward_ratio).toBeCloseTo(67 / 93, 3);
    });

    it('return on cost basis at TP ≈ 23.4%', () => {
        expect(eco.return_on_cost_basis_tp).toBeCloseTo(67 / 286.50, 3);
    });

    it('return on cost basis at SL ≈ -32.5%', () => {
        expect(eco.return_on_cost_basis_sl).toBeCloseTo(-93 / 286.50, 3);
    });

    it('theoretical max profit is unlimited for calls', () => {
        expect(eco.theoretical_max_profit).toBe('unlimited');
    });

    it('is_na is false', () => {
        expect(eco.is_na).toBe(false);
    });
});

// ============================================================
//  Long call — small quantity (min-per-leg floor)
// ============================================================
describe('Long call — 1 contract (minimum commission floor)', () => {
    const signal = {
        action: 'BUY',
        is_spread: false,
        option_type: 'CALL',
        target_limit_price: 1.00,
        take_profit_price: 1.50,
        stop_loss_price: 0.70,
        quantity: 1,
        recommended_strike: 400,
    };
    const eco = computeEconomics(signal);

    it('commission_open = $1.00 (minimum floor)', () => {
        // 1 × $0.65 = $0.65 < $1.00 → $1.00
        expect(eco.commission_open).toBeCloseTo(1.00, 2);
    });

    it('round_trip_commission = $2.00', () => {
        expect(eco.round_trip_commission).toBeCloseTo(2.00, 2);
    });

    it('max profit at TP = $48.00', () => {
        // ($1.50 - $1.00) × 1 × 100 - $2 = $50 - $2 = $48
        expect(eco.max_profit_at_tp).toBeCloseTo(48.00, DELTA);
    });

    it('max loss at SL = $32.00', () => {
        // ($1.00 - $0.70) × 1 × 100 + $2 = $30 + $2 = $32
        expect(eco.max_loss_at_sl).toBeCloseTo(32.00, DELTA);
    });
});

// ============================================================
//  Long put
// ============================================================
describe('Long put — BUY PUT, 5 contracts', () => {
    const signal = {
        action: 'BUY',
        is_spread: false,
        option_type: 'PUT',
        target_limit_price: 2.00,
        take_profit_price: 3.00,
        stop_loss_price: 1.40,
        quantity: 5,
        recommended_strike: 350,
    };
    const eco = computeEconomics(signal);

    it('theoretical max profit bounded by strike going to 0', () => {
        // strike × 100 × qty - cost_basis
        // cost_basis = 2.00 × 100 × 5 + max(0.65×5, 1.00) = $1000 + $3.25 = $1003.25
        const expected_cost = 2.00 * 100 * 5 + Math.max(0.65 * 5, 1.00);
        expect(eco.theoretical_max_profit).toBeCloseTo(350 * 100 * 5 - expected_cost, 0);
    });

    it('theoretical max profit is a number (not unlimited) for puts', () => {
        expect(typeof eco.theoretical_max_profit).toBe('number');
    });
});

// ============================================================
//  Bull put spread (credit spread)
// ============================================================
describe('Bull put spread — credit, 10 contracts, short=$360, long=$355', () => {
    const signal = {
        action: 'SELL',
        is_spread: true,
        option_type: 'PUT',
        target_limit_price: 1.00,  // net credit received
        take_profit_price: 0,
        stop_loss_price: 0,
        quantity: 10,
        short_strike: 360,
        long_strike: 355,
    };
    const eco = computeEconomics(signal);

    it('is credit spread', () => {
        expect(eco.spread_type).toBe('credit');
    });

    it('round_trip commission = $26.00 (4 legs × $6.50)', () => {
        expect(eco.round_trip_commission).toBeCloseTo(26.00, 2);
    });

    it('max profit = credit - commission', () => {
        // $1.00 × 10 × 100 - $26 = $1000 - $26 = $974
        expect(eco.max_profit_at_tp).toBeCloseTo(974.00, DELTA);
    });

    it('max loss = (width - credit) × qty × 100 + commission', () => {
        // ($5 - $1) × 10 × 100 + $26 = $4000 + $26 = $4026
        expect(eco.max_loss_at_sl).toBeCloseTo(4026.00, DELTA);
    });

    it('is_na = false', () => {
        expect(eco.is_na).toBe(false);
    });
});

// ============================================================
//  Bull call spread (debit spread)
// ============================================================
describe('Bull call spread — debit, 5 contracts, long=$360, short=$370', () => {
    const signal = {
        action: 'BUY',
        is_spread: true,
        option_type: 'CALL',
        target_limit_price: 3.00,  // net debit paid
        take_profit_price: 0,
        stop_loss_price: 0,
        quantity: 5,
        long_strike: 360,
        short_strike: 370,
    };
    const eco = computeEconomics(signal);

    it('is debit spread', () => {
        expect(eco.spread_type).toBe('debit');
    });

    it('max profit = (width - debit) × qty × 100 - commission', () => {
        // ($10 - $3) × 5 × 100 - comm_rt
        // comm_rt = 4 × max(0.65×5, 1) = 4 × 3.25 = $13
        // = $3500 - $13 = $3487
        expect(eco.max_profit_at_tp).toBeCloseTo(3487.00, DELTA);
    });

    it('max loss = debit × qty × 100 + commission', () => {
        // $3 × 5 × 100 + $13 = $1500 + $13 = $1513
        expect(eco.max_loss_at_sl).toBeCloseTo(1513.00, DELTA);
    });
});

// ============================================================
//  Complex spread — missing strike data
// ============================================================
describe('Complex spread — missing strike data returns N/A', () => {
    const signal = {
        action: 'BUY',
        is_spread: true,
        option_type: 'CALL',
        target_limit_price: 2.00,
        take_profit_price: 3.00,
        stop_loss_price: 1.00,
        quantity: 5,
        short_strike: 0,
        long_strike: 0,
    };
    const eco = computeEconomics(signal);

    it('is_na = true', () => {
        expect(eco.is_na).toBe(true);
    });

    it('na_reason is set', () => {
        expect(eco.na_reason).toBeTruthy();
    });
});

// ============================================================
//  Display helpers
// ============================================================
describe('formatRR', () => {
    it('formats 0.72 as "1 : 0.72"', () => {
        expect(formatRR(67 / 93)).toBe('1 : 0.72');
    });

    it('formats 1.5 as "1 : 1.50"', () => {
        expect(formatRR(1.5)).toBe('1 : 1.50');
    });
});

describe('rrColorClass', () => {
    it('returns green for ratio >= 1', () => {
        expect(rrColorClass(1.0)).toBe('green');
        expect(rrColorClass(2.0)).toBe('green');
    });

    it('returns amber for 0.5–1', () => {
        expect(rrColorClass(0.72)).toBe('amber');
        expect(rrColorClass(0.5)).toBe('amber');
    });

    it('returns red for < 0.5', () => {
        expect(rrColorClass(0.3)).toBe('red');
        expect(rrColorClass(0)).toBe('red');
    });
});
