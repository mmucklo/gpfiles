"""
Gate test: Commission-aware signal viability check.

Tests the publisher.py commission-viability gate added in Phase 2 / FIX 3.

The gate rejects signals where IBKR round-trip commissions eliminate or invert
the profit at the stated take-profit price.

Commission schedule used (IBKR Pro Options):
  $0.65 / contract / leg, $1.00 minimum per leg (order), 2 legs for a round trip.
  Spreads have 4 legs (2 per side × open + close).
"""
import sys
import os
import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — tests run from repo root via pytest alpha_engine/tests/
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Import the functions under test.  We import from the alpha_engine package
# path that matches how publisher.py is invoked.
from alpha_engine.publisher import (  # noqa: E402
    compute_round_trip_commission,
    signal_is_commission_viable,
    IBKR_OPTION_FEE_PER_CONTRACT,
    IBKR_OPTION_MIN_PER_LEG,
)


# ---------------------------------------------------------------------------
# compute_round_trip_commission
# ---------------------------------------------------------------------------

class TestComputeRoundTripCommission:

    def test_single_leg_large_qty(self):
        """50 contracts: per_leg = 0.65 × 50 = $32.50; round trip = $65."""
        assert compute_round_trip_commission(50) == pytest.approx(65.0)

    def test_single_leg_min_floor(self):
        """1 contract: per_leg is floored at $1 min; round trip = $2."""
        assert compute_round_trip_commission(1) == pytest.approx(2.0)

    def test_single_leg_two_contracts(self):
        """2 contracts: per_leg = max(0.65×2, 1.00) = $1.30; round trip = $2.60."""
        assert compute_round_trip_commission(2) == pytest.approx(2.60)

    def test_spread_doubles_legs(self):
        """Spread with 50 contracts: 4 legs × $32.50 = $130."""
        assert compute_round_trip_commission(50, is_spread=True) == pytest.approx(130.0)

    def test_spread_min_floor(self):
        """Spread with 1 contract: 4 legs × $1.00 = $4."""
        assert compute_round_trip_commission(1, is_spread=True) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# signal_is_commission_viable
# ---------------------------------------------------------------------------

class TestSignalIsCommissionViable:

    # ── Rejection cases ──────────────────────────────────────────────────────

    def test_single_contract_cheap_option_rejected(self):
        """$0.05 limit × 1 contract, TP=$0.10.
        Gross = (0.10-0.05)×100×1 = $5; commission = $2; net = $3 → ACCEPTED.
        Use TP=$0.06 to trigger rejection: gross=$1, commission=$2, net=-$1.
        """
        viable, reason = signal_is_commission_viable(
            limit_price=0.05,
            take_profit_price=0.06,
            stop_loss_price=0.01,
            qty=1,
        )
        assert not viable, f"Expected REJECTED but got ACCEPTED. reason={reason!r}"
        assert "commission-negative" in reason

    def test_small_qty_at_limit_floor(self):
        """2 contracts, $0.05 limit TP=$0.06:
        Gross = (0.01)×100×2=$2; commission=max(0.65×2,1)×2=$2.60; net=-$0.60 → REJECTED.
        """
        viable, reason = signal_is_commission_viable(
            limit_price=0.05,
            take_profit_price=0.06,
            stop_loss_price=0.01,
            qty=2,
        )
        assert not viable, f"Expected REJECTED but got ACCEPTED. reason={reason!r}"

    def test_zero_net_profit_rejected(self):
        """Net profit exactly zero is rejected (gate: net > 0, not net >= 0)."""
        # Need gross == commission.
        # commission for qty=1 = $2; gross = TP_delta×100×1 = $2 → TP_delta = $0.02
        # limit=0.10, tp=0.12: gross=(0.02)×100=2, commission=2 → net=0 → REJECTED
        viable, reason = signal_is_commission_viable(
            limit_price=0.10,
            take_profit_price=0.12,
            stop_loss_price=0.05,
            qty=1,
        )
        assert not viable, f"Net=0 should be REJECTED but got viable. reason={reason!r}"

    # ── Acceptance cases ──────────────────────────────────────────────────────

    def test_standard_trade_accepted(self):
        """$0.28 × 50 contracts TP=$0.36 (spec example).
        Gross = (0.36-0.28)×100×50 = $400; commission=$65; net=$335 → ACCEPTED.
        """
        viable, reason = signal_is_commission_viable(
            limit_price=0.28,
            take_profit_price=0.36,
            stop_loss_price=0.15,
            qty=50,
        )
        assert viable, f"Expected ACCEPTED but got REJECTED. reason={reason!r}"
        assert reason == ""

    def test_single_contract_profitable(self):
        """1 contract $1.00 → TP $1.50: gross=$50, commission=$2, net=$48 → ACCEPTED."""
        viable, reason = signal_is_commission_viable(
            limit_price=1.00,
            take_profit_price=1.50,
            stop_loss_price=0.50,
            qty=1,
        )
        assert viable, f"Expected ACCEPTED but got REJECTED. reason={reason!r}"

    def test_ten_contracts_accepted(self):
        """10 contracts at $0.50 → TP $0.70.
        Gross=(0.20)×100×10=$200; commission=max(0.65×10,1)×2=$13; net=$187 → ACCEPTED.
        """
        viable, reason = signal_is_commission_viable(
            limit_price=0.50,
            take_profit_price=0.70,
            stop_loss_price=0.25,
            qty=10,
        )
        assert viable, f"Expected ACCEPTED but got REJECTED. reason={reason!r}"

    # ── Spread cases ──────────────────────────────────────────────────────────

    def test_spread_small_credit_rejected(self):
        """Spread: 1 contract, credit=$0.05, TP=$0.03 (buy back at 60% of credit).
        Gross = abs(0.03-0.05)×100×1 = $2; spread commission = $4; net = -$2 → REJECTED.
        """
        viable, reason = signal_is_commission_viable(
            limit_price=0.05,
            take_profit_price=0.03,
            stop_loss_price=0.10,
            qty=1,
            is_spread=True,
        )
        assert not viable, f"Expected REJECTED for small spread credit but got ACCEPTED. reason={reason!r}"

    def test_spread_adequate_credit_accepted(self):
        """Spread: 5 contracts, credit=$2.00, TP=$1.00 (50% profit on credit spread).
        Gross=abs(1.00-2.00)×100×5=$500; commission=max(0.65×5,1)×4=max(3.25,1)×4=$13; net=$487 → ACCEPTED.
        """
        viable, reason = signal_is_commission_viable(
            limit_price=2.00,
            take_profit_price=1.00,
            stop_loss_price=4.00,
            qty=5,
            is_spread=True,
        )
        assert viable, f"Expected ACCEPTED for adequate spread credit but got REJECTED. reason={reason!r}"


# ── Constants sanity (module-level function, not class method) ─────────────

def test_commission_constants():
    """Verify the IBKR commission constants match the documented schedule."""
    assert IBKR_OPTION_FEE_PER_CONTRACT == pytest.approx(0.65)
    assert IBKR_OPTION_MIN_PER_LEG == pytest.approx(1.00)
