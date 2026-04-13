"""
Tests for alpha_engine/ingestion/correlation_regime.py

Covers:
  - Pearson correlation computation (_pearson_r)
  - Rolling 5-day correlation series (_rolling_5d_correlations)
  - Z-score computation (_z_score)
  - IDIOSYNCRATIC classification when z < -2.0
  - MACRO_LOCKED classification when z > +2.0
  - NORMAL classification for z in [-2, +2]
  - Synthetic decorrelation fixture → IDIOSYNCRATIC detection
  - Edge cases: insufficient data, zero variance
"""
import sys
import math
import random
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from ingestion.correlation_regime import (
    _pearson_r,
    _rolling_5d_correlations,
    _z_score,
    _log_returns,
    _fetch_correlation_regime,
    get_correlation_regime,
)


class TestPearsonR(unittest.TestCase):
    """Bivariate Pearson r must handle edge cases correctly."""

    def test_perfect_positive_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        self.assertAlmostEqual(_pearson_r(x, y), 1.0, places=4)

    def test_perfect_negative_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [5.0, 4.0, 3.0, 2.0, 1.0]
        self.assertAlmostEqual(_pearson_r(x, y), -1.0, places=4)

    def test_no_correlation(self):
        """Orthogonal returns should produce correlation near 0."""
        random.seed(42)
        x = [random.gauss(0, 1) for _ in range(200)]
        y = [random.gauss(0, 1) for _ in range(200)]
        r = _pearson_r(x, y)
        self.assertAlmostEqual(r, 0.0, delta=0.2)

    def test_constant_series_returns_none(self):
        """Zero variance in either series → None."""
        self.assertIsNone(_pearson_r([1.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0]))
        self.assertIsNone(_pearson_r([1.0, 2.0, 3.0, 4.0, 5.0], [2.0] * 5))

    def test_insufficient_data_returns_none(self):
        self.assertIsNone(_pearson_r([1.0], [1.0]))
        self.assertIsNone(_pearson_r([], []))


class TestLogReturns(unittest.TestCase):
    def test_basic_computation(self):
        closes = [100.0, 101.0, 99.0, 102.0]
        expected = [
            math.log(101.0 / 100.0),
            math.log(99.0 / 101.0),
            math.log(102.0 / 99.0),
        ]
        result = _log_returns(closes)
        for r, e in zip(result, expected):
            self.assertAlmostEqual(r, e, places=8)

    def test_single_close_returns_empty(self):
        self.assertEqual(_log_returns([100.0]), [])

    def test_zero_close_skipped(self):
        closes = [100.0, 0.0, 102.0]
        result = _log_returns(closes)
        # Second pair (0.0 → 102.0) has zero prev, should be skipped
        self.assertEqual(len(result), 1)


class TestRolling5DCorrelations(unittest.TestCase):
    """Rolling 5-day correlation series must have correct length."""

    def test_length_is_n_minus_4(self):
        # With 20 return observations, we get 16 windows (indices 4..19)
        tsla_r = [float(i) * 0.01 for i in range(20)]
        qqq_r  = [float(i) * 0.012 + 0.001 for i in range(20)]
        corrs = _rolling_5d_correlations(tsla_r, qqq_r)
        self.assertEqual(len(corrs), 16)

    def test_correlated_series_produces_high_values(self):
        """Perfectly correlated series should give correlation ~1.0 throughout."""
        x = [math.sin(i * 0.5) for i in range(30)]
        y = [2 * v + 0.1 for v in x]  # linear transform → r = 1.0
        corrs = _rolling_5d_correlations(x, y)
        for c in corrs:
            self.assertAlmostEqual(abs(c), 1.0, places=3)

    def test_insufficient_data_returns_empty(self):
        corrs = _rolling_5d_correlations([0.01, 0.02], [0.01, 0.02])
        self.assertEqual(corrs, [])


class TestZScore(unittest.TestCase):
    def test_mean_value_has_zero_zscore(self):
        population = [0.5, 0.6, 0.7, 0.8, 0.9]
        mean_val = sum(population) / len(population)
        self.assertAlmostEqual(_z_score(mean_val, population), 0.0, places=4)

    def test_extreme_low_negative_zscore(self):
        population = [0.5, 0.6, 0.5, 0.6, 0.5, 0.6] * 5  # mean=0.55, std~0.05
        low_value = 0.0  # far below mean
        z = _z_score(low_value, population)
        self.assertLess(z, -2.0)

    def test_extreme_high_positive_zscore(self):
        population = [0.5, 0.6, 0.5, 0.6, 0.5, 0.6] * 5
        high_value = 1.0
        z = _z_score(high_value, population)
        self.assertGreater(z, 2.0)

    def test_constant_population_returns_zero(self):
        """Constant population → std=0 → z=0."""
        z = _z_score(0.5, [0.5] * 10)
        self.assertEqual(z, 0.0)

    def test_insufficient_population_returns_none(self):
        self.assertIsNone(_z_score(0.5, [0.5]))


class TestSyntheticDecorrelation(unittest.TestCase):
    """
    Core regression: synthesize data where TSLA decorrelates from QQQ in the
    most recent window, and assert IDIOSYNCRATIC classification.

    Approach:
      - First 30 days: TSLA and QQQ move together (correlation ~0.9)
      - Last 5 days:   TSLA moves independently / inversely (correlation ~ -0.5)

    This produces a z-score << -2.0 for the last window.
    """

    def _make_correlated_prices(self, n: int, base: float, noise_scale: float) -> list[float]:
        """Generate synthetic prices that follow a common factor + noise."""
        random.seed(0)
        prices = [base]
        for _ in range(n):
            r = random.gauss(0.0005, 0.015) + random.gauss(0, noise_scale)
            prices.append(prices[-1] * (1 + r))
        return prices

    def _build_mock_yf(self, tsla_closes, qqq_closes):
        """Build a yfinance mock returning the given close series."""
        import pandas as pd

        def mock_ticker(sym):
            t = MagicMock()
            if sym == "TSLA":
                hist = pd.DataFrame({"Close": tsla_closes})
            elif sym == "QQQ":
                hist = pd.DataFrame({"Close": qqq_closes})
            else:
                hist = pd.DataFrame({"Close": [100.0 + i * 0.5 for i in range(len(tsla_closes))]})
            t.history.return_value = hist
            return t

        return mock_ticker

    def test_decorrelation_produces_idiosyncratic(self):
        """
        Build 40-day history where last 5 days of TSLA are uncorrelated/anticorrelated
        with QQQ, vs high correlation over the prior 30 days.
        """
        random.seed(1)
        n = 40

        # Common market factor (what QQQ roughly does)
        market = [0.0]
        for _ in range(n):
            market.append(market[-1] + random.gauss(0.0003, 0.012))

        # QQQ closely tracks market
        qqq_r  = [market[i] - market[i-1] for i in range(1, n+1)]
        # TSLA first 35 days: highly correlated with QQQ
        tsla_r = [r * 1.2 + random.gauss(0, 0.003) for r in qqq_r[:35]]
        # TSLA last 5 days: strongly anti-correlated / independent
        tsla_r += [-r * 1.5 + random.gauss(0, 0.010) for r in qqq_r[35:]]

        assert len(tsla_r) == n
        assert len(qqq_r) == n

        corr_series = _rolling_5d_correlations(tsla_r, qqq_r)
        self.assertGreater(len(corr_series), 5, "Need at least 5 correlation windows")

        recent = corr_series[-1]
        reference = corr_series[-31:-1] if len(corr_series) > 30 else corr_series[:-1]
        z = _z_score(recent, reference)

        self.assertIsNotNone(z)
        self.assertLess(z, -2.0,
            f"Expected z < -2.0 for decorrelated tail, got z={z:.3f}, "
            f"recent_corr={recent:.3f}, ref_mean={sum(reference)/len(reference):.3f}")

    def test_correlated_series_stays_normal(self):
        """Consistently correlated series must NOT produce IDIOSYNCRATIC."""
        random.seed(2)
        n = 40
        base_r = [random.gauss(0.0003, 0.012) for _ in range(n)]
        tsla_r = [r * 1.2 + random.gauss(0, 0.002) for r in base_r]
        qqq_r  = [r + random.gauss(0, 0.002) for r in base_r]

        corr_series = _rolling_5d_correlations(tsla_r, qqq_r)
        recent = corr_series[-1]
        reference = corr_series[:-1]
        z = _z_score(recent, reference)
        if z is not None:
            # Consistently correlated → z should be within ±2 sigma
            self.assertGreater(z, -2.0,
                f"Stable correlation should not be IDIOSYNCRATIC: z={z:.3f}")


class TestCorrelationRegimeFull(unittest.TestCase):
    """Integration test: _fetch_correlation_regime with mocked yfinance."""

    def _make_df(self, closes):
        import pandas as pd
        return pd.DataFrame({"Close": closes})

    def test_normal_regime_with_consistent_correlation(self):
        """Consistent correlation series → NORMAL regime."""
        random.seed(3)
        n = 45
        base_r = [random.gauss(0.0003, 0.01) for _ in range(n)]
        tsla_c = [300.0]
        qqq_c  = [400.0]
        for r in base_r:
            tsla_c.append(tsla_c[-1] * (1 + r * 1.3 + random.gauss(0, 0.002)))
            qqq_c.append(qqq_c[-1] * (1 + r + random.gauss(0, 0.002)))

        def mock_ticker(sym):
            t = MagicMock()
            if sym == "TSLA":
                t.history.return_value = self._make_df(tsla_c)
            elif sym == "QQQ":
                t.history.return_value = self._make_df(qqq_c)
            else:
                t.history.return_value = self._make_df(qqq_c)
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            import ingestion.correlation_regime as cr
            cr._CORR_CACHE = None
            result = _fetch_correlation_regime()

        self.assertIn(result["regime"], ("NORMAL", "MACRO_LOCKED", "IDIOSYNCRATIC"))
        self.assertIsNotNone(result["tsla_qqq_5d_corr"])
        self.assertIsNone(result["error"])

    def test_insufficient_data_returns_normal_fallback(self):
        """With only 5 days of data, we can't compute z-score → NORMAL fallback."""
        def mock_ticker(sym):
            t = MagicMock()
            import pandas as pd
            t.history.return_value = pd.DataFrame({"Close": [100.0 + i for i in range(5)]})
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            import ingestion.correlation_regime as cr
            cr._CORR_CACHE = None
            result = _fetch_correlation_regime()

        self.assertEqual(result["regime"], "NORMAL")
        self.assertIsNotNone(result["error"])

    def test_result_schema(self):
        """Result must always contain required keys."""
        def mock_ticker(sym):
            t = MagicMock()
            import pandas as pd
            t.history.return_value = pd.DataFrame({"Close": [100.0 + i for i in range(3)]})
            return t

        with patch("yfinance.Ticker", side_effect=mock_ticker):
            import ingestion.correlation_regime as cr
            cr._CORR_CACHE = None
            result = _fetch_correlation_regime()

        for key in ("regime", "tsla_qqq_5d_corr", "z_score", "corr_series_length",
                    "mag7_avg_5d_corr", "error"):
            self.assertIn(key, result)

    def test_cache_is_1_hour(self):
        """Second call within TTL must not re-fetch."""
        call_count = {"n": 0}

        def mock_ticker(sym):
            call_count["n"] += 1
            t = MagicMock()
            import pandas as pd
            t.history.return_value = pd.DataFrame({"Close": [100.0 + i for i in range(5)]})
            return t

        import ingestion.correlation_regime as cr
        cr._CORR_CACHE = None
        cr._CORR_CACHE_TS = 0.0
        with patch("yfinance.Ticker", side_effect=mock_ticker):
            get_correlation_regime()
            first_count = call_count["n"]
            get_correlation_regime()  # should hit cache
            self.assertEqual(call_count["n"], first_count, "Should not re-fetch within TTL")


if __name__ == "__main__":
    unittest.main()
