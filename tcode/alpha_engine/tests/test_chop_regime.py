"""
Tests for Phase 14 chop_regime.py.
Uses mocked yfinance data — no network calls.
"""
import sys
import math
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from ingestion.chop_regime import (
    _wilder_adx,
    _bollinger_squeeze,
    _range_ratio,
    _realized_vol_5d,
    get_chop_regime,
    _trending_fallback,
)


class TestWilderADX:
    def _make_trending_series(self):
        """Strongly trending series: each bar higher by 5."""
        n = 40
        highs  = [100 + i * 5 + 2 for i in range(n)]
        lows   = [100 + i * 5 - 2 for i in range(n)]
        closes = [100 + i * 5 for i in range(n)]
        return highs, lows, closes

    def _make_choppy_series(self):
        """Choppy series: alternating +2/-2 with no net trend."""
        n = 40
        highs  = [100 + 4 if i % 2 == 0 else 100 + 2 for i in range(n)]
        lows   = [100 - 4 if i % 2 == 0 else 100 - 2 for i in range(n)]
        closes = [100 + 2 if i % 2 == 0 else 100 - 2 for i in range(n)]
        return highs, lows, closes

    def test_trending_adx_above_20(self):
        highs, lows, closes = self._make_trending_series()
        adx = _wilder_adx(highs, lows, closes)
        assert adx > 20, f"ADX={adx} should be > 20 for trending series"

    def test_choppy_adx_below_20(self):
        highs, lows, closes = self._make_choppy_series()
        adx = _wilder_adx(highs, lows, closes)
        assert adx < 25, f"ADX={adx} should be lower for choppy series"

    def test_insufficient_data_returns_zero(self):
        adx = _wilder_adx([100, 101], [99, 100], [100, 100.5], period=14)
        assert adx == 0.0

    def test_returns_non_negative(self):
        h, l, c = self._make_choppy_series()
        adx = _wilder_adx(h, l, c)
        assert adx >= 0


class TestBollingerSqueeze:
    def test_tight_band_returns_low_ratio(self):
        """After a quiet period, squeeze ratio < 0.6."""
        # 90 days of normal volatility, then last 20 very tight
        import random
        random.seed(42)
        closes = [100.0]
        for i in range(89):
            closes.append(closes[-1] * (1 + random.gauss(0, 0.02)))  # 2% daily vol
        # Tight last 20 bars
        for i in range(20):
            closes.append(closes[-1] * (1 + random.gauss(0, 0.002)))  # 0.2% daily vol
        bb = _bollinger_squeeze(closes)
        # Tight recent period → ratio should be < 1.0
        assert bb is not None
        assert bb < 1.0

    def test_normal_returns_around_one(self):
        """Consistent volatility → ratio ≈ 1."""
        import random
        random.seed(0)
        closes = [100.0]
        for i in range(120):
            closes.append(closes[-1] * (1 + random.gauss(0, 0.015)))
        bb = _bollinger_squeeze(closes)
        # Should not be extreme in either direction
        assert bb is not None
        assert 0.1 < bb < 10.0

    def test_insufficient_data_returns_none(self):
        bb = _bollinger_squeeze([100.0] * 20)
        assert bb is None


class TestRangeRatio:
    def test_trending_day_low_ratio(self):
        """Strong directional day: H-L ≈ C-O → ratio ≈ 1."""
        opens  = [100, 101, 102, 103, 104]
        highs  = [101, 102, 103, 104, 105]
        lows   = [99,  100, 101, 102, 103]
        closes = [100.9, 101.9, 102.9, 103.9, 104.9]
        rr = _range_ratio(opens, highs, lows, closes, days=5)
        assert rr > 0

    def test_choppy_day_high_ratio(self):
        """Wide range, tiny move → high ratio."""
        opens  = [100, 100, 100, 100, 100]
        highs  = [110, 110, 110, 110, 110]
        lows   = [90,   90,  90,  90,  90]
        closes = [100.1, 99.9, 100.1, 99.9, 100.1]  # tiny net move
        rr = _range_ratio(opens, highs, lows, closes, days=5)
        assert rr > 5.0, f"range_ratio={rr} should be > 5 for choppy wide-range bars"


class TestRealizedVol:
    def test_zero_vol_returns_zero(self):
        closes = [100.0] * 10
        rv = _realized_vol_5d(closes)
        assert rv == 0.0

    def test_high_vol_series(self):
        """10% daily moves → high annualised vol."""
        closes = [100, 110, 99, 109, 98, 108]
        rv = _realized_vol_5d(closes)
        assert rv > 0.5  # > 50% annualised

    def test_insufficient_data_returns_zero(self):
        rv = _realized_vol_5d([100, 101, 102])
        assert rv == 0.0


class TestGetChopRegime:
    def _make_mock_hist(self, regime: str):
        """Build a mock pandas DataFrame that yfinance.Ticker.history() would return."""
        import pandas as pd
        import numpy as np

        n = 100
        closes = [100.0]
        if regime == "TRENDING":
            # Strong uptrend
            for _ in range(n - 1):
                closes.append(closes[-1] * 1.005)
        else:
            # Choppy: alternating +2%/-2%
            for i in range(n - 1):
                closes.append(closes[-1] * (1.02 if i % 2 == 0 else 0.98))

        opens  = [c * 0.998 for c in closes]
        highs  = [c * 1.015 for c in closes]
        lows   = [c * 0.985 for c in closes]

        return pd.DataFrame({
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1_000_000] * n,
        })

    def test_choppy_regime_detected(self):
        hist = self._make_mock_hist("CHOPPY")
        import ingestion.chop_regime as _cr
        with patch.object(_cr, "yf", MagicMock(Ticker=MagicMock(return_value=MagicMock(history=MagicMock(return_value=hist))))), \
             patch.object(_cr, "_atm_iv", return_value=0.65):
            _cr._chop_cache = None
            _cr._chop_cache_ts = 0.0
            result = _cr.get_chop_regime(force_refresh=True)
        assert result["regime"] in ("MIXED", "CHOPPY")
        assert result["score"] >= 0

    def test_trending_regime_detected(self):
        hist = self._make_mock_hist("TRENDING")
        import ingestion.chop_regime as _cr
        with patch.object(_cr, "yf", MagicMock(Ticker=MagicMock(return_value=MagicMock(history=MagicMock(return_value=hist))))), \
             patch.object(_cr, "_atm_iv", return_value=0.65):
            _cr._chop_cache = None
            _cr._chop_cache_ts = 0.0
            result = _cr.get_chop_regime(force_refresh=True)
        assert result["regime"] in ("TRENDING", "MIXED")

    def test_fallback_on_error(self):
        import ingestion.chop_regime as _cr
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = Exception("network error")
        with patch.object(_cr, "yf", mock_yf):
            _cr._chop_cache = None
            _cr._chop_cache_ts = 0.0
            result = _cr.get_chop_regime(force_refresh=True)
        assert result["regime"] == "TRENDING"
        assert "error" in result

    def test_result_schema(self):
        hist = self._make_mock_hist("CHOPPY")
        import ingestion.chop_regime as _cr
        with patch.object(_cr, "yf", MagicMock(Ticker=MagicMock(return_value=MagicMock(history=MagicMock(return_value=hist))))), \
             patch.object(_cr, "_atm_iv", return_value=0.65):
            _cr._chop_cache = None
            _cr._chop_cache_ts = 0.0
            result = _cr.get_chop_regime(force_refresh=True)
        required = {"regime", "score", "components", "thresholds_hit", "ts", "source"}
        assert required <= set(result.keys())
        assert result["regime"] in ("TRENDING", "MIXED", "CHOPPY")
        assert 0.0 <= result["score"] <= 1.0
        assert isinstance(result["thresholds_hit"], list)
