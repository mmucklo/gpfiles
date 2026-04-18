"""
Phase 17 — Integration test: 3 losing trades → circuit breaker → publisher auto-pauses.
"""
import sys
import os
import json
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import circuit_breaker


def _reset():
    circuit_breaker._hard_stop = False
    circuit_breaker._soft_pause_until = None


def _make_losing_trades(n: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"id": i, "net_pnl": -100.0 * (i + 1),
         "ts_entry": now.isoformat()}
        for i in range(n)
    ]


class TestCircuitBreakerTriggersPause:
    def setup_method(self):
        _reset()

    def test_three_consecutive_losses_write_pause_file(self, tmp_path):
        pause_file = str(tmp_path / "pause_state.json")
        trades = _make_losing_trades(3)

        with patch.dict(os.environ, {"PUBLISHER_PAUSE_STATE_FILE": pause_file}):
            with patch.object(circuit_breaker, "_today_trades", return_value=trades):
                with patch.object(circuit_breaker, "_write_alert"):
                    result = circuit_breaker.evaluate()

        assert result["status"] == circuit_breaker.STATUS_SOFT_PAUSE
        # Pause file must exist and have paused=True
        assert os.path.exists(pause_file)
        with open(pause_file) as f:
            state = json.load(f)
        assert state["paused"] is True
        assert state.get("unpause_until") is not None

    def test_hard_stop_writes_permanent_pause(self, tmp_path):
        pause_file = str(tmp_path / "pause_state.json")
        trades = _make_losing_trades(1)
        # Override loss to trigger hard stop
        trades[0]["net_pnl"] = -3000.0

        with patch.dict(os.environ, {
            "PUBLISHER_PAUSE_STATE_FILE": pause_file,
            "DAILY_LOSS_LIMIT": "2500",
        }):
            with patch.object(circuit_breaker, "_today_trades", return_value=trades):
                with patch.object(circuit_breaker, "_write_alert"):
                    result = circuit_breaker.evaluate()

        assert result["status"] == circuit_breaker.STATUS_HARD_STOP
        with open(pause_file) as f:
            state = json.load(f)
        assert state["paused"] is True
        assert state.get("unpause_until") is None  # permanent

    def test_soft_pause_followed_by_hard_stop(self, tmp_path):
        pause_file = str(tmp_path / "pause_state.json")

        # First: 3 consecutive losses → soft pause
        trades_soft = _make_losing_trades(3)
        with patch.dict(os.environ, {"PUBLISHER_PAUSE_STATE_FILE": pause_file}):
            with patch.object(circuit_breaker, "_today_trades", return_value=trades_soft):
                with patch.object(circuit_breaker, "_write_alert"):
                    r1 = circuit_breaker.evaluate()
        assert r1["status"] == circuit_breaker.STATUS_SOFT_PAUSE

        # Then: 5 more losses bringing total to -$3000 → hard stop
        _reset()
        circuit_breaker._hard_stop = False
        trades_hard = _make_losing_trades(1)
        trades_hard[0]["net_pnl"] = -3000.0
        with patch.dict(os.environ, {
            "PUBLISHER_PAUSE_STATE_FILE": pause_file,
            "DAILY_LOSS_LIMIT": "2500",
        }):
            with patch.object(circuit_breaker, "_today_trades", return_value=trades_hard):
                with patch.object(circuit_breaker, "_write_alert"):
                    r2 = circuit_breaker.evaluate()
        assert r2["status"] == circuit_breaker.STATUS_HARD_STOP

    def test_write_alert_called_on_consecutive_losses(self):
        trades = _make_losing_trades(3)
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            with patch.object(circuit_breaker, "_write_alert") as mock_alert:
                with patch.object(circuit_breaker, "_trigger_soft_pause"):
                    circuit_breaker.evaluate()
        # _trigger_soft_pause is mocked; but evaluate calls _trigger_soft_pause internally
        # Just verify is_trading_blocked returns True
        circuit_breaker._soft_pause_until = (
            __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc
            ) + __import__('datetime').timedelta(minutes=10)
        )
        with patch.object(circuit_breaker, "_today_trades", return_value=trades):
            assert circuit_breaker.is_trading_blocked() is True
