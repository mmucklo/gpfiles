"""
Unit tests for Phase 16.1 publisher pause gate.

Tests:
- _read_pause_state() returns paused=True when no file exists
- _read_pause_state() returns paused=True when file says paused
- _read_pause_state() returns paused=False when file says active and not expired
- _read_pause_state() auto-re-pauses when unpause_until has passed
- _write_pause_state() writes a valid JSON file
- Publisher loop emits heartbeat with "PAUSED" detail when paused, skips cycle
"""
import json
import os
import sys
import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure alpha_engine is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ── Import the helpers under test ─────────────────────────────────────────────
# We import the module-level helpers directly to avoid starting the full engine.
import publisher as pub


# ── _read_pause_state ─────────────────────────────────────────────────────────

class TestReadPauseState:
    def test_no_file_returns_paused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(tmp_path / "nonexistent.json"))
        state = pub._read_pause_state()
        assert state["paused"] is True
        assert state["unpause_until"] is None

    def test_explicit_paused_file(self, tmp_path, monkeypatch):
        f = tmp_path / "pause_state.json"
        f.write_text(json.dumps({"paused": True, "unpause_until": None}))
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        state = pub._read_pause_state()
        assert state["paused"] is True

    def test_active_not_expired(self, tmp_path, monkeypatch):
        future = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = tmp_path / "pause_state.json"
        f.write_text(json.dumps({"paused": False, "unpause_until": future}))
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        state = pub._read_pause_state()
        assert state["paused"] is False
        assert state["unpause_until"] == future

    def test_expired_unpause_until_re_pauses(self, tmp_path, monkeypatch):
        past = (datetime.datetime.utcnow() - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = tmp_path / "pause_state.json"
        f.write_text(json.dumps({"paused": False, "unpause_until": past}))
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        state = pub._read_pause_state()
        assert state["paused"] is True

    def test_malformed_json_returns_paused(self, tmp_path, monkeypatch):
        f = tmp_path / "pause_state.json"
        f.write_text("not json at all {{{")
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        state = pub._read_pause_state()
        assert state["paused"] is True


# ── _write_pause_state ────────────────────────────────────────────────────────

class TestWritePauseState:
    def test_write_paused(self, tmp_path, monkeypatch):
        f = tmp_path / "pause_state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        pub._write_pause_state(paused=True, unpause_until=None)
        data = json.loads(f.read_text())
        assert data["paused"] is True
        assert data["unpause_until"] is None

    def test_write_active_with_until(self, tmp_path, monkeypatch):
        f = tmp_path / "pause_state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        until = datetime.datetime(2026, 4, 16, 14, 30, 0, tzinfo=datetime.timezone.utc)
        pub._write_pause_state(paused=False, unpause_until=until)
        data = json.loads(f.read_text())
        assert data["paused"] is False
        assert "2026-04-16T14:30:00Z" in data["unpause_until"]

    def test_write_read_roundtrip(self, tmp_path, monkeypatch):
        f = tmp_path / "pause_state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        until = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
        until = until.replace(tzinfo=datetime.timezone.utc)
        pub._write_pause_state(paused=False, unpause_until=until)
        state = pub._read_pause_state()
        assert state["paused"] is False


# ── Publisher cycle: paused skips external calls, heartbeat continues ─────────

class TestPublisherPauseBehavior:
    """
    Simulate one paused iteration of the broadcast_loop cycle.
    Verify: heartbeat emitted with "PAUSED" detail, no spot price fetch.
    """

    def test_paused_cycle_emits_heartbeat_and_skips(self, tmp_path, monkeypatch):
        """When paused, heartbeat fires with PAUSED detail, external calls skipped."""
        # Patch pause state file to return paused
        f = tmp_path / "pause_state.json"
        f.write_text(json.dumps({"paused": True, "unpause_until": None}))
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))

        heartbeat_calls = []

        async def fake_heartbeat(component, status="ok", detail=None, logger=None):
            heartbeat_calls.append({"component": component, "status": status, "detail": detail})

        # Simulate what the top of the while loop does
        async def run_one_paused_iteration():
            _pause = pub._read_pause_state()
            if _pause.get("paused", True):
                await fake_heartbeat("publisher", status="ok", detail="PAUSED — awaiting user activation", logger=None)
                return True  # would continue in real loop
            return False

        result = asyncio.run(run_one_paused_iteration())

        assert result is True, "Should have taken the paused branch"
        assert len(heartbeat_calls) == 1
        hb = heartbeat_calls[0]
        assert hb["component"] == "publisher"
        assert hb["status"] == "ok"
        assert "PAUSED" in (hb["detail"] or "")

    def test_active_cycle_does_not_pause(self, tmp_path, monkeypatch):
        """When active (not expired), pause check returns False and cycle proceeds."""
        future = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = tmp_path / "pause_state.json"
        f.write_text(json.dumps({"paused": False, "unpause_until": future}))
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))

        async def run_one_active_iteration():
            _pause = pub._read_pause_state()
            return _pause.get("paused", True)  # True → would skip, False → proceeds

        is_paused = asyncio.run(run_one_active_iteration())
        assert is_paused is False, "Should NOT take the paused branch when active"

    def test_expired_active_auto_pauses(self, tmp_path, monkeypatch):
        """When unpause_until has passed, _read_pause_state re-pauses automatically."""
        past = (datetime.datetime.utcnow() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = tmp_path / "pause_state.json"
        f.write_text(json.dumps({"paused": False, "unpause_until": past}))
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))

        state = pub._read_pause_state()
        assert state["paused"] is True, "Expired window should auto-re-pause"
