"""
Tests for heartbeat.py — process liveness pulse system.

Verifies:
1. emit_heartbeat() writes the correct row to SQLite
2. Status promotion logic: ok → degraded → error (via heartbeat_query.py EXPECTED_MAX_AGE)
3. system_alert row is written when status == "error"
4. emit_heartbeat_recovered() logs a HEARTBEAT-RECOVERED system_alert
"""
import os
import sqlite3
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.init_db import init_db
from heartbeat import emit_heartbeat, emit_heartbeat_recovered


@pytest.fixture()
def tmp_db(tmp_path):
    """Initialise a fresh tsla_alpha.db for each test."""
    db_path = str(tmp_path / "tsla_alpha.db")
    conn = init_db(db_path)
    conn.close()
    return db_path


class TestEmitHeartbeat:
    def test_writes_row(self, tmp_db):
        emit_heartbeat("publisher", status="ok", db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute(
            "SELECT component, status, detail FROM process_heartbeats WHERE component='publisher'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "publisher"
        assert rows[0][1] == "ok"
        assert rows[0][2] is None

    def test_writes_detail(self, tmp_db):
        emit_heartbeat("intel_refresh", status="degraded",
                        detail="FRED rate-limited", db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT detail FROM process_heartbeats WHERE component='intel_refresh'"
        ).fetchone()
        conn.close()
        assert row[0] == "FRED rate-limited"

    def test_error_status_writes_system_alert(self, tmp_db):
        emit_heartbeat("publisher", status="error",
                        detail="intel_fetch_failed:ConnectionError", db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        alerts = conn.execute(
            "SELECT component, status, message FROM system_alerts"
        ).fetchall()
        conn.close()
        assert len(alerts) == 1
        assert alerts[0][0] == "publisher"
        assert alerts[0][1] == "error"
        assert "intel_fetch_failed" in alerts[0][2]

    def test_ok_status_does_not_write_alert(self, tmp_db):
        emit_heartbeat("publisher", status="ok", db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM system_alerts").fetchone()[0]
        conn.close()
        assert count == 0

    def test_multiple_components(self, tmp_db):
        for comp in ["publisher", "intel_refresh", "options_chain_api"]:
            emit_heartbeat(comp, db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute(
            "SELECT component FROM process_heartbeats ORDER BY id"
        ).fetchall()
        conn.close()
        components = [r[0] for r in rows]
        assert "publisher" in components
        assert "intel_refresh" in components
        assert "options_chain_api" in components

    def test_does_not_raise_on_nonexistent_db(self, tmp_path):
        # Must not raise even if DB hasn't been created
        bad_path = str(tmp_path / "new_subdir" / "test.db")
        # This will fail gracefully (SQLite will try to create the dir — it won't,
        # but emit_heartbeat swallows exceptions)
        try:
            emit_heartbeat("publisher", db_path=bad_path)
        except Exception as e:
            pytest.fail(f"emit_heartbeat raised unexpectedly: {e}")


class TestEmitHeartbeatRecovered:
    def test_writes_recovery_alert(self, tmp_db):
        emit_heartbeat_recovered("publisher", db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        alerts = conn.execute(
            "SELECT message FROM system_alerts WHERE component='publisher'"
        ).fetchall()
        conn.close()
        assert len(alerts) == 1
        assert "[HEARTBEAT-RECOVERED]" in alerts[0][0]


class TestStatusPromotionLogic:
    """
    Verify the heartbeat_query.py _compute_status() function produces
    ok / degraded / error based on age relative to expected_max_age.
    """

    def test_status_ok_within_max(self):
        from heartbeat_query import _compute_status
        # publisher expected_max = 30s → age 10s = ok
        assert _compute_status("publisher", 10, None) == "ok"

    def test_status_ok_at_boundary(self):
        from heartbeat_query import _compute_status
        assert _compute_status("publisher", 30, None) == "ok"

    def test_status_degraded_above_max(self):
        from heartbeat_query import _compute_status
        # 31s > 30s max → degraded
        assert _compute_status("publisher", 31, None) == "degraded"

    def test_status_degraded_within_3x(self):
        from heartbeat_query import _compute_status
        # 60s = 2× max (30s), so still degraded
        assert _compute_status("publisher", 60, None) == "degraded"

    def test_status_error_beyond_3x(self):
        from heartbeat_query import _compute_status
        # 91s > 3×30s → error
        assert _compute_status("publisher", 91, None) == "error"

    def test_premarket_off_hours_always_ok(self, monkeypatch):
        from heartbeat_query import _compute_status
        import heartbeat_query
        # Patch _is_premarket_window to return False (off-hours)
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: False)
        # Even with extreme age, premarket is ok off-hours
        assert _compute_status("premarket", 999999, None) == "ok"

    def test_premarket_in_hours_uses_normal_logic(self, monkeypatch):
        from heartbeat_query import _compute_status
        import heartbeat_query
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        # 121s > 120s max → degraded
        assert _compute_status("premarket", 121, None) == "degraded"
