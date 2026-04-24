"""
test_data_subpackage.py — Phase 19 smoke tests

Smoke tests for the data/ subpackage:
  - data/backfill.py   — DB table creation + per-table backfill helpers
  - data/fill_detail.py — get_fill_detail, list_fills
  - data/init_db.py    — init_db creates all schema tables
  - data/migrate.py    — migrate adds Phase 14 columns idempotently
  - data/scorecard.py  — get_scorecard, get_loss_summary, tag_trade

All tests use an in-memory (or tempfile) SQLite database — no production
database is touched and no network calls are made.
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import json
from unittest.mock import MagicMock, patch
import pytest

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Path to alpha_engine/data/
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_SCHEMA_PATH = os.path.join(_DATA_DIR, "schema.sql")


# ── shared helpers ────────────────────────────────────────────────────────────

def _load_module(name: str, relpath: str):
    """Load a module from a path relative to alpha_engine/ root."""
    abspath = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", relpath))
    spec = importlib.util.spec_from_file_location(name, abspath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_tmp_db() -> str:
    """Return the path to a fresh temporary SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _apply_schema(db_path: str):
    """Apply the canonical schema.sql to a fresh database."""
    conn = sqlite3.connect(db_path)
    with open(_SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def _seed_closed_trades(db_path: str, rows=None):
    """Insert sample closed_trades rows for scorecard tests."""
    if rows is None:
        rows = [
            # (id, model_id, pnl, pnl_pct, win, confidence_at_entry, entry_ts, exit_ts, loss_tag, loss_notes)
            ("t1", "MOMENTUM", 150.0, 0.15, 1, 0.85, "2026-01-01 10:00:00", "2026-01-01 15:00:00", None, None),
            ("t2", "MOMENTUM", -50.0, -0.05, 0, 0.80, "2026-01-02 10:00:00", "2026-01-02 15:00:00", None, None),
            ("t3", "IRON_CONDOR", 80.0, 0.08, 1, 0.75, "2026-01-03 10:00:00", "2026-01-03 15:00:00", None, None),
            ("t4", "IRON_CONDOR", -30.0, -0.03, 0, 0.72, "2026-01-04 10:00:00", "2026-01-04 15:00:00", "bad_signal", "false breakout"),
        ]
    conn = sqlite3.connect(db_path)
    conn.executemany(
        """INSERT INTO closed_trades
           (id, model_id, pnl, pnl_pct, win, confidence_at_entry,
            entry_ts, exit_ts, loss_tag, loss_notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# data/init_db.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestInitDb:

    def _load(self):
        return _load_module("alpha_engine.data.init_db", "data/init_db.py")

    def test_import(self):
        mod = self._load()
        assert hasattr(mod, "init_db")

    def test_init_db_creates_file(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            assert conn is not None
            conn.close()
            assert os.path.exists(path)
        finally:
            os.unlink(path)

    def test_init_db_creates_signals_table(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            conn.close()
            assert "signals" in tables
        finally:
            os.unlink(path)

    def test_init_db_creates_fills_table(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            conn.close()
            assert "fills" in tables
        finally:
            os.unlink(path)

    def test_init_db_creates_closed_trades_table(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            conn.close()
            assert "closed_trades" in tables
        finally:
            os.unlink(path)

    def test_init_db_is_idempotent(self):
        """Running init_db twice on the same file must not raise."""
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            conn.close()
            conn2 = mod.init_db(path)
            conn2.close()
        finally:
            os.unlink(path)

    def test_init_db_returns_connection_with_row_factory(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            assert conn.row_factory == sqlite3.Row
            conn.close()
        finally:
            os.unlink(path)

    def test_init_db_wal_mode(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            conn = mod.init_db(path)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
            assert mode == "wal"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════════
# data/migrate.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigrate:

    def _load(self):
        return _load_module("alpha_engine.data.migrate", "data/migrate.py")

    def test_import(self):
        mod = self._load()
        assert hasattr(mod, "migrate")

    def test_migrate_adds_loss_tag_column(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            mod.migrate(path)
            conn = sqlite3.connect(path)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(closed_trades)").fetchall()}
            conn.close()
            assert "loss_tag" in cols
            assert "loss_notes" in cols
        finally:
            os.unlink(path)

    def test_migrate_adds_phase14_signal_columns(self):
        mod = self._load()
        path = _make_tmp_db()
        try:
            mod.migrate(path)
            conn = sqlite3.connect(path)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
            conn.close()
            assert "selection_score" in cols
            assert "chop_regime" in cols
        finally:
            os.unlink(path)

    def test_migrate_is_idempotent(self):
        """Running migrate twice must not raise (columns already exist)."""
        mod = self._load()
        path = _make_tmp_db()
        try:
            mod.migrate(path)
            mod.migrate(path)  # second run should not raise
        finally:
            os.unlink(path)

    def test_migrate_does_not_destroy_existing_data(self):
        """Migration must not delete pre-existing signals rows."""
        mod_init = _load_module("alpha_engine.data.init_db", "data/init_db.py")
        path = _make_tmp_db()
        try:
            conn = mod_init.init_db(path)
            conn.execute(
                "INSERT INTO signals (id, ts, model_id, direction, confidence) VALUES (?, ?, ?, ?, ?)",
                ("sig-001", "2026-01-01 10:00:00", "MOMENTUM", "BULLISH", 0.88),
            )
            conn.commit()
            conn.close()

            mod = self._load()
            mod.migrate(path)

            conn2 = sqlite3.connect(path)
            row = conn2.execute("SELECT confidence FROM signals WHERE id='sig-001'").fetchone()
            conn2.close()
            assert row is not None
            assert abs(row[0] - 0.88) < 1e-6
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════════
# data/scorecard.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestScorecard:

    def _load(self):
        return _load_module("alpha_engine.data.scorecard", "data/scorecard.py")

    def _setup_db(self):
        """Return path to a tmp DB with schema + sample trades."""
        path = _make_tmp_db()
        _apply_schema(path)
        # Ensure migration columns exist
        mod_m = _load_module("alpha_engine.data.migrate", "data/migrate.py")
        mod_m.migrate(path)
        _seed_closed_trades(path)
        return path

    def test_import(self):
        mod = self._load()
        assert hasattr(mod, "get_scorecard")
        assert hasattr(mod, "get_loss_summary")
        assert hasattr(mod, "tag_trade")
        assert hasattr(mod, "get_losing_trades")

    def test_sharpe_helper_two_positive_returns(self):
        mod = self._load()
        result = mod._sharpe([0.05, 0.10, 0.07])
        assert isinstance(result, float)
        assert result > 0

    def test_sharpe_helper_mixed_returns_finite(self):
        """Mixed positive and negative returns produce a finite Sharpe ratio."""
        import math
        mod = self._load()
        result = mod._sharpe([0.05, -0.02, 0.04, -0.01])
        assert isinstance(result, float)
        assert math.isfinite(result)

    def test_sharpe_helper_single_element(self):
        mod = self._load()
        assert mod._sharpe([0.05]) == 0.0

    # ── get_scorecard ─────────────────────────────────────────────────────────

    def test_get_scorecard_empty_db(self):
        mod = self._load()
        path = _make_tmp_db()
        _apply_schema(path)
        try:
            result = mod.get_scorecard(path)
            assert isinstance(result, list)
            assert result == []
        finally:
            os.unlink(path)

    def test_get_scorecard_returns_list_of_dicts(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_scorecard(path)
            assert isinstance(result, list)
            assert len(result) >= 2  # MOMENTUM + IRON_CONDOR
            for item in result:
                assert isinstance(item, dict)
        finally:
            os.unlink(path)

    def test_get_scorecard_required_keys(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_scorecard(path)
            required = {
                "model_id", "trade_count", "win_count", "loss_count",
                "win_rate", "total_pnl", "avg_pnl", "best_trade",
                "worst_trade", "avg_confidence", "sharpe",
                "confidence_calibration", "common_loss_tags",
            }
            for item in result:
                missing = required - item.keys()
                assert not missing, f"Missing keys in scorecard row: {missing}"
        finally:
            os.unlink(path)

    def test_get_scorecard_win_rate_in_range(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_scorecard(path)
            for item in result:
                assert 0.0 <= item["win_rate"] <= 1.0, (
                    f"win_rate out of range for model {item['model_id']}: {item['win_rate']}"
                )
        finally:
            os.unlink(path)

    def test_get_scorecard_sorted_by_pnl_desc(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_scorecard(path)
            pnls = [item["total_pnl"] for item in result]
            assert pnls == sorted(pnls, reverse=True)
        finally:
            os.unlink(path)

    def test_get_scorecard_momentum_trade_count(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_scorecard(path)
            momentum = next((r for r in result if r["model_id"] == "MOMENTUM"), None)
            assert momentum is not None
            assert momentum["trade_count"] == 2
            assert momentum["win_count"] == 1
            assert momentum["loss_count"] == 1
        finally:
            os.unlink(path)

    # ── get_loss_summary ──────────────────────────────────────────────────────

    def test_get_loss_summary_empty_db(self):
        mod = self._load()
        path = _make_tmp_db()
        _apply_schema(path)
        try:
            result = mod.get_loss_summary(path)
            assert result["total_losses"] == 0
            assert result["total_loss_amount"] == 0.0
        finally:
            os.unlink(path)

    def test_get_loss_summary_counts_losses(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_loss_summary(path)
            assert result["total_losses"] == 2  # t2 and t4
            assert result["total_loss_amount"] < 0
            assert "loss_tags" in result
        finally:
            os.unlink(path)

    def test_get_loss_summary_required_keys(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_loss_summary(path)
            for key in ("total_losses", "total_loss_amount", "avg_loss", "loss_tags"):
                assert key in result
        finally:
            os.unlink(path)

    # ── get_losing_trades ─────────────────────────────────────────────────────

    def test_get_losing_trades_returns_list(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_losing_trades(path)
            assert isinstance(result, list)
        finally:
            os.unlink(path)

    def test_get_losing_trades_all_negative_pnl(self):
        mod = self._load()
        path = self._setup_db()
        try:
            result = mod.get_losing_trades(path)
            for trade in result:
                assert (trade["pnl"] or 0) <= 0
        finally:
            os.unlink(path)

    # ── tag_trade ─────────────────────────────────────────────────────────────

    def test_tag_trade_valid_tag(self):
        mod = self._load()
        path = self._setup_db()
        try:
            ok = mod.tag_trade(path, "t2", "bad_timing", "entered too early")
            assert ok is True
            # Verify the tag was persisted
            conn = sqlite3.connect(path)
            row = conn.execute(
                "SELECT loss_tag, loss_notes FROM closed_trades WHERE id='t2'"
            ).fetchone()
            conn.close()
            assert row[0] == "bad_timing"
            assert "too early" in row[1]
        finally:
            os.unlink(path)

    def test_tag_trade_invalid_tag_raises(self):
        mod = self._load()
        path = self._setup_db()
        try:
            with pytest.raises(ValueError, match="Invalid tag"):
                mod.tag_trade(path, "t2", "not_a_real_tag")
        finally:
            os.unlink(path)

    def test_tag_trade_nonexistent_id_returns_false(self):
        """Tagging a trade that doesn't exist returns False (0 rows updated)."""
        mod = self._load()
        path = self._setup_db()
        try:
            ok = mod.tag_trade(path, "nonexistent-id", "bad_signal")
            assert ok is False
        finally:
            os.unlink(path)

    def test_valid_tags_set(self):
        mod = self._load()
        expected = {
            "bad_signal", "bad_timing", "macro_event", "stop_loss",
            "expiry_decay", "oversize", "manual_error", "unknown",
        }
        assert mod.VALID_TAGS == expected


# ═══════════════════════════════════════════════════════════════════════════════
# data/fill_detail.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestFillDetail:

    def _load(self):
        return _load_module("alpha_engine.data.fill_detail", "data/fill_detail.py")

    def _setup_db(self, home_dir: str):
        """Create a tsla_alpha.db in a fake HOME with schema + sample data."""
        db_path = os.path.join(home_dir, "tsla_alpha.db")
        _apply_schema(db_path)
        mod_m = _load_module("alpha_engine.data.migrate", "data/migrate.py")
        mod_m.migrate(db_path)

        conn = sqlite3.connect(db_path)
        # Insert a signal
        conn.execute(
            """INSERT INTO signals (id, ts, model_id, direction, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            ("sig-001", "2026-01-01 10:00:00", "MOMENTUM", "BULLISH", 0.88),
        )
        # Insert a fill
        conn.execute(
            """INSERT INTO fills (id, ts, order_id, signal_id, ticker, side, qty, fill_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("fill-001", "2026-01-01 10:01:00", "ord-001", "sig-001", "TSLA", "BUY", 1, 10.50),
        )
        # Insert a closed trade
        conn.execute(
            """INSERT INTO closed_trades
               (id, signal_id, ticker, option_type, strike, expiration_date,
                entry_ts, exit_ts, entry_price, exit_price, qty, pnl, pnl_pct, win,
                catalyst, model_id, confidence_at_entry, exit_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ct-001", "sig-001", "TSLA", "CALL", 400, "2026-06-20",
                "2026-01-01 10:01:00", "2026-01-01 15:00:00",
                10.50, 12.00, 1, 150.0, 0.15, 1,
                None, "MOMENTUM", 0.88, "profit_target",
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_import(self):
        mod = self._load()
        assert hasattr(mod, "get_fill_detail")
        assert hasattr(mod, "list_fills")

    def test_get_fill_detail_no_db(self):
        """When the database doesn't exist, returns {error: 'no database'}."""
        mod = self._load()
        # Point DB at a path that doesn't exist
        orig_db = mod.DB
        mod.DB = type("FakePath", (), {"exists": lambda self: False})()
        try:
            result = mod.get_fill_detail("nonexistent")
            assert "error" in result
            assert result["error"] == "no database"
        finally:
            mod.DB = orig_db

    def test_list_fills_no_db(self):
        """When the database doesn't exist, returns empty list."""
        mod = self._load()
        orig_db = mod.DB
        mod.DB = type("FakePath", (), {"exists": lambda self: False})()
        try:
            result = mod.list_fills()
            assert result == []
        finally:
            mod.DB = orig_db

    def test_get_fill_detail_found(self, tmp_path):
        mod = self._load()
        db_path = str(tmp_path / "tsla_alpha.db")
        # Use tmp_path as a fake home so DB points to a real file
        # Manually create the DB there
        conn = sqlite3.connect(db_path)
        conn.executescript(open(_SCHEMA_PATH).read())
        conn.commit()
        # Migrate
        mod_m = _load_module("alpha_engine.data.migrate", "data/migrate.py")
        mod_m.migrate(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO signals (id, ts, model_id, direction, confidence)
               VALUES ('sig-001', '2026-01-01 10:00:00', 'MOMENTUM', 'BULLISH', 0.88)"""
        )
        conn.execute(
            """INSERT INTO closed_trades
               (id, signal_id, ticker, option_type, strike, expiration_date,
                entry_ts, exit_ts, entry_price, exit_price, qty, pnl, pnl_pct, win,
                catalyst, model_id, confidence_at_entry, exit_reason)
               VALUES ('ct-001', 'sig-001', 'TSLA', 'CALL', 400, '2026-06-20',
               '2026-01-01 10:01:00', '2026-01-01 15:00:00',
               10.50, 12.00, 1, 150.0, 0.15, 1, NULL, 'MOMENTUM', 0.88, 'profit_target')"""
        )
        conn.commit()
        conn.close()

        # Override the DB path in the module
        from pathlib import Path
        orig_db = mod.DB
        mod.DB = Path(db_path)
        try:
            result = mod.get_fill_detail("ct-001")
            assert isinstance(result, dict)
            assert "closed_trade" in result
            assert "signal" in result
            assert result["closed_trade"] is not None
            assert result["closed_trade"]["ticker"] == "TSLA"
        finally:
            mod.DB = orig_db

    def test_list_fills_returns_list(self, tmp_path):
        mod = self._load()
        db_path = str(tmp_path / "tsla_alpha.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(open(_SCHEMA_PATH).read())
        conn.commit()
        mod_m = _load_module("alpha_engine.data.migrate", "data/migrate.py")
        mod_m.migrate(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO closed_trades
               (id, signal_id, ticker, option_type, strike, expiration_date,
                entry_ts, exit_ts, entry_price, exit_price, qty, pnl, pnl_pct, win,
                catalyst, model_id, confidence_at_entry, exit_reason)
               VALUES ('ct-001', 'sig-001', 'TSLA', 'CALL', 400, '2026-06-20',
               '2026-01-01 10:01:00', '2026-01-01 15:00:00',
               10.50, 12.00, 1, 150.0, 0.15, 1, NULL, 'MOMENTUM', 0.88, 'profit_target')"""
        )
        conn.commit()
        conn.close()

        from pathlib import Path
        orig_db = mod.DB
        mod.DB = Path(db_path)
        try:
            result = mod.list_fills()
            assert isinstance(result, list)
            assert len(result) >= 1
            assert result[0]["ticker"] == "TSLA"
        finally:
            mod.DB = orig_db

    def test_list_fills_structure(self, tmp_path):
        """Each fill record must have expected keys."""
        mod = self._load()
        db_path = str(tmp_path / "tsla_alpha.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(open(_SCHEMA_PATH).read())
        conn.commit()
        mod_m = _load_module("alpha_engine.data.migrate", "data/migrate.py")
        mod_m.migrate(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO closed_trades
               (id, signal_id, ticker, option_type, strike, expiration_date,
                entry_ts, exit_ts, entry_price, exit_price, qty, pnl, pnl_pct, win,
                catalyst, model_id, confidence_at_entry, exit_reason)
               VALUES ('ct-002', 'sig-002', 'TSLA', 'PUT', 380, '2026-06-20',
               '2026-01-05 10:01:00', '2026-01-05 15:00:00',
               8.0, 6.0, 2, -200.0, -0.10, 0, NULL, 'IRON_CONDOR', 0.71, 'stop_loss')"""
        )
        conn.commit()
        conn.close()

        from pathlib import Path
        orig_db = mod.DB
        mod.DB = Path(db_path)
        try:
            result = mod.list_fills(limit=10)
            assert len(result) >= 1
            for fill in result:
                for key in ("id", "ticker", "entry_price", "qty", "pnl"):
                    assert key in fill, f"Missing key '{key}' in fill: {fill}"
        finally:
            mod.DB = orig_db


# ═══════════════════════════════════════════════════════════════════════════════
# data/backfill.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackfill:
    """Tests for backfill helper functions using a tmp SQLite DB and a yfinance stub."""

    @pytest.fixture(autouse=True)
    def stub_yfinance(self, monkeypatch):
        """Provide a fake yfinance that returns minimal OHLCV data."""
        import pandas as pd

        yf_stub = types.ModuleType("yfinance")

        class FakeTicker:
            def history(self, **_):
                idx = pd.date_range("2026-01-01", periods=3, freq="D")
                return pd.DataFrame(
                    {"Open": [400, 405, 410], "High": [410, 415, 420],
                     "Low": [395, 400, 405], "Close": [405, 410, 415], "Volume": [1_000_000] * 3},
                    index=idx,
                )

        def download(symbol, period="1y", progress=False, **_):
            idx = pd.date_range("2026-01-01", periods=3, freq="D")
            return pd.DataFrame(
                {"Open": [400, 405, 410], "High": [410, 415, 420],
                 "Low": [395, 400, 405], "Close": [405, 410, 415], "Volume": [1_000_000] * 3},
                index=idx,
            )

        yf_stub.Ticker = FakeTicker
        yf_stub.download = download
        monkeypatch.setitem(sys.modules, "yfinance", yf_stub)

    def _load(self):
        return _load_module("alpha_engine.data.backfill", "data/backfill.py")

    def _make_conn(self):
        """Return an in-memory connection with backfill tables."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS historical_prices (
                ts TEXT NOT NULL, ticker TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                PRIMARY KEY (ts, ticker)
            );
            CREATE TABLE IF NOT EXISTS macro_snapshots (
                ts TEXT PRIMARY KEY, vix REAL, vix_9d REAL, spy REAL,
                treasury_10y REAL, fxi REAL, regime TEXT
            );
            CREATE TABLE IF NOT EXISTS ev_sector_snapshots (
                ts TEXT PRIMARY KEY, tsla REAL, rivn REAL, lcid REAL, byd REAL, driv REAL
            );
            CREATE TABLE IF NOT EXISTS catalyst_events (
                ts TEXT PRIMARY KEY, event_type TEXT, description TEXT, impact_score REAL
            );
        """)
        conn.commit()
        return conn

    def test_import(self):
        mod = self._load()
        assert hasattr(mod, "backfill_prices")
        assert hasattr(mod, "backfill_macro_snapshots")
        assert hasattr(mod, "backfill_ev_snapshots")
        assert hasattr(mod, "_ensure_tables")

    def test_ensure_tables_creates_expected_tables(self):
        mod = self._load()
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        mod._ensure_tables(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for tbl in ("historical_prices", "macro_snapshots", "ev_sector_snapshots", "catalyst_events"):
            assert tbl in tables, f"Table missing: {tbl}"
        conn.close()

    def test_ensure_tables_is_idempotent(self):
        mod = self._load()
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        mod._ensure_tables(conn)
        mod._ensure_tables(conn)  # Should not raise
        conn.close()

    def test_backfill_prices_inserts_rows(self):
        mod = self._load()
        conn = self._make_conn()
        mod.backfill_prices(conn, tickers=["TSLA"], period="5d")
        count = conn.execute(
            "SELECT COUNT(*) FROM historical_prices WHERE ticker='TSLA'"
        ).fetchone()[0]
        assert count >= 1

    def test_backfill_prices_idempotent(self):
        """Running backfill_prices twice should not create duplicate rows."""
        mod = self._load()
        conn = self._make_conn()
        mod.backfill_prices(conn, tickers=["TSLA"], period="5d")
        count1 = conn.execute("SELECT COUNT(*) FROM historical_prices").fetchone()[0]
        mod.backfill_prices(conn, tickers=["TSLA"], period="5d")
        count2 = conn.execute("SELECT COUNT(*) FROM historical_prices").fetchone()[0]
        assert count1 == count2

    def test_backfill_macro_snapshots_empty_db(self):
        """Without SPY data, macro_snapshots should remain empty (no crash)."""
        mod = self._load()
        conn = self._make_conn()
        mod.backfill_macro_snapshots(conn)
        count = conn.execute("SELECT COUNT(*) FROM macro_snapshots").fetchone()[0]
        assert count == 0
        conn.close()

    def test_backfill_macro_snapshots_with_spy_data(self):
        mod = self._load()
        conn = self._make_conn()
        # Seed SPY + VIX prices for 3 dates
        rows = [
            ("2026-01-01", "SPY", 400, 410, 395, 405, 1_000_000),
            ("2026-01-02", "SPY", 405, 415, 400, 410, 1_100_000),
            ("2026-01-01", "^VIX", 18, 19, 17, 18.5, 0),
            ("2026-01-02", "^VIX", 20, 21, 19, 20.0, 0),
        ]
        conn.executemany(
            "INSERT INTO historical_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()
        mod.backfill_macro_snapshots(conn)
        count = conn.execute("SELECT COUNT(*) FROM macro_snapshots").fetchone()[0]
        assert count == 2
        conn.close()

    def test_backfill_macro_snapshots_regime_classification(self):
        """VIX > 30 → RISK_OFF; VIX < 15 → RISK_ON; otherwise NEUTRAL."""
        mod = self._load()
        conn = self._make_conn()
        rows = [
            ("2026-01-01", "SPY", 400, 410, 395, 405, 1_000_000),
            ("2026-01-01", "^VIX", 35, 36, 34, 35.0, 0),  # RISK_OFF
            ("2026-01-02", "SPY", 405, 415, 400, 410, 1_100_000),
            ("2026-01-02", "^VIX", 12, 13, 11, 12.0, 0),  # RISK_ON
            ("2026-01-03", "SPY", 408, 415, 405, 412, 900_000),
            ("2026-01-03", "^VIX", 20, 21, 19, 20.0, 0),  # NEUTRAL
        ]
        conn.executemany(
            "INSERT INTO historical_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()
        mod.backfill_macro_snapshots(conn)
        regimes = {
            r[0]: r[1]
            for r in conn.execute("SELECT ts, regime FROM macro_snapshots").fetchall()
        }
        assert regimes["2026-01-01"] == "RISK_OFF"
        assert regimes["2026-01-02"] == "RISK_ON"
        assert regimes["2026-01-03"] == "NEUTRAL"
        conn.close()

    def test_backfill_ev_snapshots_empty_db(self):
        mod = self._load()
        conn = self._make_conn()
        mod.backfill_ev_snapshots(conn)
        count = conn.execute("SELECT COUNT(*) FROM ev_sector_snapshots").fetchone()[0]
        assert count == 0
        conn.close()

    def test_backfill_ev_snapshots_with_tsla_data(self):
        mod = self._load()
        conn = self._make_conn()
        rows = [
            ("2026-01-01", "TSLA", 400, 410, 395, 405, 1_000_000),
            ("2026-01-01", "RIVN", 14, 15, 13, 14.5, 500_000),
            ("2026-01-02", "TSLA", 405, 415, 400, 410, 1_100_000),
        ]
        conn.executemany(
            "INSERT INTO historical_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()
        mod.backfill_ev_snapshots(conn)
        count = conn.execute("SELECT COUNT(*) FROM ev_sector_snapshots").fetchone()[0]
        assert count == 2
        conn.close()
