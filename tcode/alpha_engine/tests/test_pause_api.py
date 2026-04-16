"""
Tests for POST /api/system/pause, POST /api/system/unpause, GET /api/system/pause-status.

Unit tests verify the pause state file logic directly.
Integration tests (skipped unless server is running) exercise the live endpoints.
"""
import datetime
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import publisher as pub


# ── Unit: pause/unpause round-trip via file ───────────────────────────────────

class TestPauseUnpauseRoundTrip:
    def test_pause_then_read(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        pub._write_pause_state(paused=True, unpause_until=None)
        state = pub._read_pause_state()
        assert state["paused"] is True
        assert state["unpause_until"] is None

    def test_unpause_10m_then_read(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
        pub._write_pause_state(paused=False, unpause_until=until)
        state = pub._read_pause_state()
        assert state["paused"] is False
        assert state["unpause_until"] is not None

    def test_unpause_then_pause_clears_until(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
        pub._write_pause_state(paused=False, unpause_until=until)
        # Now pause
        pub._write_pause_state(paused=True, unpause_until=None)
        state = pub._read_pause_state()
        assert state["paused"] is True
        assert state["unpause_until"] is None

    @pytest.mark.parametrize("minutes", [10, 30, 60, 120])
    def test_duration_options(self, tmp_path, monkeypatch, minutes):
        f = tmp_path / "state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
        pub._write_pause_state(paused=False, unpause_until=until)
        raw = json.loads(f.read_text())
        assert raw["paused"] is False
        assert raw["unpause_until"] is not None

    def test_remaining_sec_positive_when_active(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
        pub._write_pause_state(paused=False, unpause_until=until)
        state = pub._read_pause_state()
        assert state["paused"] is False
        # Check remaining is calculable from unpause_until
        assert state["unpause_until"] is not None
        from_state = datetime.datetime.fromisoformat(state["unpause_until"].replace("Z", "+00:00"))
        remaining = (from_state - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        assert remaining > 200  # should be close to 300s

    def test_remaining_sec_zero_when_paused(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        monkeypatch.setattr(pub, "_PAUSE_STATE_FILE", str(f))
        pub._write_pause_state(paused=True, unpause_until=None)
        state = pub._read_pause_state()
        assert state["paused"] is True
        assert state.get("unpause_until") is None


# ── Integration: live server (skipped unless running) ─────────────────────────

def _server_running() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:2112/api/system/pause-status", timeout=1) as r:
            data = json.loads(r.read())
            return "paused" in data
    except Exception:
        return False


@pytest.mark.skipif(not _server_running(), reason="API server not running on :2112")
class TestPauseApiLive:
    def _get(self, path: str) -> dict:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:2112{path}", timeout=3) as r:
            return json.loads(r.read())

    def _post(self, path: str, body: dict | None = None) -> dict:
        import urllib.request
        payload = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:2112{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())

    def test_get_status_shape(self):
        data = self._get("/api/system/pause-status")
        assert "paused" in data
        assert "unpause_until" in data
        assert "remaining_sec" in data

    def test_pause_endpoint(self):
        data = self._post("/api/system/pause")
        assert data["paused"] is True
        assert data["remaining_sec"] == 0

    def test_unpause_10m(self):
        data = self._post("/api/system/unpause", {"duration_min": 10})
        assert data["paused"] is False
        assert data["remaining_sec"] > 500  # ~600s

    def test_unpause_then_pause_round_trip(self):
        unpause = self._post("/api/system/unpause", {"duration_min": 30})
        assert unpause["paused"] is False
        pause = self._post("/api/system/pause")
        assert pause["paused"] is True
        status = self._get("/api/system/pause-status")
        assert status["paused"] is True

    def test_status_reflects_active_remaining(self):
        self._post("/api/system/unpause", {"duration_min": 1})
        status = self._get("/api/system/pause-status")
        assert status["paused"] is False
        assert status["remaining_sec"] > 0
        # Cleanup
        self._post("/api/system/pause")
