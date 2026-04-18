"""
Phase 17 — Unit tests for circuit_breaker module.
Tests: hard stop at -$2500, soft pause at 3 losses, target celebration.
"""
import sys
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import circuit_breaker


def _reset():
    circuit_breaker._hard_stop = False
    circuit_breaker._soft_pause_until = None


def _make_trades(pnls: list[float]) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"id": i, "net_pnl": pnl, "ts_entry": (now - timedelta(minutes=len(pnls) - i)).isoformat()}
        for i, pnl in enumerate(pnls)
    ]


class TestHardStop:
    def setup_method(self):
        _reset()

    def test_hard_stop_fires_at_limit(self):
        trades = _make_trades([-500, -1000, -1500])  # total = -3000 → over limit
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            with patch.object(circuit_breaker, "_trigger_hard_stop"):
                result = circuit_breaker.evaluate()
        assert result["status"] == circuit_breaker.STATUS_HARD_STOP

    def test_hard_stop_not_fired_below_limit(self):
        trades = _make_trades([-500, -200])  # total = -700
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            result = circuit_breaker.evaluate()
        assert result["status"] != circuit_breaker.STATUS_HARD_STOP

    def test_hard_stop_persists_once_set(self):
        circuit_breaker._hard_stop = True
        trades = _make_trades([100, 200, 300])  # profitable day — but hard stop is latched
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            result = circuit_breaker.evaluate()
        assert result["status"] == circuit_breaker.STATUS_HARD_STOP

    def test_is_trading_blocked_during_hard_stop(self):
        trades = _make_trades([-1000, -2000])  # total = -3000
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            with patch.object(circuit_breaker, "_trigger_hard_stop"):
                blocked = circuit_breaker.is_trading_blocked()
        assert blocked is True


class TestSoftPause:
    def setup_method(self):
        _reset()

    def test_soft_pause_fires_at_consecutive_limit(self):
        trades = _make_trades([-100, -200, -50])  # 3 consecutive losses
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            with patch.object(circuit_breaker, "_trigger_soft_pause"):
                result = circuit_breaker.evaluate()
        assert result["status"] == circuit_breaker.STATUS_SOFT_PAUSE
        assert result["consecutive_losses"] == 3

    def test_soft_pause_does_not_fire_with_win_in_sequence(self):
        trades = _make_trades([-100, 200, -50])  # win in middle — resets streak
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            result = circuit_breaker.evaluate()
        assert result["status"] != circuit_breaker.STATUS_SOFT_PAUSE
        assert result["consecutive_losses"] == 1

    def test_remaining_pause_sec_decreases(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=25)
        circuit_breaker._soft_pause_until = future
        trades = _make_trades([-100, -100, -100])
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            result = circuit_breaker.evaluate()
        assert result["status"] == circuit_breaker.STATUS_SOFT_PAUSE
        assert 24 * 60 < result["remaining_pause_sec"] <= 25 * 60

    def test_soft_pause_expires_and_clears(self):
        circuit_breaker._soft_pause_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        trades = _make_trades([100, 200])  # no more losses after cool-off
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            result = circuit_breaker.evaluate()
        assert result["status"] == circuit_breaker.STATUS_ACTIVE
        assert circuit_breaker._soft_pause_until is None

    def test_is_trading_blocked_during_soft_pause(self):
        circuit_breaker._soft_pause_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        trades = _make_trades([100])
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            blocked = circuit_breaker.is_trading_blocked()
        assert blocked is True


class TestTargetReached:
    def setup_method(self):
        _reset()

    def test_target_reached_banner(self):
        trades = _make_trades([3000, 4000, 3500])  # total = 10500 → above target
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            result = circuit_breaker.evaluate()
        assert result["status"] == circuit_breaker.STATUS_TARGET_REACHED
        assert result["daily_pnl"] > float(os.getenv("DAILY_TARGET", "10000"))

    def test_trading_not_blocked_at_target(self):
        trades = _make_trades([3000, 4000, 3500])
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            blocked = circuit_breaker.is_trading_blocked()
        assert blocked is False


class TestDailyStats:
    def setup_method(self):
        _reset()

    def test_daily_stats_zero_with_no_trades(self):
        with patch.object(circuit_breaker, "_today_trades", return_value=[]):
            stats = circuit_breaker.compute_daily_stats()
        assert stats["daily_pnl"] == 0.0
        assert stats["winners"] == 0
        assert stats["losers"] == 0
        assert stats["consecutive_losses"] == 0

    def test_consecutive_losses_counted_from_end(self):
        # W L W L L  → consecutive = 2
        trades = _make_trades([100, -50, 200, -30, -80])
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            stats = circuit_breaker.compute_daily_stats()
        assert stats["consecutive_losses"] == 2

    def test_reset_for_new_day_clears_state(self):
        circuit_breaker._hard_stop = True
        circuit_breaker._soft_pause_until = datetime.now(timezone.utc) + timedelta(hours=1)
        circuit_breaker.reset_for_new_day()
        assert circuit_breaker._hard_stop is False
        assert circuit_breaker._soft_pause_until is None
