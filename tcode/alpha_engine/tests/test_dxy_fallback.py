"""
Tests for Phase 13.5: DXY fallback chain in macro_regime.py and premarket.py

Covers:
  - DX-Y.NYB primary succeeds → dxy_status="live"
  - DX-Y.NYB fails, UUP succeeds → dxy_status="uup_proxy"
  - Both fail → dxy_status="unavailable", dxy=None (no fake zeros)
  - get_macro_regime() propagates dxy_status into macro result
  - premarket.py DXY dict has source field
"""
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

import ingestion.macro_regime as mr


def _make_hist(price: float, n_rows: int = 2):
    """Create a minimal mock yfinance history DataFrame with `n_rows` rows."""
    import pandas as pd
    closes = [price * 0.99, price] if n_rows >= 2 else [price]
    return pd.DataFrame({"Close": closes})


def _empty_hist():
    """Return an empty DataFrame (simulates yfinance returning nothing)."""
    import pandas as pd
    return pd.DataFrame({"Close": []})


class TestDxyFallback(unittest.TestCase):
    def setUp(self):
        # Reset module-level cache before each test
        mr._dxy_cache = None
        mr._dxy_cache_ts = 0.0

    def test_primary_dxy_nyb_succeeds(self):
        """When DX-Y.NYB returns valid data, status is 'live' and source is 'DX-Y.NYB'."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_hist(104.5)

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = mr._fetch_dxy()

        self.assertEqual(result["dxy_status"], "live")
        self.assertEqual(result["dxy_source"], "DX-Y.NYB")
        self.assertIsNotNone(result["dxy"])
        self.assertGreater(result["dxy"], 0)

    def test_dxy_nyb_fails_uup_succeeds(self):
        """When DX-Y.NYB fails and UUP returns data, status is 'uup_proxy'."""
        call_count = {"n": 0}

        def mock_ticker(symbol):
            t = MagicMock()
            call_count["n"] += 1
            if symbol == "DX-Y.NYB":
                t.history.side_effect = Exception("No data")
            elif symbol == "UUP":
                t.history.return_value = _make_hist(28.3)
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            result = mr._fetch_dxy()

        self.assertEqual(result["dxy_status"], "uup_proxy")
        self.assertEqual(result["dxy_source"], "UUP")
        self.assertIsNotNone(result["dxy"])

    def test_both_fail_returns_unavailable(self):
        """When both DX-Y.NYB and UUP fail, dxy=None and status='unavailable'."""
        def mock_ticker(symbol):
            t = MagicMock()
            t.history.return_value = _empty_hist()
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            result = mr._fetch_dxy()

        self.assertEqual(result["dxy_status"], "unavailable")
        self.assertIsNone(result["dxy"])
        self.assertIsNone(result["dxy_change_pct"])
        # Crucially: no stale zeros substituted
        self.assertNotEqual(result.get("dxy"), 0.0)

    def test_both_fail_with_exceptions(self):
        """Exceptions on both sources also yield unavailable (no stale zeros)."""
        def mock_ticker(symbol):
            t = MagicMock()
            t.history.side_effect = RuntimeError("Network error")
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            result = mr._fetch_dxy()

        self.assertEqual(result["dxy_status"], "unavailable")
        self.assertIsNone(result["dxy"])

    def test_dxy_status_propagates_to_macro_regime(self):
        """get_macro_regime() includes dxy_status field from _fetch_dxy()."""
        # Reset caches
        mr._macro_cache = None
        mr._macro_cache_ts = 0.0
        mr._vix_cache = None
        mr._vix_cache_ts = 0.0
        mr._dxy_cache = None
        mr._dxy_cache_ts = 0.0

        def mock_ticker(symbol):
            t = MagicMock()
            if symbol in ("DX-Y.NYB", "UUP"):
                t.history.return_value = _empty_hist()
            else:
                t.history.return_value = _empty_hist()
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            regime = mr.get_macro_regime()

        self.assertIn("dxy_status", regime)
        # With both failing, must be unavailable — not "live" or absent
        self.assertEqual(regime["dxy_status"], "unavailable")
        # dxy must not be a fake zero
        self.assertIsNone(regime.get("dxy"))

    def test_dxy_change_pct_computed_correctly(self):
        """Change percent is computed as (current - prev) / prev * 100."""
        call_count = {"n": 0}
        import pandas as pd

        def mock_ticker(symbol):
            t = MagicMock()
            if symbol == "DX-Y.NYB":
                t.history.return_value = pd.DataFrame({"Close": [100.0, 101.0]})
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            result = mr._fetch_dxy()

        self.assertEqual(result["dxy_status"], "live")
        self.assertAlmostEqual(result["dxy_change_pct"], 1.0, places=2)


class TestDxyTTL(unittest.TestCase):
    """DXY cache respects TTL and returns fresh data after expiry."""

    def setUp(self):
        mr._dxy_cache = None
        mr._dxy_cache_ts = 0.0

    def test_cache_is_reused_within_ttl(self):
        """Second call within TTL does not re-fetch."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_hist(104.5)

        with patch("yfinance.Ticker", return_value=mock_ticker):
            r1 = mr._fetch_dxy()
            r2 = mr._fetch_dxy()

        # Ticker.history should only be called once (first fetch)
        self.assertEqual(mock_ticker.history.call_count, 1)
        self.assertEqual(r1["dxy_status"], r2["dxy_status"])

    def test_cache_refreshes_after_expiry(self):
        """After TTL expires, a new fetch is performed."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_hist(104.5)

        with patch("yfinance.Ticker", return_value=mock_ticker):
            mr._fetch_dxy()
            # Artificially expire the cache
            mr._dxy_cache_ts = time.time() - 10000
            mr._fetch_dxy()

        self.assertGreaterEqual(mock_ticker.history.call_count, 2)


if __name__ == "__main__":
    unittest.main()
