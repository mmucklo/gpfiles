"""
Tests for heartbeat_query.py — /api/system/heartbeats aggregation logic.

Verifies:
1. query_heartbeats() returns all expected components
2. Age and status are computed correctly from DB rows
3. Premarket off-hours special case: ok with "skipped:off-hours"
4. Never-seen components return error status
5. query_sparkline() returns recent rows for a given component
6. query_recent_alerts() returns system_alerts in reverse order
"""
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.init_db import init_db
from heartbeat import emit_heartbeat
import heartbeat_query


EXPECTED_COMPONENTS = {
    "publisher", "intel_refresh", "options_chain_api", "premarket",
    "congress_trades", "correlation_regime", "macro_regime",
    "engine_subscriber", "engine_ibkr_status",
}


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Initialise DB, patch DB_PATH in heartbeat_query, return path."""
    db_path = str(tmp_path / "tsla_alpha.db")
    conn = init_db(db_path)
    conn.close()
    monkeypatch.setattr(heartbeat_query, "DB_PATH", db_path)
    return db_path


def insert_heartbeat(db_path: str, component: str, status: str = "ok",
                     detail: str | None = None, age_sec: int = 5) -> None:
    """Insert a heartbeat row with a timestamp `age_sec` seconds in the past."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_sec)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO process_heartbeats (component, ts, status, detail, pid, uptime_sec) VALUES (?,?,?,?,?,?)",
        (component, ts, status, detail, 12345, 3600),
    )
    conn.commit()
    conn.close()


class TestQueryHeartbeats:
    def test_returns_all_expected_components(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        result = heartbeat_query.query_heartbeats()
        assert set(result["components"].keys()) == EXPECTED_COMPONENTS

    def test_fresh_heartbeat_is_ok(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        insert_heartbeat(fresh_db, "publisher", "ok", age_sec=5)
        result = heartbeat_query.query_heartbeats()
        comp = result["components"]["publisher"]
        assert comp["status"] == "ok"
        assert comp["age_sec"] is not None
        assert comp["age_sec"] < 30  # publisher max age

    def test_stale_heartbeat_is_degraded(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        # publisher max = 30s; 60s = 2× → degraded
        insert_heartbeat(fresh_db, "publisher", "ok", age_sec=60)
        result = heartbeat_query.query_heartbeats()
        assert result["components"]["publisher"]["status"] == "degraded"

    def test_very_stale_heartbeat_is_error(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        # publisher max = 30s; 95s = > 3× → error
        insert_heartbeat(fresh_db, "publisher", "ok", age_sec=95)
        result = heartbeat_query.query_heartbeats()
        assert result["components"]["publisher"]["status"] == "error"

    def test_never_seen_component_is_error(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        # Don't insert any heartbeat for publisher
        result = heartbeat_query.query_heartbeats()
        assert result["components"]["publisher"]["status"] == "error"
        assert result["components"]["publisher"]["last_ts"] is None

    def test_premarket_off_hours_is_ok_even_when_stale(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: False)
        # Premarket never pulsed — but off-hours so should be ok
        result = heartbeat_query.query_heartbeats()
        comp = result["components"]["premarket"]
        assert comp["status"] == "ok"
        assert "skipped:off-hours" in (comp["detail"] or "")

    def test_premarket_off_hours_overrides_age(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: False)
        # Insert a very stale premarket heartbeat
        insert_heartbeat(fresh_db, "premarket", "ok", age_sec=9999)
        result = heartbeat_query.query_heartbeats()
        # Off-hours overrides stale age → still ok
        assert result["components"]["premarket"]["status"] == "ok"

    def test_returns_expected_max_age_sec(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "_is_premarket_window", lambda: True)
        result = heartbeat_query.query_heartbeats()
        assert result["components"]["publisher"]["expected_max_age_sec"] == 30
        assert result["components"]["congress_trades"]["expected_max_age_sec"] == 3600


class TestQuerySparkline:
    def test_returns_rows_for_component(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "DB_PATH", fresh_db)
        for i in range(5):
            insert_heartbeat(fresh_db, "publisher", "ok", age_sec=i * 30)
        rows = heartbeat_query.query_sparkline("publisher", limit=10)
        assert len(rows) == 5
        for r in rows:
            assert "ts" in r
            assert "status" in r

    def test_returns_empty_for_unknown_component(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "DB_PATH", fresh_db)
        rows = heartbeat_query.query_sparkline("nonexistent_component")
        assert rows == []

    def test_respects_limit(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "DB_PATH", fresh_db)
        for i in range(15):
            insert_heartbeat(fresh_db, "publisher", age_sec=i)
        rows = heartbeat_query.query_sparkline("publisher", limit=10)
        assert len(rows) == 10


class TestQueryRecentAlerts:
    def test_returns_alerts(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "DB_PATH", fresh_db)
        conn = sqlite3.connect(fresh_db)
        for i in range(3):
            conn.execute(
                "INSERT INTO system_alerts (ts, component, status, message) VALUES (?,?,?,?)",
                ("2026-04-14 10:00:00", "publisher", "error", f"alert {i}"),
            )
        conn.commit()
        conn.close()
        alerts = heartbeat_query.query_recent_alerts(limit=5)
        assert len(alerts) == 3
        assert all("component" in a for a in alerts)

    def test_empty_when_no_alerts(self, fresh_db, monkeypatch):
        monkeypatch.setattr(heartbeat_query, "DB_PATH", fresh_db)
        assert heartbeat_query.query_recent_alerts() == []
