"""
Tests for ingestion/ibkr_account.py
Uses mocks to avoid requiring a live IB Gateway connection.
"""
import math
import unittest
from unittest.mock import MagicMock, patch


class TestGetAccountSummary(unittest.TestCase):
    def _mock_summary(self):
        items = [
            MagicMock(tag="NetLiquidation",      value="1000086.35", currency="USD"),
            MagicMock(tag="TotalCashValue",       value="1000000.0",  currency="USD"),
            MagicMock(tag="BuyingPower",           value="4000000.0",  currency="USD"),
            MagicMock(tag="UnrealizedPnL",         value="0.0",        currency="USD"),
            MagicMock(tag="RealizedPnL",           value="0.0",        currency="USD"),
            MagicMock(tag="EquityWithLoanValue",   value="1000000.0",  currency="USD"),
        ]
        return items

    def test_get_account_summary_shape(self):
        from ingestion.ibkr_account import get_account_summary
        mock_ib = MagicMock()
        mock_ib.accountSummary.return_value = self._mock_summary()

        with patch("ingestion.ibkr_account._get_ib", return_value=mock_ib):
            result = get_account_summary()

        required = ["net_liquidation", "cash_balance", "buying_power",
                    "unrealized_pnl", "realized_pnl", "equity_with_loan", "ts"]
        for key in required:
            self.assertIn(key, result, f"Missing key: {key}")

        self.assertAlmostEqual(result["net_liquidation"], 1000086.35, places=1)
        self.assertAlmostEqual(result["cash_balance"], 1000000.0, places=1)
        self.assertAlmostEqual(result["buying_power"], 4000000.0, places=1)

    def test_nan_values_coerced_to_zero(self):
        """NaN values from IBKR must be replaced with 0.0."""
        from ingestion.ibkr_account import get_account_summary
        items = [
            MagicMock(tag="NetLiquidation", value="nan", currency="USD"),
            MagicMock(tag="TotalCashValue", value="nan", currency="USD"),
        ]
        mock_ib = MagicMock()
        mock_ib.accountSummary.return_value = items

        with patch("ingestion.ibkr_account._get_ib", return_value=mock_ib):
            result = get_account_summary()

        self.assertEqual(result["net_liquidation"], 0.0)
        self.assertEqual(result["cash_balance"], 0.0)


class TestGetPositions(unittest.TestCase):
    def test_get_positions_shape(self):
        """Positions list must be a list of dicts with required keys."""
        from ingestion.ibkr_account import get_positions
        mock_ib = MagicMock()
        mock_ib.positions.return_value = []

        with patch("ingestion.ibkr_account._get_ib", return_value=mock_ib):
            result = get_positions()

        self.assertIsInstance(result, list)
        # Empty positions is valid
        self.assertEqual(result, [])

    def test_position_dict_has_signal_lookup_keys(self):
        """Non-empty position dicts must have signal_id and catalyst keys."""
        from ingestion.ibkr_account import get_positions
        from unittest.mock import MagicMock

        mock_contract = MagicMock()
        mock_contract.symbol = "TSLA"
        mock_contract.secType = "OPT"
        mock_contract.strike = 380.0
        mock_contract.right = "C"
        mock_contract.lastTradeDateOrContractMonth = "20260410"
        mock_contract.multiplier = 100

        mock_pos = MagicMock()
        mock_pos.contract = mock_contract
        mock_pos.position = 2
        mock_pos.avgCost = 3.50

        mock_ticker = MagicMock()
        mock_ticker.last = 4.20
        mock_ticker.close = 4.20
        mock_ticker.bid = 4.10
        mock_ticker.ask = 4.30

        mock_ib = MagicMock()
        mock_ib.positions.return_value = [mock_pos]
        mock_ib.reqMktData.return_value = mock_ticker
        mock_ib.sleep.return_value = None
        mock_ib.cancelMktData.return_value = None

        with patch("ingestion.ibkr_account._get_ib", return_value=mock_ib):
            result = get_positions()

        self.assertEqual(len(result), 1)
        pos = result[0]
        for key in ("ticker", "qty", "avg_cost", "current_price", "unrealized_pnl",
                    "market_value", "option_type", "strike", "expiration",
                    "signal_id", "catalyst", "model_id"):
            self.assertIn(key, pos, f"Missing key: {key}")
        self.assertEqual(pos["ticker"], "TSLA")
        self.assertEqual(pos["strike"], 380)
        self.assertEqual(pos["option_type"], "CALL")


class TestGetFills(unittest.TestCase):
    def setUp(self):
        # Ensure a fresh event loop exists (ib_insync uses eventkit which needs one)
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def test_fills_schema(self):
        from ingestion.ibkr_account import get_fills
        mock_ib = MagicMock()
        mock_ib.reqExecutions.return_value = []

        with patch("ingestion.ibkr_account._get_ib", return_value=mock_ib), \
             patch("ib_insync.ExecutionFilter", MagicMock()):
            result = get_fills(24)

        self.assertIsInstance(result, list)
        # Empty fills is valid when no executions
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
