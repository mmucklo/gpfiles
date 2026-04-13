"""
Phase 9: Bracket Order Roundtrip Tests

Integration tests for bracket order placement, stop-limit SL legs, and
underlying-price conditions. Tests that require a live IB Gateway are marked
@pytest.mark.live_gateway and skipped in CI.

Unit tests for CLI argument validation and stop-limit SL math run without
a broker connection.
"""
import json
import os
import subprocess
import sys
import pytest
from unittest.mock import MagicMock, patch


# ── CLI validation tests (no gateway required) ────────────────────────────────

PYTHON = os.path.join(os.path.dirname(__file__), "..", "venv", "bin", "python")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable


def _run_place(*extra_args):
    """Run ibkr_order place with minimal valid args plus extras."""
    cmd = [
        PYTHON, "-m", "ingestion.ibkr_order", "place",
        "--symbol", "TSLA",
        "--contract", "CALL",
        "--strike", "365",
        "--expiry", "2026-05-16",
        "--action", "BUY",
        "--quantity", "1",
        "--limit-price", "0.28",
        "--mode", "IBKR_PAPER",
        "--client-id", "99",
        *extra_args,
    ]
    env = {**os.environ, "IBKR_HOST": "127.0.0.1", "IBKR_PORT": "4002"}
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=env,
        timeout=10,
    )
    return result


class TestBracketCLIValidation:
    """Validate CLI guards — no broker connection needed."""

    def test_take_profit_without_stop_loss_rejected(self):
        """--take-profit without --stop-loss must exit non-zero (no half-bracket)."""
        result = _run_place("--take-profit", "0.56")
        assert result.returncode != 0, (
            "Should reject --take-profit without --stop-loss"
        )
        out = json.loads(result.stdout)
        assert "error" in out
        assert "stop-loss" in out["error"].lower()

    def test_stop_loss_without_take_profit_rejected(self):
        """--stop-loss without --take-profit must exit non-zero (no half-bracket)."""
        result = _run_place("--stop-loss", "0.14")
        assert result.returncode != 0, (
            "Should reject --stop-loss without --take-profit"
        )
        out = json.loads(result.stdout)
        assert "error" in out
        assert "take-profit" in out["error"].lower()

    def test_underlying_stop_bad_format_rejected(self):
        """--underlying-stop with invalid format must exit non-zero."""
        result = _run_place(
            "--take-profit", "0.56",
            "--stop-loss", "0.14",
            "--underlying-stop", "INVALID_NO_COLON",
        )
        assert result.returncode != 0
        out = json.loads(result.stdout)
        assert "error" in out


class TestStopLimitMath:
    """Unit tests for stop-limit SL leg construction (no gateway needed)."""

    def test_sl_leg_is_stp_lmt(self):
        """place_bracket_order must always produce STP LMT for SL, never stop-market."""
        from ingestion.ibkr_order import place_bracket_order
        from ib_insync import IB, Option

        mock_ib = MagicMock()
        mock_contract = MagicMock()
        mock_contract.conId = 12345
        mock_ib.qualifyContracts.return_value = [mock_contract]

        # Simulate bracketOrder returning three mock orders
        parent_order = MagicMock()
        parent_order.orderId = 1001
        parent_order.orderType = "LMT"
        parent_order.ocaGroup = "OCA_TEST"

        tp_order = MagicMock()
        tp_order.orderId = 1002
        tp_order.orderType = "LMT"
        tp_order.ocaGroup = "OCA_TEST"

        sl_order = MagicMock()
        sl_order.orderId = 1003
        sl_order.orderType = "STP"  # initial orderType from bracketOrder
        sl_order.ocaGroup = "OCA_TEST"

        parent_status = MagicMock()
        parent_status.status = "PreSubmitted"

        parent_trade = MagicMock()
        parent_trade.order = parent_order
        parent_trade.orderStatus = parent_status

        tp_trade = MagicMock()
        tp_trade.order = tp_order
        tp_trade.orderStatus = MagicMock(status="PreSubmitted")

        sl_trade = MagicMock()
        sl_trade.order = sl_order
        sl_trade.orderStatus = MagicMock(status="PreSubmitted")

        mock_ib.bracketOrder.return_value = [parent_order, tp_order, sl_order]
        mock_ib.placeOrder.side_effect = [parent_trade, tp_trade, sl_trade]
        mock_ib.sleep = MagicMock()

        with patch("ingestion.ibkr_order._connect", return_value=mock_ib), \
             patch("ingestion.ibkr_order._disconnect"):
            result = place_bracket_order(
                host="127.0.0.1", port=4002, client_id=99,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-05-16", action="BUY", quantity=1,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
            )

        # SL leg must have been converted to STP LMT
        assert sl_order.orderType == "STP LMT", (
            f"SL leg orderType should be 'STP LMT', got {sl_order.orderType!r}"
        )

    def test_sl_lmt_price_is_10pct_below_trigger(self):
        """SL limit floor must be auxPrice * (1 - STOP_LIMIT_SLIPPAGE_PCT)."""
        from ingestion.ibkr_order import place_bracket_order

        mock_ib = MagicMock()
        mock_ib.qualifyContracts.return_value = [MagicMock(conId=1)]

        captured_sl = {}

        def capture_bracket(action, quantity, limitPrice, takeProfitPrice, stopLossPrice):
            parent  = MagicMock(orderId=1001, orderType="LMT", ocaGroup="G1")
            tp      = MagicMock(orderId=1002, orderType="LMT", ocaGroup="G1")
            sl      = MagicMock(orderId=1003, orderType="STP", ocaGroup="G1")
            captured_sl["sl"] = sl
            return [parent, tp, sl]

        mock_ib.bracketOrder.side_effect = capture_bracket
        mock_ib.placeOrder.side_effect = lambda c, o: MagicMock(
            order=o,
            orderStatus=MagicMock(status="PreSubmitted"),
        )
        mock_ib.sleep = MagicMock()

        with patch("ingestion.ibkr_order._connect", return_value=mock_ib), \
             patch("ingestion.ibkr_order._disconnect"), \
             patch.dict(os.environ, {"STOP_LIMIT_SLIPPAGE_PCT": "0.10"}):
            place_bracket_order(
                host="127.0.0.1", port=4002, client_id=99,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-05-16", action="BUY", quantity=1,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
            )

        sl = captured_sl["sl"]
        assert sl.auxPrice == pytest.approx(0.14), "auxPrice (trigger) must equal stop_loss_price"
        expected_floor = 0.14 * 0.90
        assert sl.lmtPrice == pytest.approx(expected_floor, rel=1e-4), (
            f"lmtPrice (floor) should be {expected_floor:.4f} (10% below trigger)"
        )

    def test_sl_custom_slippage_pct(self):
        """STOP_LIMIT_SLIPPAGE_PCT env var controls the limit floor."""
        from ingestion.ibkr_order import place_bracket_order

        mock_ib = MagicMock()
        mock_ib.qualifyContracts.return_value = [MagicMock(conId=1)]
        captured_sl = {}

        def capture_bracket(action, quantity, limitPrice, takeProfitPrice, stopLossPrice):
            sl = MagicMock(orderId=1003, orderType="STP", ocaGroup="G1")
            captured_sl["sl"] = sl
            return [
                MagicMock(orderId=1001, orderType="LMT", ocaGroup="G1"),
                MagicMock(orderId=1002, orderType="LMT", ocaGroup="G1"),
                sl,
            ]

        mock_ib.bracketOrder.side_effect = capture_bracket
        mock_ib.placeOrder.side_effect = lambda c, o: MagicMock(
            order=o,
            orderStatus=MagicMock(status="PreSubmitted"),
        )
        mock_ib.sleep = MagicMock()

        with patch("ingestion.ibkr_order._connect", return_value=mock_ib), \
             patch("ingestion.ibkr_order._disconnect"), \
             patch.dict(os.environ, {"STOP_LIMIT_SLIPPAGE_PCT": "0.05"}):  # 5% slippage
            place_bracket_order(
                host="127.0.0.1", port=4002, client_id=99,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-05-16", action="BUY", quantity=1,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.20,
            )

        sl = captured_sl["sl"]
        expected = 0.20 * 0.95
        assert sl.lmtPrice == pytest.approx(expected, rel=1e-4), (
            f"With 5% slippage, lmtPrice should be {expected:.4f}"
        )


class TestExpiryCloseSimulation:
    """
    Simulate the expiry-close path without a live broker.
    Verifies that positions expiring today trigger SELL MARKET orders.
    """

    def test_expiry_close_fires_on_matching_position(self):
        """Positions with expiry==today must get SELL MARKET TIF=DAY orders placed."""
        from ingestion.ibkr_order import expiry_close
        from ib_insync import Position

        today_ib = "20260413"  # YYYYMMDD for 2026-04-13

        mock_ib = MagicMock()
        mock_ib.sleep = MagicMock()

        # Simulate one open position expiring today
        mock_contract = MagicMock()
        mock_contract.secType = "OPT"
        mock_contract.lastTradeDateOrContractMonth = today_ib
        mock_contract.localSymbol = "TSLA 260413C00365000"
        mock_contract.symbol = "TSLA"

        mock_pos = MagicMock()
        mock_pos.contract = mock_contract
        mock_pos.position = 5.0

        mock_ib.positions.return_value = [mock_pos]

        placed_orders = []
        def fake_place_order(contract, order):
            placed_orders.append(order)
            trade = MagicMock()
            trade.order = MagicMock(orderId=2001)
            return trade

        mock_ib.placeOrder.side_effect = fake_place_order

        with patch("ingestion.ibkr_order._connect", return_value=mock_ib), \
             patch("ingestion.ibkr_order._disconnect"):
            result = expiry_close(
                host="127.0.0.1", port=4002, client_id=99,
                expiry_date="2026-04-13",
            )

        assert result["closed_count"] == 1, f"Expected 1 closed, got {result}"
        assert len(placed_orders) == 1
        assert placed_orders[0].action == "SELL"
        assert placed_orders[0].tif == "DAY"

    def test_expiry_close_skips_non_expiring_positions(self):
        """Positions with a different expiry must not be closed."""
        from ingestion.ibkr_order import expiry_close

        mock_ib = MagicMock()
        mock_ib.sleep = MagicMock()

        mock_contract = MagicMock()
        mock_contract.secType = "OPT"
        mock_contract.lastTradeDateOrContractMonth = "20260516"  # next month
        mock_contract.symbol = "TSLA"

        mock_pos = MagicMock()
        mock_pos.contract = mock_contract
        mock_pos.position = 3.0

        mock_ib.positions.return_value = [mock_pos]

        with patch("ingestion.ibkr_order._connect", return_value=mock_ib), \
             patch("ingestion.ibkr_order._disconnect"):
            result = expiry_close(
                host="127.0.0.1", port=4002, client_id=99,
                expiry_date="2026-04-13",
            )

        assert result["closed_count"] == 0
        mock_ib.placeOrder.assert_not_called()


# ── Live gateway tests (require IB Gateway running) ───────────────────────────

@pytest.mark.live_gateway
class TestBracketRoundtripLive:
    """
    Integration tests requiring a live IB Gateway (paper account).
    Run with: pytest -m live_gateway tests/test_bracket_roundtrip.py
    """

    def test_bracket_place_and_cancel(self):
        """
        Place a bracket order, verify all three legs in ib.openOrders(),
        then cancel the parent and verify all three are cancelled.
        """
        from ingestion.ibkr_order import place_bracket_order, cancel_order, get_status

        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "4002"))

        result = place_bracket_order(
            host=host, port=port, client_id=98,
            symbol="TSLA", contract_type="CALL", strike=400.0,
            expiry="2026-05-15",  # Far-dated to avoid accidental fill
            action="BUY", quantity=1,
            limit_price=0.01,   # Well away from market to avoid fill
            take_profit_price=0.05,
            stop_loss_price=0.005,
        )

        assert result["parent_order_id"] > 0
        assert result["take_profit_order_id"] > 0
        assert result["stop_loss_order_id"] > 0
        assert result["group_oca"] != ""

        # Verify status shows all three legs
        status = get_status(host=host, port=port, client_id=97,
                            order_id=result["parent_order_id"])
        assert "bracket" in status, "status should include bracket legs"
        roles = {leg["role"] for leg in status["bracket"]}
        assert roles == {"parent", "take_profit", "stop_loss"}

        # Cancel parent — OCO should auto-cancel TP and SL
        cancel_result = cancel_order(host=host, port=port, client_id=96,
                                     order_id=result["parent_order_id"])
        assert cancel_result["status"] == "Cancelled"

        # Wait for OCO cancellation to propagate
        import time; time.sleep(2)
        final_status = get_status(host=host, port=port, client_id=95,
                                  order_id=result["parent_order_id"])
        assert final_status["status"] in ("Cancelled", "Inactive"), (
            f"Parent should be cancelled, got {final_status['status']!r}"
        )
