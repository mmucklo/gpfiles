"""
Tests for data/logger.py and data/init_db.py
"""
import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock


class TestInitDB(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_schema_creates_all_tables(self):
        from data.init_db import init_db
        conn = init_db(self.db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        for expected in ("signals", "fills", "price_bars", "account_snapshots",
                         "options_snapshots", "closed_trades"):
            self.assertIn(expected, tables)

    def test_schema_migration_idempotent(self):
        """Running init_db twice must not raise."""
        from data.init_db import init_db
        conn = init_db(self.db_path)
        conn.close()
        conn2 = init_db(self.db_path)  # second run — CREATE TABLE IF NOT EXISTS
        conn2.close()


class TestDataLogger(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _make_signal(self):
        """Create a minimal ModelSignal-like mock."""
        from consensus import ModelSignal, SignalDirection, ModelType
        return ModelSignal(
            model_id=ModelType.SENTIMENT,
            direction=SignalDirection.BULLISH,
            confidence=0.95,
            timestamp=time.time(),
            ticker="TSLA",
            underlying_price=375.0,
            price_source="test",
            strategy_code="TEST-001",
        )

    def test_log_signal_insert_and_read(self):
        from data.logger import DataLogger
        logger = DataLogger(self.db_path)
        logger.init()

        sig = self._make_signal()

        async def run():
            await logger.start()
            sid = await logger.log_signal(sig)
            await asyncio.sleep(0.1)   # let writer flush
            await logger.stop()
            return sid

        sid = asyncio.run(run())

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["ticker"], "TSLA")
        self.assertAlmostEqual(row["confidence"], 0.95, places=3)
        self.assertEqual(row["direction"], "BULLISH")

    def test_log_account_snapshot(self):
        from data.logger import DataLogger
        logger = DataLogger(self.db_path)
        logger.init()

        snap = {
            "ts": "2026-04-01 12:00:00",
            "net_liquidation": 1000000.0,
            "cash_balance":    500000.0,
            "buying_power":    2000000.0,
            "unrealized_pnl":  1500.0,
            "realized_pnl":    -200.0,
            "equity_with_loan": 500000.0,
        }

        async def run():
            await logger.start()
            await logger.log_account_snapshot(snap)
            await asyncio.sleep(0.1)
            await logger.stop()

        asyncio.run(run())

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM account_snapshots").fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["net_liquidation"], 1000000.0, places=1)
        self.assertAlmostEqual(row["cash_balance"], 500000.0, places=1)

    def test_schema_migration_idempotent(self):
        """Logger can init twice without error."""
        from data.logger import DataLogger
        logger = DataLogger(self.db_path)
        logger.init()
        logger.init()  # second call — no exception


if __name__ == "__main__":
    unittest.main()
