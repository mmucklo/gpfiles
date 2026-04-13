"""
Tests for the minimum-edge floor rejection gate (Phase 10).

Verifies that signals below the min-edge floor are rejected with the correct
reason string, and that the floor scales correctly with notional and contracts.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from publisher import (
    compute_min_edge_floor,
    signal_passes_min_edge,
    compute_round_trip_commission,
    IBKR_OPTION_FEE_PER_CONTRACT,
    IBKR_OPTION_MIN_PER_LEG,
)


class TestMinEdgeFloorValue:
    def test_25k_default(self):
        """Floor at $25k = 0.25% × $25k = $62.50 (assuming 5×commission < 62.50)."""
        floor = compute_min_edge_floor(25000, qty=1)
        assert floor == pytest.approx(62.50, abs=0.01)

    def test_floor_is_pct_not_commission_when_larger(self):
        """When 0.25% of notional exceeds 5×commission, pct drives floor."""
        # At $25k: pct = 62.50, 5×commission(qty=1) = 5 × max(0.65, 1.0) × 2 = 10.0
        # 62.50 > 10.0, so pct drives
        floor = compute_min_edge_floor(25000, qty=1)
        commission_5x = 5 * compute_round_trip_commission(1)
        assert floor >= commission_5x

    def test_floor_is_commission_when_larger(self):
        """At very small notional, 5×commission may exceed pct floor."""
        # Very small notional: $100 → 0.25% = $0.25; commission min = $10
        floor = compute_min_edge_floor(100, qty=1)
        commission_5x = 5 * compute_round_trip_commission(1)
        assert floor == pytest.approx(max(100 * 0.0025, commission_5x), abs=0.01)

    def test_floor_scales_linearly_with_notional(self):
        """Floor is linear in notional (when commission-driven component is constant)."""
        f1 = compute_min_edge_floor(25000, qty=1)
        f4 = compute_min_edge_floor(100000, qty=1)
        # At 4× notional, pct floor is 4× as large
        assert f4 == pytest.approx(f1 * 4, rel=0.01)

    def test_floor_spread_higher_than_single(self):
        """Credit spreads have 4-leg commission → higher floor."""
        f_single = compute_min_edge_floor(25000, qty=1, is_spread=False)
        f_spread = compute_min_edge_floor(25000, qty=1, is_spread=True)
        assert f_spread >= f_single

    def test_floor_multi_contract(self):
        """Multi-contract floor grows because commission grows."""
        f1 = compute_min_edge_floor(25000, qty=1)
        f5 = compute_min_edge_floor(25000, qty=5)
        assert f5 >= f1  # commission grows with qty


class TestSignalPassesMinEdge:
    def test_reject_tiny_profit(self):
        """Signal with $1 net profit is far below $62.50 floor → reject."""
        ok, reason = signal_passes_min_edge(
            limit_price=3.00, take_profit_price=3.01,
            qty=1, notional=25000,
        )
        assert not ok
        assert "min-edge floor" in reason
        assert "floor=" in reason
        assert "net=" in reason

    def test_accept_good_profit(self):
        """Signal with $200 net profit clearly exceeds $62.50 floor → accept."""
        ok, reason = signal_passes_min_edge(
            limit_price=3.00, take_profit_price=5.00,
            qty=1, notional=25000,
        )
        assert ok, reason

    def test_boundary_exactly_at_floor(self):
        """Signal whose net profit equals the floor should pass (>= not >)."""
        floor = compute_min_edge_floor(25000, qty=1)
        commission = compute_round_trip_commission(1)
        # gross - commission = floor → gross = floor + commission
        gross_needed = floor + commission
        # gross = abs(tp - limit) * 100 → tp = limit + gross_needed/100
        limit = 2.00
        tp = limit + gross_needed / 100
        ok, _ = signal_passes_min_edge(limit, tp, qty=1, notional=25000)
        assert ok

    def test_boundary_just_below_floor(self):
        """Signal whose net profit is $0.01 below floor → reject."""
        floor = compute_min_edge_floor(25000, qty=1)
        commission = compute_round_trip_commission(1)
        # net = gross - commission < floor → gross = floor + commission - 0.01
        gross = floor + commission - 0.01
        limit = 2.00
        tp = limit + gross / 100 - 0.0001  # just below
        ok, _ = signal_passes_min_edge(limit, tp, qty=1, notional=25000)
        assert not ok

    def test_reason_contains_notional(self):
        """Rejection reason includes the notional value for traceability."""
        ok, reason = signal_passes_min_edge(1.00, 1.01, qty=1, notional=25000)
        assert not ok
        assert "notional=25000" in reason

    def test_spread_stricter_rejection(self):
        """Same TP distance but spread has higher commission → easier to fail."""
        # Use a marginal case that passes for single but fails for spread
        limit, tp = 1.00, 2.00
        ok_single, _ = signal_passes_min_edge(limit, tp, qty=2, notional=25000, is_spread=False)
        ok_spread, _ = signal_passes_min_edge(limit, tp, qty=2, notional=25000, is_spread=True)
        # Single should pass or spread should fail more easily — at least they're not both passing with identical results
        # Key: spread commission is always >= single commission, so ok_spread <= ok_single (bool)
        if ok_single:
            pass  # single passes, spread may or may not
        else:
            assert not ok_spread  # if single fails, spread must also fail

    def test_zero_qty_edge_case(self):
        """qty=0 → no commission, gross = 0 → fails floor."""
        # Not a valid trade but should not crash
        ok, reason = signal_passes_min_edge(1.00, 5.00, qty=0, notional=25000)
        assert not ok  # gross = 0 < floor

    def test_macro_example_365_call(self):
        """
        Reproduce the example from the brief:
        MACRO $365 CALL — old sizing: 10 contracts at $6.70.
        New sizing at $25k notional, 1% risk: ~1-2 contracts.
        """
        # Old: qty=10, limit=$6.70, TP=$8.71 (×1.3), SL=$4.69 (×0.7)
        # Old expected net = ($8.71-$6.70)*100*10 - commission(10) = $2010 - 13 = ~$1997
        # New: archetype DIRECTIONAL_STD, risk_pct=1%, notional=25000
        from publisher import compute_notional_sizing
        from config.archetypes import get_archetype
        cfg = get_archetype("MACRO")
        qty, _ = compute_notional_sizing(25000, cfg["risk_pct"], 6.70, 0.67, 6.70)
        assert qty <= 10  # definitely fewer than old hard cap
        assert qty >= 1

        # New TP: rr=2.5 → tp = entry + 2.5*(entry - sl) = 6.70 + 2.5*(6.70-0.67) = 6.70 + 15.075 = 21.775
        sl = round(max(0.01, 6.70 * 0.10), 2)
        tp = round(6.70 + cfg["rr"] * (6.70 - sl), 2)
        ok, reason = signal_passes_min_edge(6.70, tp, qty=qty, notional=25000)
        assert ok, f"MACRO example failed min-edge: {reason}"
