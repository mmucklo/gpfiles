"""
Phase 17 — Unit tests for realtime_bars module.
Tests: ATR computation, volume ratio, bar window management, backfill.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Patch SQLite path and TRADIER_API_TOKEN before import
os.environ.setdefault("TRADIER_API_TOKEN", "test-token")

from ingestion.realtime_bars import (
    _compute_atr,
    _compute_indicators,
    REALTIME_BAR_WINDOW,
    ATR_PERIOD,
)


def _make_bar(close, high=None, low=None, volume=100):
    return {
        "ts": "2026-04-17 10:00",
        "open": close,
        "high": high if high is not None else close + 0.5,
        "low": low if low is not None else close - 0.5,
        "close": close,
        "volume": volume,
        "vwap": close,
    }


class TestATRComputation:
    def test_empty_bars_returns_zero(self):
        assert _compute_atr([], 14) == 0.0

    def test_single_bar_returns_zero(self):
        bars = [_make_bar(100.0)]
        assert _compute_atr(bars, 14) == 0.0

    def test_two_bars_returns_single_tr(self):
        bars = [
            {"ts": "t1", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100, "vwap": 101},
            {"ts": "t2", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 100, "vwap": 102},
        ]
        # TR for bar2: max(103-100, |103-101|, |100-101|) = max(3, 2, 1) = 3
        atr = _compute_atr(bars, 14)
        assert atr == pytest.approx(3.0)

    def test_atr_uses_last_period_bars(self):
        # 20 bars, period=14 — ATR should only use last 14 TRs
        bars = []
        for i in range(20):
            bars.append({
                "ts": f"t{i}",
                "open": 100, "high": 101, "low": 99,
                "close": 100, "volume": 100, "vwap": 100,
            })
        atr = _compute_atr(bars, 14)
        # Each TR = max(2, 1, 1) = 2 → ATR = 2.0
        assert atr == pytest.approx(2.0)

    def test_atr_period_shorter_than_window(self):
        bars = [_make_bar(100.0, high=102.0, low=99.0) for _ in range(5)]
        atr = _compute_atr(bars, 3)
        assert atr > 0


class TestVolumeRatio:
    def test_volume_ratio_equal_volume(self):
        bars = [_make_bar(100.0, volume=200) for _ in range(5)]
        indicators = _compute_indicators(bars)
        assert indicators["volume_ratio"] == pytest.approx(1.0)

    def test_volume_expansion_detected(self):
        bars = [_make_bar(100.0, volume=100) for _ in range(19)]
        bars.append(_make_bar(100.0, volume=300))  # last bar 3× volume
        indicators = _compute_indicators(bars)
        # avg vol ≈ (19×100 + 300) / 20 = 110, last bar = 300 → ratio ≈ 2.73
        assert indicators["volume_ratio"] > 2.0

    def test_volume_ratio_zero_avg_no_crash(self):
        bars = [_make_bar(100.0, volume=0) for _ in range(5)]
        indicators = _compute_indicators(bars)
        assert "volume_ratio" in indicators


class TestBarWindowManagement:
    def test_empty_window_returns_zeros(self):
        indicators = _compute_indicators([])
        assert indicators["atr"] == 0.0
        assert indicators["bar_count"] == 0

    def test_window_keys_present(self):
        bars = [_make_bar(100.0 + i * 0.1) for i in range(5)]
        indicators = _compute_indicators(bars)
        assert set(indicators.keys()) >= {"atr", "volume_ratio", "vwap", "bar_range_vs_atr", "bar_count"}

    def test_vwap_equals_close_when_volume_zero(self):
        bars = [_make_bar(100.0, volume=0)]
        indicators = _compute_indicators(bars)
        # Should use bar's close/vwap fallback
        assert indicators["vwap"] == pytest.approx(100.0)

    def test_bar_count_matches_input(self):
        bars = [_make_bar(100.0) for _ in range(7)]
        indicators = _compute_indicators(bars)
        assert indicators["bar_count"] == 7

    def test_bar_range_vs_atr_positive(self):
        bars = [
            {"ts": f"t{i}", "open": 100, "high": 102, "low": 98, "close": 100, "volume": 100, "vwap": 100}
            for i in range(15)
        ]
        indicators = _compute_indicators(bars)
        assert indicators["bar_range_vs_atr"] > 0

    def test_vwap_weighted_correctly(self):
        # Two bars: bar1 close=100 vol=100, bar2 close=200 vol=300
        bars = [
            {"ts": "t1", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100, "vwap": 100},
            {"ts": "t2", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 300, "vwap": 200},
        ]
        indicators = _compute_indicators(bars)
        # VWAP = (100×100 + 200×300) / 400 = 70000/400 = 175
        assert indicators["vwap"] == pytest.approx(175.0)
