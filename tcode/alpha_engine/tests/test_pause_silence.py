"""
test_pause_silence.py — Phase 21: zero network calls when paused.

Verifies that decorated ingestion entry points make ZERO external calls
(requests.get, yfinance, etc.) when the system is paused.

Strategy: force is_paused() to return True via monkeypatching, then assert
that each entry-point function returns None without touching the network.
"""
from __future__ import annotations

import json
import os
import sys
import importlib

import pytest

# Ensure alpha_engine root is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ingestion")))


def _force_paused(monkeypatch, tmp_path):
    """Write a paused state file and point PUBLISHER_PAUSE_STATE_FILE at it."""
    pf = tmp_path / "pause.json"
    pf.write_text(json.dumps({"paused": True, "unpause_until": None}))
    monkeypatch.setenv("PUBLISHER_PAUSE_STATE_FILE", str(pf))
    # Reset the pause_guard cache so the new file is read.
    import pause_guard as pg
    pg._cache_expires_at = 0.0  # invalidate TTL cache
    return str(pf)


class TestCatalystTrackerSilence:
    def test_get_catalyst_intel_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        # Stub requests so any accidental call is detected
        request_calls = []
        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=lambda *a, **kw: request_calls.append(a) or mock.MagicMock(json=lambda: {})):
            import ingestion.catalyst_tracker as ct
            result = ct.get_catalyst_intel()

        assert result is None, f"Expected None when paused, got {result!r}"
        assert request_calls == [], f"requests.get was called {len(request_calls)} time(s) while paused"


class TestEVSectorSilence:
    def test_get_ev_sector_intel_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=AssertionError("requests.get called during pause")):
            import ingestion.ev_sector as ev
            result = ev.get_ev_sector_intel()

        assert result is None


class TestInstitutionalSilence:
    def test_get_institutional_intel_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=AssertionError("requests.get called during pause")):
            import ingestion.institutional as inst
            result = inst.get_institutional_intel()

        assert result is None


class TestMacroRegimeSilence:
    def test_get_macro_regime_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=AssertionError("requests.get called during pause")):
            import ingestion.macro_regime as mr
            result = mr.get_macro_regime()

        assert result is None


class TestTradierChainSilence:
    def test_get_expirations_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=AssertionError("requests.get called during pause")):
            import ingestion.tradier_chain as tc
            result = tc.get_expirations()

        assert result is None

    def test_get_chain_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=AssertionError("requests.get called during pause")):
            import ingestion.tradier_chain as tc
            result = tc.get_chain("TSLA", "2026-04-25")

        assert result is None


class TestPremarketSilence:
    def test_fetch_premarket_returns_none_when_paused(self, monkeypatch, tmp_path):
        _force_paused(monkeypatch, tmp_path)
        import pause_guard as pg
        pg._cache_expires_at = 0.0

        import unittest.mock as mock
        with mock.patch("requests.get", side_effect=AssertionError("requests.get called during pause")):
            import ingestion.premarket as pm
            result = pm._fetch_premarket()

        assert result is None


class TestPauseGuardCacheInvalidation:
    """Verify the 500ms TTL cache is correctly invalidated."""

    def test_cache_expires_after_ttl(self, tmp_path):
        import pause_guard as pg

        # Start with a paused state
        pf = tmp_path / "pause.json"
        pf.write_text(json.dumps({"paused": True, "unpause_until": None}))
        old_file = pg.PAUSE_STATE_FILE
        pg.PAUSE_STATE_FILE = str(pf)
        pg._cache_expires_at = 0.0

        try:
            assert pg.is_paused() is True, "Should be paused"

            # Now switch to unpaused
            import datetime
            future = (datetime.datetime.now(datetime.timezone.utc) +
                      datetime.timedelta(hours=1)).isoformat()
            pf.write_text(json.dumps({"paused": False, "unpause_until": future}))

            # Cache still reports paused
            assert pg.is_paused() is True, "Cache should still report paused within TTL"

            # Expire the cache manually
            pg._cache_expires_at = 0.0
            assert pg.is_paused() is False, "After cache expiry, should read new file"
        finally:
            pg.PAUSE_STATE_FILE = old_file
            pg._cache_expires_at = 0.0
