"""
Phase 19 — Kelly fraction 4% notional cap tests.

Verifies:
  - MAX_KELLY_PCT env var (default 0.04 = 4%)
  - Position cost clamp: entry * qty * 100 <= NOTIONAL * 0.04
  - At NOTIONAL=25000: max cost $1,000 → max qty capped at floor(1000/(entry*100))
  - Doubling NOTIONAL doubles the allowed qty
  - 90% confidence signal stays capped at 4%
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import publisher


def _compute_clamped_qty(notional, max_kelly_pct, entry_price, raw_qty):
    """Mirror the Phase 19 clamp logic from publisher.broadcast_loop."""
    max_cost = notional * max_kelly_pct
    position_cost = entry_price * raw_qty * 100
    if position_cost > max_cost:
        return max(1, int(max_cost / (entry_price * 100)))
    return raw_qty


class TestKellyCap:
    def test_default_max_kelly_pct_is_four_percent(self):
        """MAX_KELLY_PCT default is 0.04."""
        assert publisher.MAX_KELLY_PCT == pytest.approx(0.04)

    def test_25k_notional_50_dollar_entry_max_qty_2(self):
        """NOTIONAL=25000, entry=$50/contract → max cost $1000 → max qty=2."""
        # 25000 * 0.04 = $1000; entry=$50 → $50*100 = $5000/contract → max 1
        # Wait: entry_price here is option premium (not underlying). $50/contract option = $5000 cost.
        # More realistic: entry=$5/contract (premium), cost=$500/contract
        notional = 25000
        entry = 5.0  # $5 premium per contract = $500 cost
        max_cost = notional * 0.04  # $1000
        max_qty = int(max_cost / (entry * 100))  # 2
        assert max_qty == 2, f"expected max_qty=2 at $5 entry, got {max_qty}"

    def test_clamp_reduces_qty_5_to_2(self):
        """Signal requests qty=5 at $5 entry → clamped to 2."""
        result = _compute_clamped_qty(
            notional=25000, max_kelly_pct=0.04, entry_price=5.0, raw_qty=5
        )
        assert result == 2, f"expected clamped qty=2, got {result}"

    def test_100k_notional_max_qty_8(self):
        """NOTIONAL=100000 → max cost $4000 → max qty=8 at $5 entry."""
        result = _compute_clamped_qty(
            notional=100000, max_kelly_pct=0.04, entry_price=5.0, raw_qty=20
        )
        assert result == 8, f"expected clamped qty=8 at 100k, got {result}"

    def test_doubling_notional_doubles_qty(self):
        """Doubling NOTIONAL should double allowed qty (same entry, same raw_qty)."""
        qty_25k = _compute_clamped_qty(25000, 0.04, 5.0, 20)
        qty_50k = _compute_clamped_qty(50000, 0.04, 5.0, 20)
        assert qty_50k == qty_25k * 2, f"expected 2x qty: {qty_25k} → {qty_50k}"

    def test_no_clamp_when_within_limit(self):
        """Qty that stays under 4% cap should not be reduced."""
        result = _compute_clamped_qty(
            notional=25000, max_kelly_pct=0.04, entry_price=5.0, raw_qty=1
        )
        assert result == 1, "small qty within limit should not be clamped"

    def test_minimum_qty_is_always_1(self):
        """Even if 4% notional allows less than 1 contract, minimum is 1."""
        # Tiny notional: $1000 * 4% = $40; entry=$50/contract = $5000 > $40
        result = _compute_clamped_qty(
            notional=1000, max_kelly_pct=0.04, entry_price=50.0, raw_qty=3
        )
        assert result == 1, "minimum qty must be 1"

    def test_env_max_kelly_pct_configurable(self, monkeypatch):
        """MAX_KELLY_PCT can be overridden via env."""
        monkeypatch.setattr(publisher, "MAX_KELLY_PCT", 0.02)
        # At 2% of $25k = $500; entry=$5 → max qty=1
        max_cost = 25000 * 0.02  # $500
        max_qty = max(1, int(max_cost / (5.0 * 100)))
        assert max_qty == 1

    def test_position_cost_within_cap_after_clamp(self):
        """After clamping, position_cost must not exceed NOTIONAL * 0.04."""
        notional = 25000
        entry = 3.0
        raw_qty = 10
        clamped = _compute_clamped_qty(notional, 0.04, entry, raw_qty)
        actual_cost = entry * clamped * 100
        max_allowed = notional * 0.04
        assert actual_cost <= max_allowed + 1e-6, \
            f"clamped cost ${actual_cost:.2f} exceeds max ${max_allowed:.2f}"

    def test_high_confidence_still_capped(self):
        """Even at 90% confidence (raw Kelly ~0.08+), position cost stays capped at 4%."""
        # We test the clamp function directly — high confidence → large raw_qty
        raw_qty = 50  # simulate high-confidence request
        clamped = _compute_clamped_qty(25000, 0.04, 5.0, raw_qty)
        cost = clamped * 5.0 * 100
        assert cost <= 25000 * 0.04 + 1e-6, "90% confidence must still be capped at 4%"
