"""
Phase 17 — Unit tests for stop_manager module.
Tests: initial stop, trailing logic (never moves backward), time stop, target hit.
"""
import sys
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Patch SQLite writes so tests don't need a real DB
with patch("sqlite3.connect") as _:
    pass

import stop_manager


def _reset():
    """Clear in-memory position store between tests."""
    stop_manager._positions.clear()


def _open(
    trade_id=1, entry=10.0, qty=1, direction="LONG", strategy="MOMENTUM", atr=1.0
):
    return stop_manager.open_position(
        trade_id=trade_id,
        entry_price=entry,
        quantity=qty,
        direction=direction,
        strategy=strategy,
        atr_at_entry=atr,
    )


class TestInitialStop:
    def setup_method(self):
        _reset()

    def test_long_initial_stop_below_entry(self):
        pos = _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # stop_mult = 1.5 → stop = 10 - 1.5 = 8.5
        assert pos.initial_stop == pytest.approx(8.5)

    def test_short_initial_stop_above_entry(self):
        pos = _open(trade_id=2, entry=10.0, direction="SHORT", atr=1.0)
        # stop = 10 + 1.5 = 11.5
        assert pos.initial_stop == pytest.approx(11.5)

    def test_wave_rider_stop_multiplier(self):
        pos = stop_manager.open_position(
            trade_id=3, entry_price=10.0, quantity=1,
            direction="LONG", strategy="WAVE_RIDER", atr_at_entry=1.0
        )
        # stop_mult = 1.0 → stop = 10 - 1.0 = 9.0
        assert pos.initial_stop == pytest.approx(9.0)

    def test_straddle_stop_multiplier(self):
        pos = stop_manager.open_position(
            trade_id=4, entry_price=10.0, quantity=1,
            direction="LONG", strategy="STRADDLE", atr_at_entry=1.0
        )
        # stop_mult = 2.0 → stop = 10 - 2.0 = 8.0
        assert pos.initial_stop == pytest.approx(8.0)

    def test_target_computed_at_entry(self):
        pos = _open(trade_id=5, entry=10.0, direction="LONG", atr=1.0)
        # target_mult = 2.0 → target = 10 + 2.0 = 12.0
        assert pos.target == pytest.approx(12.0)

    def test_gamma_scalp_no_fixed_target(self):
        pos = stop_manager.open_position(
            trade_id=6, entry_price=10.0, quantity=1,
            direction="LONG", strategy="GAMMA_SCALP", atr_at_entry=1.0
        )
        assert pos.target is None

    def test_time_stop_set_correctly_momentum(self):
        pos = _open(trade_id=7, entry=10.0, strategy="MOMENTUM")
        delta = pos.time_stop_at - pos.entry_time
        assert delta.total_seconds() == pytest.approx(15 * 60, abs=2)


class TestTrailingStop:
    def setup_method(self):
        _reset()

    def test_trailing_not_engaged_without_profit(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # Current price below activation threshold (entry + 0.5*ATR = 10.5)
        should_close, stop_type = stop_manager.update_stops(1, current_price=10.2, current_atr=1.0)
        assert stop_manager._positions[1].trailing_engaged is False
        assert not should_close

    def test_trailing_engages_on_profit(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # Price above activation: 10 + 0.5*1 = 10.5 → price at 11.0 activates trail
        stop_manager.update_stops(1, current_price=11.0, current_atr=1.0)
        assert stop_manager._positions[1].trailing_engaged is True

    def test_trailing_stop_never_moves_backward(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # Activate trail at 11.0 → trail = 11.0 - 0.75 = 10.25
        stop_manager.update_stops(1, current_price=11.0, current_atr=1.0)
        trail_after_rise = stop_manager._positions[1].current_stop

        # Price drops to 10.5 (above trail) — stop must NOT decrease
        stop_manager.update_stops(1, current_price=10.5, current_atr=1.0)
        trail_after_drop = stop_manager._positions[1].current_stop
        assert trail_after_drop >= trail_after_rise, "Trailing stop moved backward!"

    def test_trailing_stop_fires_on_reversal(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # Run price up to 11.5 (below target=12.0) to engage trail
        # activation threshold: 0.5×ATR=0.5 per share → price must be > 10.5
        # trail = 11.5 - 0.75 = 10.75
        stop_manager.update_stops(1, current_price=11.5, current_atr=1.0)
        assert stop_manager._positions[1].trailing_engaged is True
        trail = stop_manager._positions[1].current_stop
        assert trail == pytest.approx(10.75)

        # Price drops below trailing stop (10.75) → fires TRAILING
        should_close, stop_type = stop_manager.update_stops(1, current_price=10.5, current_atr=1.0)
        assert should_close
        assert stop_type == "TRAILING"


class TestTimeStop:
    def setup_method(self):
        _reset()

    def test_time_stop_fires_after_expiry(self):
        pos = _open(trade_id=1, entry=10.0, strategy="MOMENTUM")
        # Manually wind back the time_stop_at to the past
        stop_manager._positions[1].time_stop_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        should_close, stop_type = stop_manager.update_stops(1, current_price=10.0, current_atr=1.0)
        assert should_close
        assert stop_type == "TIME_STOP"

    def test_time_stop_has_priority_over_target(self):
        pos = _open(trade_id=1, entry=10.0, strategy="MOMENTUM")
        stop_manager._positions[1].time_stop_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        # Price at target (12.0) but time stop fires first
        should_close, stop_type = stop_manager.update_stops(1, current_price=12.0, current_atr=1.0)
        assert stop_type == "TIME_STOP"


class TestTargetHit:
    def setup_method(self):
        _reset()

    def test_long_target_fires_at_tp(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # target = 12.0 → hit at 12.5
        should_close, stop_type = stop_manager.update_stops(1, current_price=12.5, current_atr=1.0)
        assert should_close
        assert stop_type == "TP"

    def test_long_target_not_fired_below(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        should_close, stop_type = stop_manager.update_stops(1, current_price=11.5, current_atr=1.0)
        assert not should_close

    def test_short_target_fires_below(self):
        stop_manager.open_position(
            trade_id=1, entry_price=10.0, quantity=1,
            direction="SHORT", strategy="MOMENTUM", atr_at_entry=1.0
        )
        # target for SHORT = 10 - 2.0 = 8.0 → price at 7.5 fires
        should_close, stop_type = stop_manager.update_stops(1, current_price=7.5, current_atr=1.0)
        assert should_close
        assert stop_type == "TP"


class TestInitialStopCutLoss:
    def setup_method(self):
        _reset()

    def test_initial_stop_fires_on_loss(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        # stop = 8.5 → price at 8.0 → fires
        should_close, stop_type = stop_manager.update_stops(1, current_price=8.0, current_atr=1.0)
        assert should_close
        assert stop_type == "SL"

    def test_no_fire_above_initial_stop(self):
        _open(trade_id=1, entry=10.0, direction="LONG", atr=1.0)
        should_close, _ = stop_manager.update_stops(1, current_price=8.6, current_atr=1.0)
        assert not should_close


class TestManualClose:
    def setup_method(self):
        _reset()

    def test_manual_close_marks_closed(self):
        _open(trade_id=1)
        with patch.object(stop_manager, "close_position") as mock_close:
            result = stop_manager.manual_close(1, exit_price=11.0)
        assert result is True

    def test_manual_close_unknown_id(self):
        result = stop_manager.manual_close(999, exit_price=10.0)
        assert result is False
