"""
test_api_rejections.py — Phase 14.3

Unit tests for:
- heartbeat_query.query_rejections() — list + pagination + filter correctness
- heartbeat_query.query_rejection_by_id() — detail row returns JSON-decoded chain_snapshot
- heartbeat_query.query_rejections_summary() — aggregated counts
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

# Allow importing from the parent alpha_engine directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import heartbeat_query as hq


def _make_db(path: str) -> sqlite3.Connection:
    """Create a minimal signal_rejections DB for testing."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_rejections (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                       TEXT NOT NULL,
            model                    TEXT NOT NULL,
            opt_type                 TEXT NOT NULL,
            archetype                TEXT NOT NULL,
            reason                   TEXT NOT NULL,
            expiry                   TEXT,
            model_id                 TEXT,
            direction                TEXT,
            confidence               REAL,
            ticker                   TEXT,
            option_type              TEXT,
            expiration_date          TEXT,
            target_strike_attempted  REAL,
            spot_at_rejection        REAL,
            reason_code              TEXT,
            reason_detail            TEXT,
            chain_snapshot           TEXT,
            strike_selector_breakdown TEXT,
            chop_regime_at_rejection TEXT,
            regime_context           TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            component TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class TestQueryRejections(unittest.TestCase):
    """Tests for query_rejections()."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.conn = _make_db(self.db_path)
        # Monkey-patch DB_PATH so hq functions use our test DB
        self._orig_db = hq.DB_PATH
        hq.DB_PATH = self.db_path

        now = datetime.now(timezone.utc)
        # Insert 5 STRIKE_SELECT_FAIL rows in the last 24h
        for i in range(5):
            ts = _iso(now - timedelta(hours=i))
            self.conn.execute(
                """INSERT INTO signal_rejections
                   (ts, model, opt_type, archetype, reason, model_id, direction,
                    confidence, reason_code, reason_detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ts, "SENTIMENT", "CALL", "DIRECTIONAL_STRONG",
                 "no_strike_passed_filters", "SENTIMENT", "BULLISH",
                 0.75, "STRIKE_SELECT_FAIL", f"Detail {i}"),
            )
        # 1 older row (outside 24h)
        old_ts = _iso(now - timedelta(hours=30))
        self.conn.execute(
            """INSERT INTO signal_rejections
               (ts, model, opt_type, archetype, reason, model_id, reason_code)
               VALUES (?,?,?,?,?,?,?)""",
            (old_ts, "MACRO", "PUT", "MOMENTUM_BREAKOUT", "old_reason", "MACRO", "LIQUIDITY_REJECT"),
        )
        # 2 LIQUIDITY_REJECT rows inside 24h
        for j in range(2):
            ts = _iso(now - timedelta(minutes=j * 30 + 10))
            self.conn.execute(
                """INSERT INTO signal_rejections
                   (ts, model, opt_type, archetype, reason, model_id, direction,
                    confidence, reason_code, reason_detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ts, "MACRO", "PUT", "MOMENTUM_BREAKOUT",
                 "liquidity_reject", "MACRO", "BEARISH",
                 0.60, "LIQUIDITY_REJECT", f"Liquidity detail {j}"),
            )
        self.conn.commit()

    def tearDown(self):
        hq.DB_PATH = self._orig_db
        self.conn.close()
        os.unlink(self.db_path)

    def test_default_returns_last_24h_only(self):
        result = hq.query_rejections(hours=24)
        # 5 STRIKE_SELECT_FAIL + 2 LIQUIDITY_REJECT = 7 (not the 1 older row)
        self.assertEqual(result["total_count"], 7)
        self.assertFalse(result["has_more"])

    def test_pagination_limit_and_offset(self):
        page1 = hq.query_rejections(hours=24, limit=3, offset=0)
        self.assertEqual(len(page1["items"]), 3)
        self.assertTrue(page1["has_more"])

        page3 = hq.query_rejections(hours=24, limit=3, offset=6)
        self.assertEqual(len(page3["items"]), 1)
        self.assertFalse(page3["has_more"])

    def test_filter_by_reason_code(self):
        result = hq.query_rejections(hours=24, reason_code="LIQUIDITY_REJECT")
        self.assertEqual(result["total_count"], 2)
        for item in result["items"]:
            rc = item.get("reason_code") or ""
            self.assertIn("LIQUIDITY_REJECT", rc)

    def test_filter_by_model(self):
        result = hq.query_rejections(hours=24, model="SENTIMENT")
        self.assertEqual(result["total_count"], 5)

    def test_reason_detail_trimmed_to_80_chars_in_list(self):
        long_detail = "X" * 200
        now_ts = _iso(datetime.now(timezone.utc))
        self.conn.execute(
            "INSERT INTO signal_rejections (ts, model, opt_type, archetype, reason, reason_detail) VALUES (?,?,?,?,?,?)",
            (now_ts, "EV_SECTOR", "CALL", "CONTRARIAN", "reason", long_detail),
        )
        self.conn.commit()
        result = hq.query_rejections(hours=1)
        found = next((i for i in result["items"] if (i.get("model") or i.get("model_id")) == "EV_SECTOR"), None)
        self.assertIsNotNone(found)
        detail = found.get("reason_detail") or ""
        self.assertLessEqual(len(detail), 80)

    def test_sorted_newest_first(self):
        result = hq.query_rejections(hours=24)
        items = result["items"]
        ids = [item["id"] for item in items]
        self.assertEqual(ids, sorted(ids, reverse=True))


class TestQueryRejectionById(unittest.TestCase):
    """Tests for query_rejection_by_id()."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.conn = _make_db(self.db_path)
        self._orig_db = hq.DB_PATH
        hq.DB_PATH = self.db_path

        chain = [{"strike": 365, "option_type": "CALL", "delta": 0.42, "volume": 3, "open_interest": 120}]
        breakdown = [{"strike": 365, "filter_killed": "LIQUIDITY", "filter_reason": "volume=3 < 50"}]
        regime = {"macro_regime": "RISK_OFF", "correlation_regime": "NORMAL"}

        now_ts = _iso(datetime.now(timezone.utc))
        self.conn.execute(
            """INSERT INTO signal_rejections
               (ts, model, opt_type, archetype, reason, model_id, direction, confidence,
                ticker, option_type, expiration_date, spot_at_rejection, reason_code,
                reason_detail, chain_snapshot, strike_selector_breakdown, regime_context,
                chop_regime_at_rejection)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now_ts, "SENTIMENT", "CALL", "DIRECTIONAL_STRONG", "no_strike_passed_filters",
             "SENTIMENT", "BULLISH", 0.82, "TSLA", "CALL", "2026-04-24",
             364.50, "STRIKE_SELECT_FAIL",
             "All 47 candidate strikes rejected: delta_band=23, liquidity=18, theta_cap=6",
             json.dumps(chain), json.dumps(breakdown), json.dumps(regime),
             "CHOPPY"),
        )
        self.conn.commit()
        self.row_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def tearDown(self):
        hq.DB_PATH = self._orig_db
        self.conn.close()
        os.unlink(self.db_path)

    def test_returns_full_row(self):
        d = hq.query_rejection_by_id(self.row_id)
        self.assertIsNotNone(d)
        self.assertEqual(d["id"], self.row_id)
        self.assertEqual(d["model_id"], "SENTIMENT")
        self.assertEqual(d["direction"], "BULLISH")
        self.assertAlmostEqual(d["confidence"], 0.82)
        self.assertEqual(d["reason_code"], "STRIKE_SELECT_FAIL")
        self.assertEqual(d["chop_regime_at_rejection"], "CHOPPY")

    def test_chain_snapshot_decoded_as_list(self):
        d = hq.query_rejection_by_id(self.row_id)
        snap = d["chain_snapshot"]
        self.assertIsInstance(snap, list, "chain_snapshot should be decoded from JSON string to list")
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["strike"], 365)

    def test_strike_selector_breakdown_decoded(self):
        d = hq.query_rejection_by_id(self.row_id)
        bd = d["strike_selector_breakdown"]
        self.assertIsInstance(bd, list)
        self.assertEqual(bd[0]["filter_killed"], "LIQUIDITY")

    def test_regime_context_decoded_as_dict(self):
        d = hq.query_rejection_by_id(self.row_id)
        rc = d["regime_context"]
        self.assertIsInstance(rc, dict)
        self.assertEqual(rc["macro_regime"], "RISK_OFF")

    def test_not_found_returns_none(self):
        result = hq.query_rejection_by_id(99999)
        self.assertIsNone(result)


class TestQueryRejectionsSummary(unittest.TestCase):
    """Tests for query_rejections_summary()."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.conn = _make_db(self.db_path)
        self._orig_db = hq.DB_PATH
        hq.DB_PATH = self.db_path

        now = datetime.now(timezone.utc)
        rows = [
            ("SENTIMENT", "CALL", "DIRECTIONAL_STRONG", "STRIKE_SELECT_FAIL"),
            ("SENTIMENT", "PUT",  "DIRECTIONAL_STRONG", "STRIKE_SELECT_FAIL"),
            ("MACRO",     "PUT",  "MOMENTUM_BREAKOUT",  "LIQUIDITY_REJECT"),
            ("EV_SECTOR", "CALL", "CONTRARIAN",         "CHOP_BLOCK"),
        ]
        for model, ot, arch, rc in rows:
            ts = _iso(now - timedelta(minutes=10))
            self.conn.execute(
                """INSERT INTO signal_rejections
                   (ts, model, opt_type, archetype, reason, model_id, reason_code)
                   VALUES (?,?,?,?,?,?,?)""",
                (ts, model, ot, arch, "reason", model, rc),
            )
        self.conn.commit()

    def tearDown(self):
        hq.DB_PATH = self._orig_db
        self.conn.close()
        os.unlink(self.db_path)

    def test_total_count(self):
        s = hq.query_rejections_summary(hours=24)
        self.assertEqual(s["total"], 4)

    def test_by_reason_counts(self):
        s = hq.query_rejections_summary(hours=24)
        self.assertEqual(s["by_reason"].get("STRIKE_SELECT_FAIL"), 2)
        self.assertEqual(s["by_reason"].get("LIQUIDITY_REJECT"), 1)
        self.assertEqual(s["by_reason"].get("CHOP_BLOCK"), 1)

    def test_by_model_counts(self):
        s = hq.query_rejections_summary(hours=24)
        self.assertEqual(s["by_model"].get("SENTIMENT"), 2)
        self.assertEqual(s["by_model"].get("MACRO"), 1)
        self.assertEqual(s["by_model"].get("EV_SECTOR"), 1)

    def test_by_archetype_counts(self):
        s = hq.query_rejections_summary(hours=24)
        self.assertEqual(s["by_archetype"].get("DIRECTIONAL_STRONG"), 2)
        self.assertEqual(s["by_archetype"].get("MOMENTUM_BREAKOUT"), 1)

    def test_no_data_in_window_returns_zeros(self):
        s = hq.query_rejections_summary(hours=0)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["by_reason"], {})


if __name__ == "__main__":
    unittest.main()
