"""
Phase 19 — Tests for the configurable commission ratio gate.

check_commission_ratio_gate() rejects signals where
  expected_profit < MIN_PROFIT_COMMISSION_RATIO × commission

where:
  expected_profit = abs(tp - entry) * qty * 100
  commission      = qty * 2 * COMMISSION_PER_CONTRACT
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import publisher


def _gate(entry, tp, qty):
    """Thin wrapper around check_commission_ratio_gate using module-level defaults."""
    return publisher.check_commission_ratio_gate(entry, tp, qty)


class TestCommissionRatioGate:
    """Unit tests for check_commission_ratio_gate with default env (0.65/contract, ratio=3.0)."""

    def test_pass_five_dollar_premium_qty1(self):
        """$0.05 premium, qty=1 → expected profit ~$5, commission ~$1.30 → ratio 3.8 → PASS."""
        # entry=$5.00, tp=$5.05 → gross_profit = 0.05 * 1 * 100 = $5
        ok, reason = _gate(5.00, 5.05, 1)
        assert ok, f"should pass: {reason}"

    def test_reject_two_cent_premium_qty1(self):
        """$0.02 premium, qty=1 → expected profit ~$2, commission ~$1.30 → ratio 1.5 → REJECT."""
        ok, reason = _gate(5.00, 5.02, 1)
        assert not ok, "should reject low-profit signal"
        assert "COMMISSION-REJECT" in reason

    def test_pass_five_dollar_premium_qty3(self):
        """$5.00 premium, qty=3 → expected profit ~$500, commission ~$3.90 → ratio 128 → PASS."""
        ok, reason = _gate(10.00, 15.00, 3)
        assert ok, f"should pass large-profit signal: {reason}"

    def test_boundary_at_and_above_ratio(self):
        """Signal clearly at 3× commission or above should PASS."""
        # commission = 1 * 2 * 0.65 = $1.30, min_required = 3.0 * 1.30 = $3.90
        # gross_profit = 0.04 * 1 * 100 = $4.00 ≥ $3.90 → PASS
        ok, reason = _gate(5.00, 5.04, 1)
        assert ok, f"should pass at 3.08x ratio: {reason}"

    def test_boundary_just_below_ratio(self):
        """Signal clearly below 3× commission should REJECT."""
        # commission = 1.30, min = 3.90; expected_profit = 0.03 * 100 = $3.00 < $3.90
        ok, _ = _gate(5.00, 5.03, 1)
        assert not ok, "should reject signal at 2.3x commission ratio"

    def test_higher_qty_raises_required_profit(self):
        """With qty=5, commission=6.50, required profit=19.50; small delta should reject."""
        # delta = 0.01 → expected = 0.01 * 5 * 100 = $5, required = 5 * 2 * 0.65 * 3 = $19.50
        ok, _ = _gate(5.00, 5.01, 5)
        assert not ok, "small delta with large qty should reject"

    def test_reject_message_contains_expected_fields(self):
        """Rejection message must contain COMMISSION-REJECT and numerical values."""
        ok, reason = _gate(5.00, 5.01, 1)
        assert not ok
        assert "COMMISSION-REJECT" in reason
        assert "expected_profit" in reason

    def test_configurable_commission_via_env(self, monkeypatch):
        """Lower commission per contract → easier to pass."""
        monkeypatch.setattr(publisher, "COMMISSION_PER_CONTRACT", 0.10)
        monkeypatch.setattr(publisher, "MIN_PROFIT_COMMISSION_RATIO", 3.0)
        # expected = 0.02 * 1 * 100 = $2, commission = 1*2*0.10 = $0.20, required = $0.60 → PASS
        ok, reason = _gate(5.00, 5.02, 1)
        assert ok, f"should pass with low commission: {reason}"

    def test_configurable_ratio_via_env(self, monkeypatch):
        """Higher ratio (10×) should reject signals that would pass at 3×."""
        monkeypatch.setattr(publisher, "COMMISSION_PER_CONTRACT", 0.65)
        monkeypatch.setattr(publisher, "MIN_PROFIT_COMMISSION_RATIO", 10.0)
        # $0.05 premium: expected=$5, commission=$1.30, required=$13 → REJECT
        ok, _ = _gate(5.00, 5.05, 1)
        assert not ok, "should reject at 10x ratio even with $0.05 premium"
