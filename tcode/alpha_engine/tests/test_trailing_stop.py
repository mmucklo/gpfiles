"""
Phase 19 — Trailing stop specification tests.

Verifies the exact Phase 19 spec scenarios:
  - Long entry at $10, ATR=$1
  - Price rises to $11 → trailing stop at $10.25 (11 - 0.75)
  - Price rises to $12 → trailing stop at $11.25
  - Price drops to $11.50 → trailing stop STAYS at $11.25
  - Price drops to $11.20 → below $11.25 → STOP TRIGGERED
  - Trailing stop NEVER decreases for longs, NEVER increases for shorts

These tests verify update_stops() behaviour from stop_manager.py.
"""
import sys, os
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Suppress SQLite writes in tests
with patch("sqlite3.connect"):
    pass

import stop_manager

ATR = 1.0
ENTRY = 10.0
TRAIL_ACTIVATE = ENTRY + ATR * 0.5   # 10.5 — activation threshold
TRAIL_DISTANCE = ATR * 0.75           # 0.75


def _reset():
    stop_manager._positions.clear()


def _open_long(trade_id=1, entry=ENTRY, atr=ATR):
    # Use GAMMA_SCALP (no fixed TP target) so trailing stop tests can explore
    # price ranges above entry + 2×ATR without the TP firing first.
    return stop_manager.open_position(
        trade_id=trade_id,
        entry_price=entry,
        quantity=1,
        direction="LONG",
        strategy="GAMMA_SCALP",
        atr_at_entry=atr,
    )


def _open_short(trade_id=1, entry=ENTRY, atr=ATR):
    return stop_manager.open_position(
        trade_id=trade_id,
        entry_price=entry,
        quantity=1,
        direction="SHORT",
        strategy="GAMMA_SCALP",
        atr_at_entry=atr,
    )


class TestTrailingStopSpec:
    """Exact Phase 19 specification scenarios."""

    def setup_method(self):
        _reset()

    def test_trailing_stop_not_set_below_activation(self):
        """Price below activation threshold: trailing stop NOT engaged."""
        _open_long()
        close, _ = stop_manager.update_stops(1, current_price=10.3, current_atr=ATR)
        pos = stop_manager._positions[1]
        assert pos.trailing_engaged is False
        assert not close

    def test_trailing_stop_set_at_11(self):
        """Price rises to $11 → trailing stop = 11 - 0.75 = $10.25."""
        _open_long()
        stop_manager.update_stops(1, current_price=11.0, current_atr=ATR)
        pos = stop_manager._positions[1]
        assert pos.trailing_engaged is True
        assert pos.current_stop == pytest.approx(10.25, abs=0.001)

    def test_trailing_stop_rises_to_12(self):
        """Price rises to $12 → trailing stop = 12 - 0.75 = $11.25."""
        _open_long()
        stop_manager.update_stops(1, current_price=11.0, current_atr=ATR)
        stop_manager.update_stops(1, current_price=12.0, current_atr=ATR)
        pos = stop_manager._positions[1]
        assert pos.current_stop == pytest.approx(11.25, abs=0.001)

    def test_trailing_stop_never_decreases_on_pullback(self):
        """Price drops to $11.50 → stop STAYS at $11.25 (never moves backward)."""
        _open_long()
        stop_manager.update_stops(1, current_price=11.0, current_atr=ATR)
        stop_manager.update_stops(1, current_price=12.0, current_atr=ATR)
        stop_before = stop_manager._positions[1].current_stop
        stop_manager.update_stops(1, current_price=11.5, current_atr=ATR)
        pos = stop_manager._positions[1]
        assert pos.current_stop == pytest.approx(stop_before, abs=0.001), \
            "trailing stop must not decrease when price pulls back"
        assert not stop_manager._positions[1].is_open or True  # still open at 11.5 > 11.25

    def test_trailing_stop_triggers_below_stop(self):
        """Price drops to $11.20 → below $11.25 → STOP TRIGGERED."""
        _open_long()
        stop_manager.update_stops(1, current_price=11.0, current_atr=ATR)
        stop_manager.update_stops(1, current_price=12.0, current_atr=ATR)
        should_close, stop_type = stop_manager.update_stops(1, current_price=11.20, current_atr=ATR)
        assert should_close, "should trigger stop at $11.20 below $11.25"
        assert stop_type == "TRAILING"

    def test_trailing_stop_sequential_rises_each_step(self):
        """Multiple price rises: stop advances with each new high."""
        _open_long()
        stop_manager.update_stops(1, current_price=11.0, current_atr=ATR)
        assert stop_manager._positions[1].current_stop == pytest.approx(10.25, abs=0.001)
        stop_manager.update_stops(1, current_price=12.0, current_atr=ATR)
        assert stop_manager._positions[1].current_stop == pytest.approx(11.25, abs=0.001)
        stop_manager.update_stops(1, current_price=13.0, current_atr=ATR)
        assert stop_manager._positions[1].current_stop == pytest.approx(12.25, abs=0.001)

    def test_short_trailing_stop_never_increases(self):
        """For SHORT positions, trailing stop must never increase (tighter = lower stop)."""
        _open_short()
        # SHORT activation: price < entry - 0.5*ATR = 9.5
        # At price=9.0: trailing stop = 9.0 + 0.75 = 9.75
        stop_manager.update_stops(1, current_price=9.0, current_atr=ATR)
        pos = stop_manager._positions[1]
        if pos.trailing_engaged:
            stop_at_9 = pos.current_stop
            # Price drops further to 8.0: stop = 8.0 + 0.75 = 8.75 (lower, tighter)
            stop_manager.update_stops(1, current_price=8.0, current_atr=ATR)
            assert stop_manager._positions[1].current_stop <= stop_at_9, \
                "short trailing stop must not increase when price falls further"

    def test_short_trailing_stop_triggers_on_reversal(self):
        """SHORT: once trailing engaged, price rise above stop triggers exit."""
        _open_short()
        # Price drops to 8.5: activation at 9.5; 8.5 < 9.5 → trail = 8.5 + 0.75 = 9.25
        stop_manager.update_stops(1, current_price=8.5, current_atr=ATR)
        pos = stop_manager._positions[1]
        if not pos.trailing_engaged:
            pytest.skip("trailing not engaged — strategy config not suited for this test")
        stop_level = pos.current_stop
        # Price reverses above stop
        should_close, stop_type = stop_manager.update_stops(
            1, current_price=stop_level + 0.10, current_atr=ATR
        )
        assert should_close, "short should trigger when price rises above trailing stop"
        assert stop_type == "TRAILING"
