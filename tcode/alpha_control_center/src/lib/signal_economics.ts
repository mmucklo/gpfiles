/**
 * signal_economics.ts — Trade economics calculator for options signals.
 *
 * Commission model: IBKR Pro tiered
 *   $0.65 per contract, minimum $1.00 per leg
 *   2 legs (open + close) for long positions
 *   4 legs for spreads
 */

// ============================================================
//  Constants (IBKR Pro)
// ============================================================
export const IBKR_OPTION_FEE_PER_CONTRACT = 0.65;
export const IBKR_OPTION_MIN_PER_LEG = 1.00;
const OPTIONS_MULTIPLIER = 100;

// ============================================================
//  Types
// ============================================================
export interface SignalInput {
    action: string;                   // 'BUY' | 'SELL'
    is_spread: boolean;
    option_type: string;              // 'CALL' | 'PUT'
    target_limit_price: number;       // entry price per share
    take_profit_price: number;        // TP price per share
    stop_loss_price: number;          // SL price per share
    quantity: number;                 // number of contracts
    short_strike?: number;
    long_strike?: number;
    recommended_strike?: number;
    direction?: string;
}

export interface SignalEconomics {
    cost_basis: number;
    commission_open: number;
    commission_close: number;
    round_trip_commission: number;
    gross_profit_at_tp: number;
    gross_loss_at_sl: number;
    max_profit_at_tp: number;
    max_loss_at_sl: number;
    breakeven: number;
    risk_reward_ratio: number;           // max_profit_at_tp / max_loss_at_sl
    return_on_cost_basis_tp: number;     // fraction (0.234 = 23.4%)
    return_on_cost_basis_sl: number;     // negative fraction
    theoretical_max_profit: number | 'unlimited';
    spread_type?: 'credit' | 'debit' | 'complex';
    is_na: boolean;                      // true when we cannot compute (complex spread)
    na_reason?: string;
}

// ============================================================
//  Commission helpers
// ============================================================
export function computeLegCommission(qty: number): number {
    return Math.max(IBKR_OPTION_FEE_PER_CONTRACT * qty, IBKR_OPTION_MIN_PER_LEG);
}

export function computeRoundTripCommission(qty: number, legs: 2 | 4 = 2): number {
    const perLeg = computeLegCommission(qty);
    return perLeg * legs;
}

// ============================================================
//  Main computation
// ============================================================
export function computeEconomics(signal: SignalInput): SignalEconomics {
    const qty = signal.quantity;
    const entry = signal.target_limit_price;
    const tp = signal.take_profit_price;
    const sl = signal.stop_loss_price;
    const mult = OPTIONS_MULTIPLIER;

    if (signal.is_spread) {
        return computeSpreadEconomics(signal);
    }

    // Long-only (action === 'BUY')
    if (signal.action === 'BUY') {
        const commission_open = computeLegCommission(qty);
        const commission_close = computeLegCommission(qty);
        const round_trip_commission = commission_open + commission_close;

        const cost_basis = entry * mult * qty + commission_open;

        const gross_profit_at_tp = (tp - entry) * mult * qty;
        const gross_loss_at_sl = (entry - sl) * mult * qty;

        const max_profit_at_tp = gross_profit_at_tp - round_trip_commission;
        const max_loss_at_sl = gross_loss_at_sl + round_trip_commission;

        const breakeven = entry + (round_trip_commission / qty / mult);

        const risk_reward_ratio = max_loss_at_sl > 0
            ? max_profit_at_tp / max_loss_at_sl
            : 0;

        const return_on_cost_basis_tp = cost_basis > 0 ? max_profit_at_tp / cost_basis : 0;
        const return_on_cost_basis_sl = cost_basis > 0 ? -max_loss_at_sl / cost_basis : 0;

        let theoretical_max_profit: number | 'unlimited';
        if (signal.option_type === 'CALL') {
            theoretical_max_profit = 'unlimited';
        } else {
            // Long put: max profit if underlying goes to 0
            const strike = signal.recommended_strike ?? 0;
            theoretical_max_profit = strike * mult * qty - cost_basis;
        }

        return {
            cost_basis,
            commission_open,
            commission_close,
            round_trip_commission,
            gross_profit_at_tp,
            gross_loss_at_sl,
            max_profit_at_tp,
            max_loss_at_sl,
            breakeven,
            risk_reward_ratio,
            return_on_cost_basis_tp,
            return_on_cost_basis_sl,
            theoretical_max_profit,
            is_na: false,
        };
    }

    // Short options (SELL) — simplified: max profit = premium, max loss = unlimited/strike-bounded
    const commission_open = computeLegCommission(qty);
    const commission_close = computeLegCommission(qty);
    const round_trip_commission = commission_open + commission_close;

    const max_profit_at_tp = entry * mult * qty - round_trip_commission;
    const cost_basis = 0; // credit received

    const gross_profit_at_tp = entry * mult * qty;
    const gross_loss_at_sl = (sl - entry) * mult * qty; // loss from price moving against us

    const max_loss_at_sl = Math.abs(gross_loss_at_sl) + round_trip_commission;
    const breakeven = entry - (round_trip_commission / qty / mult);
    const risk_reward_ratio = max_loss_at_sl > 0 ? max_profit_at_tp / max_loss_at_sl : 0;

    const theoretical_max_profit: number | 'unlimited' =
        signal.option_type === 'CALL' ? 'unlimited' : entry * mult * qty - round_trip_commission;

    return {
        cost_basis,
        commission_open,
        commission_close,
        round_trip_commission,
        gross_profit_at_tp,
        gross_loss_at_sl,
        max_profit_at_tp,
        max_loss_at_sl,
        breakeven,
        risk_reward_ratio,
        return_on_cost_basis_tp: 0,
        return_on_cost_basis_sl: 0,
        theoretical_max_profit,
        is_na: false,
    };
}

function computeSpreadEconomics(signal: SignalInput): SignalEconomics {
    const qty = signal.quantity;
    const entry = signal.target_limit_price; // net debit or net credit per share
    const mult = OPTIONS_MULTIPLIER;

    const shortStrike = signal.short_strike ?? 0;
    const longStrike = signal.long_strike ?? 0;

    // Only handle simple 2-leg spreads
    if (shortStrike <= 0 || longStrike <= 0) {
        return naEconomics('Complex spread: missing strike data');
    }

    // 4 legs (open short + open long + close short + close long)
    const commission_open = computeLegCommission(qty) * 2;  // two legs to open
    const commission_close = computeLegCommission(qty) * 2; // two legs to close
    const round_trip_commission = commission_open + commission_close;

    const width = Math.abs(shortStrike - longStrike);

    // Determine spread type
    // Convention: short_strike = sold leg, long_strike = bought leg
    let spreadKind: 'credit' | 'debit';
    if (signal.option_type === 'PUT') {
        // Bull put spread: sell higher put (short_strike > long_strike) → credit received
        // Bear put spread: buy higher put (long_strike > short_strike) → debit paid
        spreadKind = shortStrike > longStrike ? 'credit' : 'debit';
    } else {
        // Bull call spread: buy lower call (long_strike < short_strike) → debit paid
        // Bear call spread: sell lower call (short_strike < long_strike) → credit received
        spreadKind = longStrike < shortStrike ? 'debit' : 'credit';
    }

    let max_profit_at_tp: number;
    let max_loss_at_sl: number;
    let cost_basis: number;

    if (spreadKind === 'credit') {
        // Received credit = entry per share × qty × mult
        max_profit_at_tp = entry * mult * qty - round_trip_commission;
        max_loss_at_sl = (width - entry) * mult * qty + round_trip_commission;
        cost_basis = 0;
    } else {
        // Paid debit = entry per share × qty × mult
        max_profit_at_tp = (width - entry) * mult * qty - round_trip_commission;
        max_loss_at_sl = entry * mult * qty + round_trip_commission;
        cost_basis = entry * mult * qty + commission_open;
    }

    const breakeven = spreadKind === 'credit'
        ? shortStrike - entry - (round_trip_commission / qty / mult)  // simplified for put credit
        : longStrike + entry + (round_trip_commission / qty / mult);  // simplified for call debit

    const risk_reward_ratio = max_loss_at_sl > 0 ? max_profit_at_tp / max_loss_at_sl : 0;
    const gross_profit_at_tp = max_profit_at_tp + round_trip_commission;
    const gross_loss_at_sl = max_loss_at_sl - round_trip_commission;

    const return_on_cost_basis_tp = cost_basis > 0 ? max_profit_at_tp / cost_basis : 0;
    const return_on_cost_basis_sl = cost_basis > 0 ? -max_loss_at_sl / cost_basis : 0;

    return {
        cost_basis,
        commission_open,
        commission_close,
        round_trip_commission,
        gross_profit_at_tp,
        gross_loss_at_sl,
        max_profit_at_tp,
        max_loss_at_sl,
        breakeven,
        risk_reward_ratio,
        return_on_cost_basis_tp,
        return_on_cost_basis_sl,
        theoretical_max_profit: max_profit_at_tp,
        spread_type: spreadKind,
        is_na: false,
    };
}

function naEconomics(reason: string): SignalEconomics {
    return {
        cost_basis: 0,
        commission_open: 0,
        commission_close: 0,
        round_trip_commission: 0,
        gross_profit_at_tp: 0,
        gross_loss_at_sl: 0,
        max_profit_at_tp: 0,
        max_loss_at_sl: 0,
        breakeven: 0,
        risk_reward_ratio: 0,
        return_on_cost_basis_tp: 0,
        return_on_cost_basis_sl: 0,
        theoretical_max_profit: 0,
        is_na: true,
        na_reason: reason,
    };
}

// ============================================================
//  Display helpers
// ============================================================
export function formatRR(ratio: number): string {
    // Always display as "1 : X" where X = reward per $1 risked
    return `1 : ${ratio.toFixed(2)}`;
}

export function rrColorClass(ratio: number): string {
    if (ratio >= 1) return 'green';
    if (ratio >= 0.5) return 'amber';
    return 'red';
}
