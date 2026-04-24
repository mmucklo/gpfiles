"""
test_pause_socket_guard.py — Phase 21: socket-level network blocking tests.

Verifies that socket_guard.py:
  - Blocks non-whitelisted outbound connections when paused
  - Allows whitelisted connections (localhost:4222 NATS, localhost:4002 IBKR) when paused
  - Allows all connections when NOT paused
  - install_socket_guard() is idempotent (safe to call twice)
  - uninstall_socket_guard() restores the original function
"""
from __future__ import annotations

import json
import os
import socket
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import socket_guard as sg
from socket_guard import PausedNetworkError


@pytest.fixture(autouse=True)
def _reset_guard():
    """Ensure socket guard is uninstalled before and after each test."""
    sg.uninstall_socket_guard()
    yield
    sg.uninstall_socket_guard()


@pytest.fixture()
def paused_state(tmp_path, monkeypatch):
    """Point pause_guard at a paused state file and invalidate its cache."""
    import pause_guard as pg
    pf = tmp_path / "pause.json"
    pf.write_text(json.dumps({"paused": True, "unpause_until": None}))
    monkeypatch.setenv("PUBLISHER_PAUSE_STATE_FILE", str(pf))
    old_file = pg.PAUSE_STATE_FILE
    pg.PAUSE_STATE_FILE = str(pf)
    pg._cache_expires_at = 0.0
    yield str(pf)
    pg.PAUSE_STATE_FILE = old_file
    pg._cache_expires_at = 0.0


@pytest.fixture()
def unpaused_state(tmp_path, monkeypatch):
    """Point pause_guard at an unpaused state file."""
    import pause_guard as pg
    import datetime
    pf = tmp_path / "pause.json"
    future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
    pf.write_text(json.dumps({"paused": False, "unpause_until": future}))
    monkeypatch.setenv("PUBLISHER_PAUSE_STATE_FILE", str(pf))
    old_file = pg.PAUSE_STATE_FILE
    pg.PAUSE_STATE_FILE = str(pf)
    pg._cache_expires_at = 0.0
    yield str(pf)
    pg.PAUSE_STATE_FILE = old_file
    pg._cache_expires_at = 0.0


class TestSocketGuardInstallation:
    def test_install_is_idempotent(self):
        sg.install_socket_guard()
        first = socket.create_connection
        sg.install_socket_guard()   # second call should not change anything
        assert socket.create_connection is first

    def test_uninstall_restores_original(self):
        original = sg._original_create_connection
        sg.install_socket_guard()
        assert socket.create_connection is not original
        sg.uninstall_socket_guard()
        assert socket.create_connection is original

    def test_guard_installed_flag(self):
        assert sg._guard_installed is False
        sg.install_socket_guard()
        assert sg._guard_installed is True
        sg.uninstall_socket_guard()
        assert sg._guard_installed is False


class TestSocketGuardBlocking:
    def test_blocks_external_host_when_paused(self, paused_state):
        sg.install_socket_guard()
        with pytest.raises(PausedNetworkError) as exc_info:
            socket.create_connection(("api.tradier.com", 443))
        assert "blocked" in str(exc_info.value).lower()
        assert "api.tradier.com" in str(exc_info.value)

    def test_blocks_yfinance_host_when_paused(self, paused_state):
        sg.install_socket_guard()
        with pytest.raises(PausedNetworkError):
            socket.create_connection(("query1.finance.yahoo.com", 443))

    def test_blocks_any_non_whitelisted_port_when_paused(self, paused_state):
        sg.install_socket_guard()
        with pytest.raises(PausedNetworkError):
            socket.create_connection(("localhost", 8080))


class TestSocketGuardWhitelist:
    def _try_connect(self, host: str, port: int):
        """Attempt a connection and return any exception raised (or None if succeeded)."""
        try:
            conn = socket.create_connection((host, port), timeout=0.05)
            conn.close()
            return None
        except PausedNetworkError:
            raise  # re-raise to let the test catch it
        except Exception:
            return None  # connection refused / timeout is expected in test env

    def test_allows_nats_localhost_when_paused(self, paused_state):
        """localhost:4222 (NATS) must not be blocked by PausedNetworkError."""
        sg.install_socket_guard()
        # Should not raise PausedNetworkError — may succeed or fail with OSError
        self._try_connect("localhost", 4222)

    def test_allows_nats_127_when_paused(self, paused_state):
        """127.0.0.1:4222 (NATS) must not be blocked by PausedNetworkError."""
        sg.install_socket_guard()
        self._try_connect("127.0.0.1", 4222)

    def test_allows_ibkr_localhost_when_paused(self, paused_state):
        """localhost:4002 (IBKR gateway) must not be blocked by PausedNetworkError."""
        sg.install_socket_guard()
        self._try_connect("localhost", 4002)


class TestSocketGuardPassThrough:
    def test_allows_external_connections_when_not_paused(self, unpaused_state):
        """When system is NOT paused, guard is transparent — delegate to original."""
        sg.install_socket_guard()
        original_calls = []
        with mock.patch.object(
            sg, "_original_create_connection",
            side_effect=lambda *a, **kw: original_calls.append(a)
        ):
            try:
                socket.create_connection(("api.tradier.com", 443), timeout=0.01)
            except Exception:
                pass   # connection will fail in test env, that's fine

        assert len(original_calls) == 1, (
            "When unpaused, _original_create_connection should be called once"
        )
