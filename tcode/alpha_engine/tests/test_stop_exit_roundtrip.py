"""
Phase 17 — Integration test: open position → feed bars → trailing stop fires → ledger updated.
"""
import sys
import os
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def temp_db(tmp_path):
    """Create a temp SQLite DB with the schema applied."""
    db_path = str(tmp_path / "test_alpha.db")
    schema_path = os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if os.path.exists(schema_path):
        with open(schema_path) as f:
            conn.executescript(f.read())
    # Insert a test trade_ledger row
    conn.execute("""
        INSERT INTO trade_ledger (ts_entry, strategy, direction, legs, entry_price, quantity, regime_at_entry)
        VALUES (?, 'MOMENTUM', 'BULLISH', '[]', 10.0, 2, 'TRENDING')
    """, (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return db_path, trade_id


class TestStopExitRoundtrip:
    def test_trailing_stop_triggers_ledger_update(self, temp_db):
        db_path, trade_id = temp_db

        import stop_manager as sm
        sm._positions.clear()

        with patch.object(sm, "DB_PATH", db_path):
            # Open position
            pos = sm.open_position(
                trade_id=trade_id,
                entry_price=10.0,
                quantity=2,
                direction="LONG",
                strategy="MOMENTUM",
                atr_at_entry=1.0,
            )

            # Feed bars that activate and then fire trailing stop
            # Step 1: price rises to 11.5 → activates trail (11.5 - 0.75 = 10.75)
            sm.update_stops(trade_id, current_price=11.5, current_atr=1.0)
            assert pos.trailing_engaged is True

            # Step 2: price falls to 10.5 → below trailing stop (10.75) → fires
            should_close, stop_type = sm.update_stops(trade_id, current_price=10.5, current_atr=1.0)
            assert should_close
            assert stop_type == "TRAILING"

            # Close the position
            sm.close_position(trade_id, exit_price=10.5, stop_type="TRAILING")
            assert pos.is_open is False

            # Verify trade_ledger updated
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT ts_exit, exit_price, stop_type FROM trade_ledger WHERE id = ?", (trade_id,)
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[1] == pytest.approx(10.5)
            assert row[2] == "TRAILING"

    def test_time_stop_triggers_ledger_update(self, temp_db):
        db_path, trade_id = temp_db

        import stop_manager as sm
        sm._positions.clear()

        with patch.object(sm, "DB_PATH", db_path):
            sm.open_position(
                trade_id=trade_id, entry_price=10.0, quantity=1,
                direction="LONG", strategy="MOMENTUM", atr_at_entry=1.0,
            )
            # Wind the time stop to past
            sm._positions[trade_id].time_stop_at = datetime.now(timezone.utc) - timedelta(seconds=1)

            should_close, stop_type = sm.update_stops(trade_id, current_price=10.5, current_atr=1.0)
            assert should_close
            assert stop_type == "TIME_STOP"

            sm.close_position(trade_id, exit_price=10.5, stop_type="TIME_STOP")
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT stop_type FROM trade_ledger WHERE id = ?", (trade_id,)
            ).fetchone()
            conn.close()
            assert row[0] == "TIME_STOP"

    def test_check_all_positions_returns_fired_exits(self, temp_db):
        db_path, trade_id = temp_db

        import stop_manager as sm
        sm._positions.clear()

        with patch.object(sm, "DB_PATH", db_path):
            sm.open_position(
                trade_id=trade_id, entry_price=10.0, quantity=1,
                direction="LONG", strategy="MOMENTUM", atr_at_entry=1.0,
            )
            # Price at initial stop (8.5) → should fire SL
            fired = sm.check_all_positions(current_price=8.0, current_atr=1.0)
            assert len(fired) == 1
            assert fired[0]["trade_id"] == trade_id
            assert fired[0]["stop_type"] == "SL"
