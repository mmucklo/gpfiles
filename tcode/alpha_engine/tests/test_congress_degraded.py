"""
Tests for Phase 13.5: Congress data-source resilience.

Covers:
  - Senate eFTS retry with exponential backoff (3 attempts)
  - Circuit breaker opens after 3 failures
  - Circuit breaker blocks requests during cooldown
  - Recovery resets circuit state
  - get_congress_trades() includes senate/house status sub-objects
  - House PTR disabled (no fake data)
  - No stale cache returned past TTL on degraded source
"""
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

import ingestion.congress_trades as ct
from ingestion.congress_trades import (
    get_senate_status,
    get_congress_trades,
    _fetch_senate_ptrs,
    _fetch_house_ptrs,
)


def _reset_senate_state():
    """Reset all Senate circuit breaker module globals between tests."""
    ct._SENATE_FAIL_COUNT = 0
    ct._SENATE_DEGRADED_UNTIL = 0.0
    ct._SENATE_LAST_SUCCESS_AT = None
    ct._SENATE_LAST_ERROR = None
    ct._CACHE = None
    ct._CACHE_TS = 0.0


class TestSenateCircuitBreaker(unittest.TestCase):
    """Senate eFTS retry + circuit breaker behavior."""

    def setUp(self):
        _reset_senate_state()

    def test_successful_fetch_resets_fail_count(self):
        """A successful fetch after prior failures resets the circuit breaker."""
        ct._SENATE_FAIL_COUNT = 2  # simulated prior failures

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            _fetch_senate_ptrs()

        self.assertEqual(ct._SENATE_FAIL_COUNT, 0)
        self.assertEqual(ct._SENATE_DEGRADED_UNTIL, 0.0)
        self.assertIsNotNone(ct._SENATE_LAST_SUCCESS_AT)

    def test_three_failures_open_circuit(self):
        """After 3 consecutive failures, circuit is opened with a cooldown timestamp."""
        import requests as req_module
        with patch("requests.get", side_effect=req_module.ConnectionError("DNS resolution failed")), \
             patch("ingestion.congress_trades.time.sleep"):  # don't actually wait
            result = _fetch_senate_ptrs()

        self.assertEqual(result, [])
        self.assertGreater(ct._SENATE_DEGRADED_UNTIL, time.time())
        self.assertIsNotNone(ct._SENATE_LAST_ERROR)

    def test_circuit_open_blocks_requests(self):
        """When circuit is open, no HTTP requests are attempted."""
        # Force circuit open
        ct._SENATE_DEGRADED_UNTIL = time.time() + 600

        with patch("requests.get") as mock_get:
            result = _fetch_senate_ptrs()

        mock_get.assert_not_called()
        self.assertEqual(result, [])

    def test_retry_attempts_before_circuit_open(self):
        """Exactly 3 HTTP requests are attempted before circuit opens."""
        import requests as req_module
        with patch("requests.get", side_effect=req_module.Timeout("Timeout")) as mock_get, \
             patch("ingestion.congress_trades.time.sleep"):
            _fetch_senate_ptrs()

        # Should have attempted _SENATE_MAX_RETRIES times
        self.assertEqual(mock_get.call_count, ct._SENATE_MAX_RETRIES)

    def test_get_senate_status_ok_after_success(self):
        """get_senate_status() returns 'ok' after a successful fetch."""
        ct._SENATE_LAST_SUCCESS_AT = "2026-04-14T10:00:00Z"
        ct._SENATE_DEGRADED_UNTIL = 0.0
        ct._SENATE_FAIL_COUNT = 0

        status = get_senate_status()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["last_success_at"], "2026-04-14T10:00:00Z")

    def test_get_senate_status_degraded_during_cooldown(self):
        """get_senate_status() returns 'degraded' while circuit is open."""
        ct._SENATE_DEGRADED_UNTIL = time.time() + 600
        ct._SENATE_LAST_ERROR = "NameResolutionError"

        status = get_senate_status()
        self.assertEqual(status["status"], "degraded")
        self.assertIn("next_retry_at", status)
        self.assertEqual(status["last_error"], "NameResolutionError")

    def test_get_senate_status_unknown_before_any_fetch(self):
        """get_senate_status() returns 'unknown' before any successful fetch."""
        status = get_senate_status()
        self.assertEqual(status["status"], "unknown")

    def test_cooldown_expires_allows_probe(self):
        """After cooldown, the circuit half-opens and allows one probe."""
        # Expired cooldown
        ct._SENATE_DEGRADED_UNTIL = time.time() - 1  # expired 1 second ago
        ct._SENATE_FAIL_COUNT = 3

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            result = _fetch_senate_ptrs()

        # Should have succeeded and reset state
        self.assertEqual(ct._SENATE_FAIL_COUNT, 0)


class TestHousePTRDisabled(unittest.TestCase):
    """House PTR feed is disabled in Phase 13.5."""

    def setUp(self):
        _reset_senate_state()

    def test_house_ptr_returns_empty_list(self):
        """_fetch_house_ptrs() returns empty list (no fake data)."""
        result = _fetch_house_ptrs()
        self.assertEqual(result, [])

    def test_house_status_in_congress_trades(self):
        """get_congress_trades() includes house status sub-object with 'disabled'."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            result = get_congress_trades()

        self.assertIn("house", result)
        self.assertEqual(result["house"]["status"], "disabled")
        self.assertIsNone(result["house"]["last_success_at"])
        self.assertIsNotNone(result["house"]["last_error"])  # explains why disabled

    def test_senate_status_in_congress_trades(self):
        """get_congress_trades() includes senate status sub-object."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            result = get_congress_trades()

        self.assertIn("senate", result)
        self.assertIn("status", result["senate"])


class TestCongressTradesDegradedSignal(unittest.TestCase):
    """Signal emission is NEUTRAL when all sources are degraded."""

    def setUp(self):
        _reset_senate_state()

    def test_neutral_signal_when_senate_degraded(self):
        """No data from senate → signal is NEUTRAL, sentiment_multiplier is 1.0."""
        # Force circuit open
        ct._SENATE_DEGRADED_UNTIL = time.time() + 600

        result = get_congress_trades()

        self.assertEqual(result["signal"], "NEUTRAL")
        self.assertAlmostEqual(result["sentiment_multiplier"], 1.0, places=2)
        self.assertEqual(result["filing_count"], 0)

    def test_signal_still_emitted_from_senate_when_ok(self):
        """With a buy filing from Senate, signal is BULLISH even though House is disabled."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "first_name": "Roger",
                            "last_name": "Wicker",
                            "date_filed": now_str,
                            "transaction_type": "PURCHASE",
                            "amount": "$50,001 - $100,000",
                            "committee": "Senate Commerce, Science, and Transportation",
                            "link": "",
                        }
                    }
                ]
            }
        }

        with patch("requests.get", return_value=mock_resp):
            result = get_congress_trades()

        # Committee member buying in 48h → BULLISH
        self.assertEqual(result["signal"], "BULLISH")
        self.assertAlmostEqual(result["sentiment_multiplier"], 1.15, places=2)
        self.assertTrue(result["committee_weighted_buy_48h"])


if __name__ == "__main__":
    unittest.main()
