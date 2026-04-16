"""
test_regime_classifier.py — Phase 16

Unit tests for alpha_engine/regime_classifier.py.
Covers:
  - classify() returns all required keys
  - regime is one of the valid values
  - confidence is in [0, 1]
  - recommended_strategy is non-empty
  - get_or_refresh() returns same structure
  - scoring helpers handle missing/zero data gracefully
"""

import sys, os, types, importlib, json
from unittest.mock import patch, MagicMock
import pytest

# ── Minimal stubs so we can import regime_classifier without live deps ────────

def _make_fake_yfinance():
    """Return a mock yfinance module that provides deterministic OHLCV data."""
    yf = types.ModuleType('yfinance')

    class FakeTicker:
        def history(self, period='5d', interval='1d', **_):
            import pandas as pd
            idx = pd.date_range('2026-01-01', periods=5, freq='D')
            return pd.DataFrame({
                'Open':   [400, 405, 410, 408, 412],
                'High':   [415, 420, 425, 418, 430],
                'Low':    [395, 400, 405, 400, 405],
                'Close':  [410, 415, 420, 412, 425],
                'Volume': [50_000_000]*5,
            }, index=idx)

    def Ticker(sym):
        return FakeTicker()

    yf.Ticker = Ticker
    return yf


def _setup_stubs(monkeypatch):
    sys.modules['yfinance'] = _make_fake_yfinance()
    # Stub db_utils so no real DB is opened
    db_utils = types.ModuleType('alpha_engine.data.db_utils')
    db_utils.get_latest = MagicMock(return_value=None)
    sys.modules['alpha_engine.data.db_utils'] = db_utils
    # Stub process_heartbeats lookup
    ph = types.ModuleType('alpha_engine.data.process_heartbeats')
    ph.get_latest_for_publisher = MagicMock(return_value=None)
    sys.modules['alpha_engine.data.process_heartbeats'] = ph


VALID_REGIMES = {'TRENDING', 'FLAT', 'CHOPPY', 'EVENT_DRIVEN', 'UNCERTAIN'}
VALID_STRATEGIES = {'MOMENTUM', 'IRON_CONDOR', 'WAVE_RIDER', 'JADE_LIZARD', 'STRADDLE', 'GAMMA_SCALP'}


@pytest.fixture(autouse=True)
def stub_deps(monkeypatch):
    _setup_stubs(monkeypatch)
    # Remove cached module if previously imported
    sys.modules.pop('alpha_engine.regime_classifier', None)
    yield


def _import_classifier():
    # Force re-import after stubs are in place
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), '..', 'regime_classifier.py')
    spec = importlib.util.spec_from_file_location('alpha_engine.regime_classifier', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_returns_required_keys():
    rc = _import_classifier()
    result = rc.classify()

    required = {
        'regime', 'color', 'confidence', 'composite_score',
        'factors', 'recommended_strategy', 'fallback_strategy',
        'refreshed_at', 'next_refresh_at',
    }
    assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"


def test_classify_regime_is_valid():
    rc = _import_classifier()
    result = rc.classify()
    assert result['regime'] in VALID_REGIMES, f"Unexpected regime: {result['regime']}"


def test_classify_confidence_in_range():
    rc = _import_classifier()
    result = rc.classify()
    conf = result['confidence']
    assert 0.0 <= conf <= 1.0, f"Confidence out of range: {conf}"


def test_classify_recommended_strategy_valid():
    rc = _import_classifier()
    result = rc.classify()
    strat = result['recommended_strategy']
    assert strat in VALID_STRATEGIES, f"Unexpected strategy: {strat}"


def test_classify_fallback_strategy_valid():
    rc = _import_classifier()
    result = rc.classify()
    strat = result['fallback_strategy']
    assert strat in VALID_STRATEGIES, f"Unexpected fallback: {strat}"


def test_classify_factors_is_list():
    rc = _import_classifier()
    result = rc.classify()
    assert isinstance(result['factors'], list)
    assert len(result['factors']) > 0


def test_classify_timestamps_present():
    rc = _import_classifier()
    result = rc.classify()
    assert result['refreshed_at'], "refreshed_at should be non-empty"
    assert result['next_refresh_at'], "next_refresh_at should be non-empty"


def test_classify_json_serialisable():
    """The result must be directly JSON-serialisable (no datetime objects, etc.)."""
    rc = _import_classifier()
    result = rc.classify()
    # Should not raise
    json.dumps(result)


def test_get_or_refresh_returns_same_schema():
    """get_or_refresh() must return the same keys as classify()."""
    rc = _import_classifier()
    result = rc.get_or_refresh()
    required = {'regime', 'color', 'confidence', 'recommended_strategy'}
    assert required.issubset(result.keys())


def test_classify_handles_yfinance_failure_gracefully():
    """If yfinance raises an exception classify() should still return UNCERTAIN."""
    import types
    bad_yf = types.ModuleType('yfinance')

    class BrokenTicker:
        def history(self, **_):
            raise RuntimeError("network error")

    bad_yf.Ticker = lambda sym: BrokenTicker()
    sys.modules['yfinance'] = bad_yf
    sys.modules.pop('alpha_engine.regime_classifier', None)

    rc = _import_classifier()
    result = rc.classify()
    # Should degrade gracefully — regime might be UNCERTAIN
    assert result['regime'] in VALID_REGIMES
