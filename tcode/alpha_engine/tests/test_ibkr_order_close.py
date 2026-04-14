"""
Unit tests for ibkr_order.py Phase 12 subcommands:
  cancel_order_with_verify, close_position, schedule_close

All tests mock ib_insync so no live gateway is required.
"""
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone, timedelta

# Resolve alpha_engine on sys.path
_HERE = os.path.dirname(__file__)
_AE = os.path.abspath(os.path.join(_HERE, ".."))
if _AE not in sys.path:
    sys.path.insert(0, _AE)

from ingestion.ibkr_order import (
    cancel_order_with_verify,
    close_position,
    schedule_close,
    _is_market_hours,
    _next_market_open_utc,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_trade(order_id: int, status: str, oca_group: str = "", parent_id: int = 0):
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.order.ocaGroup = oca_group
    trade.order.parentId = parent_id
    trade.order.orderType = "LMT"
    trade.orderStatus.status = status
    trade.orderStatus.filled = 0
    trade.orderStatus.avgFillPrice = 0
    trade.contract.secType = "OPT"
    trade.contract.symbol = "TSLA"
    trade.contract.strike = "365"
    trade.contract.lastTradeDateOrContractMonth = "20260413"
    trade.contract.right = "C"
    trade.contract.conId = 999
    return trade


def _make_ib(trades_before, trades_after=None):
    """Return a mock IB instance pre-loaded with trades."""
    ib = MagicMock()
    ib.trades.side_effect = [trades_before, trades_after or trades_before]
    ib.cancelOrder.return_value = None

    # qualify returns a single mock contract
    mock_contract = MagicMock()
    mock_contract.conId = 999
    ib.qualifyContracts.return_value = [mock_contract]

    # placeOrder returns a mock trade
    placed = _make_trade(42, "PendingSubmit")
    ib.placeOrder.return_value = placed
    return ib


# ── cancel_order_with_verify ──────────────────────────────────────────────────

class TestCancelOrderWithVerify(unittest.TestCase):

    @patch("ingestion.ibkr_order._disconnect")
    @patch("ingestion.ibkr_order._connect")
    def test_cancel_single_order(self, mock_connect, mock_disconnect):
        trade = _make_trade(101, "Cancelled")
        ib = _make_ib([trade], [trade])
        mock_connect.return_value = ib

        result = cancel_order_with_verify("127.0.0.1", 4002, 3, 101)

        self.assertEqual(result["order_id"], 101)
        self.assertIn(result["status"], ("Cancelled", "CancelPending"))
        self.assertEqual(result["oca_cancelled"], [])
        ib.cancelOrder.assert_called_once()

    @patch("ingestion.ibkr_order._disconnect")
    @patch("ingestion.ibkr_order._connect")
    def test_cancel_bracket_parent_detects_oca_siblings(self, mock_connect, mock_disconnect):
        parent = _make_trade(200, "Cancelled", oca_group="GRP1")
        tp     = _make_trade(201, "Cancelled", oca_group="GRP1", parent_id=200)
        sl     = _make_trade(202, "Cancelled", oca_group="GRP1", parent_id=200)

        # Before: all three active; after cancel: all show Cancelled
        before = [parent, tp, sl]
        after  = [
            _make_trade(200, "Cancelled", oca_group="GRP1"),
            _make_trade(201, "Cancelled", oca_group="GRP1", parent_id=200),
            _make_trade(202, "Cancelled", oca_group="GRP1", parent_id=200),
        ]
        ib = MagicMock()
        ib.trades.side_effect = [before, after]
        ib.cancelOrder.return_value = None
        mock_connect.return_value = ib

        result = cancel_order_with_verify("127.0.0.1", 4002, 3, 200)

        self.assertEqual(result["order_id"], 200)
        self.assertIn(201, result["oca_cancelled"])
        self.assertIn(202, result["oca_cancelled"])

    @patch("ingestion.ibkr_order._disconnect")
    @patch("ingestion.ibkr_order._connect")
    def test_raises_if_order_not_found(self, mock_connect, mock_disconnect):
        ib = MagicMock()
        ib.trades.return_value = []
        mock_connect.return_value = ib

        with self.assertRaises(ValueError, msg="Order 999 not found in open orders"):
            cancel_order_with_verify("127.0.0.1", 4002, 3, 999)


# ── close_position ────────────────────────────────────────────────────────────

class TestClosePosition(unittest.TestCase):

    @patch("ingestion.ibkr_order._is_market_hours", return_value=True)
    @patch("ingestion.ibkr_order._close_position_mkt")
    def test_delegates_to_mkt_when_market_open(self, mock_mkt, mock_hours):
        mock_mkt.return_value = {"order_id": 55, "status": "Submitted", "scheduled_for": None, "timestamp": "T"}
        result = close_position("127.0.0.1", 4002, 3, "TSLA", "CALL", 365.0, "2026-04-13", 10)
        mock_mkt.assert_called_once()
        self.assertIsNone(result["scheduled_for"])

    @patch("ingestion.ibkr_order._is_market_hours", return_value=False)
    @patch("ingestion.ibkr_order.schedule_close")
    def test_delegates_to_schedule_when_market_closed(self, mock_sched, mock_hours):
        mock_sched.return_value = {
            "order_id": 77, "status": "PendingSubmit",
            "scheduled_for": "2026-04-14T13:30:00Z", "timestamp": "T",
        }
        result = close_position("127.0.0.1", 4002, 3, "TSLA", "CALL", 365.0, "2026-04-13", 10)
        mock_sched.assert_called_once()
        self.assertEqual(result["scheduled_for"], "2026-04-14T13:30:00Z")


# ── schedule_close ────────────────────────────────────────────────────────────

class TestScheduleClose(unittest.TestCase):

    @patch("ingestion.ibkr_order._disconnect")
    @patch("ingestion.ibkr_order._connect")
    @patch("ingestion.ibkr_order._next_market_open_utc")
    def test_schedule_close_submits_opg_order(self, mock_next_open, mock_connect, mock_disconnect):
        from datetime import datetime, timezone
        next_open = datetime(2026, 4, 14, 13, 30, 0, tzinfo=timezone.utc)
        mock_next_open.return_value = next_open

        placed = _make_trade(99, "PendingSubmit")
        ib = MagicMock()
        ib.qualifyContracts.return_value = [MagicMock(conId=888)]
        ib.placeOrder.return_value = placed
        mock_connect.return_value = ib

        result = schedule_close("127.0.0.1", 4002, 3, "TSLA", "CALL", 365.0, "2026-04-13", 10)

        self.assertEqual(result["order_id"], 99)
        self.assertEqual(result["scheduled_for"], "2026-04-14T13:30:00Z")
        # Verify TIF=OPG was used
        call_args = ib.placeOrder.call_args
        order_arg = call_args[0][1]
        self.assertEqual(order_arg.tif, "OPG")
        self.assertEqual(order_arg.action, "SELL")


# ── market hours helpers ──────────────────────────────────────────────────────

class TestMarketHoursHelpers(unittest.TestCase):

    def test_next_market_open_utc_returns_future_datetime(self):
        nxt = _next_market_open_utc()
        now = datetime.now(timezone.utc)
        self.assertGreater(nxt, now)

    def test_next_market_open_utc_is_weekday(self):
        nxt = _next_market_open_utc()
        # Convert to ET for weekday check
        try:
            import zoneinfo
            et = zoneinfo.ZoneInfo("America/New_York")
            nxt_et = nxt.astimezone(et)
        except Exception:
            nxt_et = nxt.astimezone(timezone(timedelta(hours=-4)))
        self.assertLess(nxt_et.weekday(), 5, "Next open must not be a weekend")
        self.assertEqual((nxt_et.hour, nxt_et.minute), (9, 30))

    def test_is_market_hours_returns_bool(self):
        result = _is_market_hours()
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
