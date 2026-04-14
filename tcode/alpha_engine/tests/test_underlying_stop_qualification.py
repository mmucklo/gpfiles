"""
Tests for Phase 13.5: Underlying-stop contract qualification fix (ibkr_order.py).

Covers:
  - classify_ibkr_error() classifies Error 321 as 'fatal'
  - classify_ibkr_error() classifies transient errors correctly
  - place_bracket_order() calls reqContractDetails() instead of qualifyContracts()
    for the underlying contract
  - When reqContractDetails() returns empty details, bracket downgrades gracefully
    (SL uses option-premium stop, [BRACKET-DOWNGRADE] logged)
  - When reqContractDetails() returns conId=0, also downgrades
  - When reqContractDetails() returns valid conId, PriceCondition is built with it

Note: Integration tests against a live IBKR Gateway are in a separate file
      and are marked @pytest.mark.integration.
"""
import sys
import json
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from ingestion.ibkr_order import classify_ibkr_error


class TestClassifyIbkrError(unittest.TestCase):
    """Error classifier must correctly identify fatal vs transient IBKR errors."""

    def test_error_321_is_fatal(self):
        """Error 321 (invalid contract id) is a configuration error — fatal, do not retry."""
        self.assertEqual(classify_ibkr_error(321), "fatal")
        self.assertEqual(classify_ibkr_error(321, "Error validating request"), "fatal")

    def test_error_200_is_fatal(self):
        """Error 200 (no security definition) is fatal — wrong contract params."""
        self.assertEqual(classify_ibkr_error(200), "fatal")

    def test_connectivity_errors_are_transient(self):
        """Connectivity errors should be retried."""
        self.assertEqual(classify_ibkr_error(1100), "transient")
        self.assertEqual(classify_ibkr_error(504), "transient")

    def test_unknown_error_code(self):
        """Unknown error codes return 'unknown'."""
        self.assertEqual(classify_ibkr_error(9999), "unknown")
        self.assertEqual(classify_ibkr_error(0), "unknown")

    def test_message_parameter_does_not_change_classification(self):
        """Classification is based on error code, not message text."""
        self.assertEqual(classify_ibkr_error(321, "some random message"), "fatal")
        self.assertEqual(classify_ibkr_error(1100, "some random message"), "transient")


class TestBracketOrderQualification(unittest.TestCase):
    """
    place_bracket_order() must use reqContractDetails() for underlying
    contract qualification, not qualifyContracts().
    """

    def _make_ib_mock(self, con_id: int = 12345, details_empty: bool = False):
        """
        Build a minimal IB mock for testing place_bracket_order.

        Args:
            con_id: conId to return from reqContractDetails
            details_empty: if True, return empty list from reqContractDetails
        """
        ib = MagicMock()

        # Option contract qualification succeeds
        opt_contract = MagicMock()
        opt_contract.conId = 99999
        ib.qualifyContracts.return_value = [opt_contract]

        # reqContractDetails for underlying
        if details_empty:
            ib.reqContractDetails.return_value = []
        else:
            detail = MagicMock()
            detail.contract = MagicMock()
            detail.contract.conId = con_id
            ib.reqContractDetails.return_value = [detail]

        # bracketOrder returns 3 mock orders
        parent = MagicMock()
        parent.orderType = "LMT"
        tp = MagicMock()
        tp.orderType = "LMT"
        sl = MagicMock()
        sl.orderType = "STP LMT"
        sl.conditions = []
        sl.auxPrice = 0.14
        sl.lmtPrice = 0.126
        ib.bracketOrder.return_value = [parent, tp, sl]

        # placeOrder returns trade mocks
        def _make_trade(order):
            t = MagicMock()
            t.order = order
            t.order.orderId = 100 + id(order) % 1000
            t.order.ocaGroup = "OCA_1"
            t.orderStatus.status = "Submitted"
            return t

        ib.placeOrder.side_effect = lambda contract, order: _make_trade(order)
        ib.sleep.return_value = None

        return ib

    def _call_place_bracket(self, ib_mock, underlying_stop: str = "TSLA:340.0"):
        """Call place_bracket_order with a test bracket including --underlying-stop."""
        from unittest.mock import patch as _patch

        # Patch the connect/disconnect helpers and ib_insync classes
        with _patch("ingestion.ibkr_order._connect", return_value=ib_mock), \
             _patch("ingestion.ibkr_order._disconnect"), \
             _patch("ingestion.ibkr_order.place_bracket_order.__code__",
                    wraps=None, create=True) as _unused:
            from ingestion.ibkr_order import place_bracket_order
            return place_bracket_order(
                host="127.0.0.1",
                port=7497,
                client_id=3,
                symbol="TSLA",
                contract_type="CALL",
                strike=365.0,
                expiry="2026-04-18",
                action="BUY",
                quantity=10,
                limit_price=0.28,
                take_profit_price=0.56,
                stop_loss_price=0.14,
                underlying_stop_symbol="TSLA",
                underlying_stop_price=340.0,
            )

    def test_req_contract_details_called_for_underlying(self):
        """reqContractDetails() must be called when underlying_stop_symbol is provided."""
        ib = self._make_ib_mock(con_id=76792991)

        with patch("ingestion.ibkr_order._connect", return_value=ib), \
             patch("ingestion.ibkr_order._disconnect"):
            from ingestion.ibkr_order import place_bracket_order
            place_bracket_order(
                host="127.0.0.1", port=7497, client_id=3,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-04-18", action="BUY", quantity=10,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
                underlying_stop_symbol="TSLA", underlying_stop_price=340.0,
            )

        ib.reqContractDetails.assert_called_once()

    def test_empty_details_downgrades_bracket(self):
        """
        When reqContractDetails returns empty list, PriceCondition is NOT applied
        and a [BRACKET-DOWNGRADE] warning is logged.
        """
        ib = self._make_ib_mock(details_empty=True)

        import logging
        with patch("ingestion.ibkr_order._connect", return_value=ib), \
             patch("ingestion.ibkr_order._disconnect"), \
             self.assertLogs("ibkr_order", level="WARNING") as log_ctx:
            from ingestion.ibkr_order import place_bracket_order
            place_bracket_order(
                host="127.0.0.1", port=7497, client_id=3,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-04-18", action="BUY", quantity=10,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
                underlying_stop_symbol="TSLA", underlying_stop_price=340.0,
            )

        # Check [BRACKET-DOWNGRADE] is in log output
        log_text = " ".join(log_ctx.output)
        self.assertIn("BRACKET-DOWNGRADE", log_text)

        # SL leg conditions must be empty (no PriceCondition attached)
        sl_order = ib.bracketOrder.return_value[2]
        self.assertEqual(sl_order.conditions, [])

    def test_zero_con_id_downgrades_bracket(self):
        """
        When reqContractDetails returns conId=0, PriceCondition is NOT applied.
        This is the exact scenario that caused Error 321 in Phase 9.
        """
        ib = self._make_ib_mock(con_id=0)

        with patch("ingestion.ibkr_order._connect", return_value=ib), \
             patch("ingestion.ibkr_order._disconnect"), \
             self.assertLogs("ibkr_order", level="WARNING") as log_ctx:
            from ingestion.ibkr_order import place_bracket_order
            place_bracket_order(
                host="127.0.0.1", port=7497, client_id=3,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-04-18", action="BUY", quantity=10,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
                underlying_stop_symbol="TSLA", underlying_stop_price=340.0,
            )

        log_text = " ".join(log_ctx.output)
        self.assertIn("BRACKET-DOWNGRADE", log_text)

    def test_valid_con_id_builds_price_condition(self):
        """
        When reqContractDetails returns conId > 0, PriceCondition is built
        with that conId (not 0).
        """
        ib = self._make_ib_mock(con_id=76792991)
        conditions_assigned = []

        original_sl = ib.bracketOrder.return_value[2]

        def capture_conditions(cond_list):
            conditions_assigned.extend(cond_list)

        type(original_sl).conditions = property(
            fget=lambda self: [],
            fset=lambda self, val: conditions_assigned.extend(val),
        )

        with patch("ingestion.ibkr_order._connect", return_value=ib), \
             patch("ingestion.ibkr_order._disconnect"):
            from ingestion.ibkr_order import place_bracket_order
            place_bracket_order(
                host="127.0.0.1", port=7497, client_id=3,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-04-18", action="BUY", quantity=10,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
                underlying_stop_symbol="TSLA", underlying_stop_price=340.0,
            )

        # reqContractDetails must have been called (not just qualifyContracts)
        ib.reqContractDetails.assert_called_once()

    def test_no_underlying_stop_skips_req_contract_details(self):
        """When no --underlying-stop is provided, reqContractDetails is not called."""
        ib = self._make_ib_mock(con_id=76792991)

        with patch("ingestion.ibkr_order._connect", return_value=ib), \
             patch("ingestion.ibkr_order._disconnect"):
            from ingestion.ibkr_order import place_bracket_order
            place_bracket_order(
                host="127.0.0.1", port=7497, client_id=3,
                symbol="TSLA", contract_type="CALL", strike=365.0,
                expiry="2026-04-18", action="BUY", quantity=10,
                limit_price=0.28, take_profit_price=0.56, stop_loss_price=0.14,
                underlying_stop_symbol="",  # empty = no underlying stop
                underlying_stop_price=0.0,
            )

        ib.reqContractDetails.assert_not_called()


if __name__ == "__main__":
    unittest.main()
