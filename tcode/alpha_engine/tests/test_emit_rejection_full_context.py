"""
test_emit_rejection_full_context.py — Phase 14.3

Integration test verifying that publisher's emit_rejection() writes ALL the new
Phase-14.3 columns: chain_snapshot, strike_selector_breakdown, regime_context,
and all other extended fields.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import heartbeat


class TestEmitRejectionFullContext(unittest.TestCase):
    """emit_rejection() must write all Phase-14.3 columns."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        # Ensure the file is empty — emit_rejection will create the schema on first write.
        os.unlink(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _fetch_last_row(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM signal_rejections ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else {}

    def test_all_phase_14_3_fields_written(self):
        chain = [
            {"strike": 365, "option_type": "CALL", "delta": 0.42, "gamma": 0.008,
             "theta": -0.12, "vega": 0.15, "volume": 3, "open_interest": 120,
             "bid": 4.50, "ask": 4.80},
        ]
        breakdown = [
            {"strike": 365, "option_type": "CALL", "score": None,
             "delta": 0.42, "filter_killed": "LIQUIDITY",
             "filter_reason": "volume=3 < MIN_OPTION_VOLUME_TODAY=50"},
        ]
        regime = {"macro_regime": "RISK_OFF", "correlation_regime": "NORMAL"}

        heartbeat.emit_rejection(
            model="SENTIMENT",
            opt_type="CALL",
            archetype="DIRECTIONAL_STRONG",
            reason="no_strike_passed_filters",
            expiry="2026-04-24",
            model_id="SENTIMENT",
            direction="BULLISH",
            confidence=0.82,
            ticker="TSLA",
            option_type="CALL",
            expiration_date="2026-04-24",
            target_strike_attempted=365.0,
            spot_at_rejection=362.50,
            reason_code="STRIKE_SELECT_FAIL",
            reason_detail="All 47 candidate strikes rejected: delta_band=23, liquidity=18, theta_cap=6",
            chain_snapshot=json.dumps(chain),
            strike_selector_breakdown=json.dumps(breakdown),
            chop_regime_at_rejection="CHOPPY",
            regime_context=json.dumps(regime),
            db_path=self.db_path,
        )

        row = self._fetch_last_row()
        self.assertNotEqual(row, {}, "No row was written to signal_rejections")

        # Phase 14.1 legacy fields
        self.assertEqual(row["model"], "SENTIMENT")
        self.assertEqual(row["opt_type"], "CALL")
        self.assertEqual(row["archetype"], "DIRECTIONAL_STRONG")
        self.assertEqual(row["reason"], "no_strike_passed_filters")

        # Phase 14.3 extended fields
        self.assertEqual(row["model_id"], "SENTIMENT")
        self.assertEqual(row["direction"], "BULLISH")
        self.assertAlmostEqual(row["confidence"], 0.82)
        self.assertEqual(row["ticker"], "TSLA")
        self.assertEqual(row["option_type"], "CALL")
        self.assertEqual(row["expiration_date"], "2026-04-24")
        self.assertAlmostEqual(row["target_strike_attempted"], 365.0)
        self.assertAlmostEqual(row["spot_at_rejection"], 362.50)
        self.assertEqual(row["reason_code"], "STRIKE_SELECT_FAIL")
        self.assertIn("47 candidate", row["reason_detail"])
        self.assertEqual(row["chop_regime_at_rejection"], "CHOPPY")

        # chain_snapshot is stored as JSON string
        snap = json.loads(row["chain_snapshot"])
        self.assertIsInstance(snap, list)
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["strike"], 365)

        # strike_selector_breakdown is stored as JSON string
        bd = json.loads(row["strike_selector_breakdown"])
        self.assertIsInstance(bd, list)
        self.assertEqual(bd[0]["filter_killed"], "LIQUIDITY")

        # regime_context is stored as JSON string
        rc = json.loads(row["regime_context"])
        self.assertEqual(rc["macro_regime"], "RISK_OFF")

    def test_legacy_call_still_writes_minimal_row(self):
        """Old callers using only model/opt_type/archetype/reason/expiry must still work."""
        heartbeat.emit_rejection(
            model="MACRO",
            opt_type="PUT",
            archetype="MOMENTUM_BREAKOUT",
            reason="strike_selector_exception:ConnectionError()",
            expiry="2026-04-24",
            db_path=self.db_path,
        )

        row = self._fetch_last_row()
        self.assertEqual(row["model"], "MACRO")
        self.assertEqual(row["opt_type"], "PUT")
        self.assertEqual(row["reason"], "strike_selector_exception:ConnectionError()")
        # New columns should be NULL or defaults
        self.assertIsNone(row.get("reason_code"))
        self.assertIsNone(row.get("chain_snapshot"))

    def test_write_failure_does_not_crash(self):
        """emit_rejection must never raise — failed writes are swallowed."""
        # Use a path that cannot be created (file in non-existent dir)
        bad_path = "/nonexistent_dir_xyz/test.db"
        try:
            heartbeat.emit_rejection(
                model="SENTIMENT",
                opt_type="CALL",
                archetype="DIRECTIONAL_STRONG",
                reason="test_failure",
                db_path=bad_path,
            )
        except Exception as e:
            self.fail(f"emit_rejection raised an exception on write failure: {e}")

    def test_system_alert_written_on_rejection(self):
        """emit_rejection must also write a [SIGNAL-REJECTED] row to system_alerts."""
        heartbeat.emit_rejection(
            model="EV_SECTOR",
            opt_type="CALL",
            archetype="CONTRARIAN",
            reason="no_strike_passed_filters",
            reason_code="STRIKE_SELECT_FAIL",
            db_path=self.db_path,
        )

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        alert = conn.execute(
            "SELECT message FROM system_alerts WHERE message LIKE '%SIGNAL-REJECTED%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        self.assertIsNotNone(alert, "No system_alert was written for this rejection")
        self.assertIn("[SIGNAL-REJECTED]", alert["message"])

    def test_old_table_migrated_forward(self):
        """If signal_rejections exists with only Phase-14.1 columns, migration adds new columns."""
        # Create minimal Phase-14.1 table
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE signal_rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                model TEXT NOT NULL,
                opt_type TEXT NOT NULL,
                archetype TEXT NOT NULL,
                reason TEXT NOT NULL,
                expiry TEXT
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
        conn.close()

        # Emit a rich rejection — migration should add the new columns
        heartbeat.emit_rejection(
            model="SENTIMENT",
            opt_type="CALL",
            archetype="DIRECTIONAL_STRONG",
            reason="no_strike_passed_filters",
            reason_code="STRIKE_SELECT_FAIL",
            confidence=0.75,
            direction="BULLISH",
            db_path=self.db_path,
        )

        row = self._fetch_last_row()
        self.assertEqual(row["model"], "SENTIMENT")
        # New column should be populated
        self.assertEqual(row.get("reason_code"), "STRIKE_SELECT_FAIL")
        self.assertAlmostEqual(row.get("confidence"), 0.75)


class TestPublisherPathPopulatesAllFields(unittest.TestCase):
    """Phase 14.5 — verify that publisher-style emit_rejection() calls write
    non-NULL values for ALL context fields, not just the 14.1 legacy 5."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        os.unlink(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _fetch_last_row(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM signal_rejections ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else {}

    def test_publisher_path_no_strike_passed_filters(self):
        """Simulate publisher's emit_rejection when select_strike returns no candidate.

        Verifies that direction, confidence, spot_at_rejection, reason_detail,
        chain_snapshot, strike_selector_breakdown, chop_regime_at_rejection,
        and regime_context are all non-NULL — the fix introduced in Phase 14.5.
        """
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

        # Simulate the rejection audit that select_strike now returns
        rejection_audit = {
            "total_candidates": 9,
            "filter_eliminations": {
                "liquidity": 5,
                "greeks_unavailable": 0,
                "delta_band": 4,
                "theta_cap": 0,
                "vega_floor": 0,
            },
            "per_strike": [
                {"strike": 360.0, "option_type": "CALL", "score": None,
                 "delta": 0.55, "filter_killed": "LIQUIDITY",
                 "filter_reason": "volume=3<50"},
                {"strike": 365.0, "option_type": "CALL", "score": None,
                 "delta": 0.42, "filter_killed": "DELTA_BAND",
                 "filter_reason": "delta 0.420 outside [0.25, 0.35]"},
            ],
        }

        # Simulate chain rows (as dicts, as publisher would serialize them)
        chain_snapshot_rows = [
            {"strike": 360.0, "option_type": "CALL", "volume": 3, "open_interest": 120,
             "bid": 4.50, "ask": 4.80, "delta": 0.55, "gamma": 0.008,
             "theta": -0.12, "vega": 0.15, "iv": 0.65},
        ]

        elim = rejection_audit["filter_eliminations"]
        total = rejection_audit["total_candidates"]
        parts = [f"{k}={v}" for k, v in elim.items() if v > 0]
        reason_detail = (
            f"{total} candidates rejected: {', '.join(parts)}"
            if parts else f"{total} candidates, all rejected by unknown filter"
        )

        heartbeat.emit_rejection(
            model="SENTIMENT",
            opt_type="CALL",
            archetype="DIRECTIONAL_STRONG",
            reason="no_strike_passed_filters",
            expiry="2026-04-24",
            # Phase 14.5 context fields — must all be non-NULL on fresh rejections
            model_id="SENTIMENT",
            direction="BULLISH",
            confidence=0.82,
            ticker="TSLA",
            option_type="CALL",
            expiration_date="2026-04-24",
            reason_code="no_strike_passed_filters",
            reason_detail=reason_detail,
            spot_at_rejection=362.50,
            target_strike_attempted=360.0,
            chain_snapshot=json.dumps(chain_snapshot_rows),
            strike_selector_breakdown=json.dumps(rejection_audit["per_strike"]),
            chop_regime_at_rejection="CHOPPY",
            regime_context=json.dumps({
                "macro_regime": "RISK_OFF",
                "correlation_regime": "NORMAL",
            }),
            db_path=self.db_path,
        )

        row = self._fetch_last_row()
        self.assertNotEqual(row, {})

        # All Phase 14.5 fields must be non-NULL
        self.assertIsNotNone(row.get("direction"), "direction must not be NULL")
        self.assertIsNotNone(row.get("confidence"), "confidence must not be NULL")
        self.assertIsNotNone(row.get("spot_at_rejection"), "spot_at_rejection must not be NULL")
        self.assertIsNotNone(row.get("reason_detail"), "reason_detail must not be NULL")
        self.assertIsNotNone(row.get("chain_snapshot"), "chain_snapshot must not be NULL")
        self.assertIsNotNone(row.get("strike_selector_breakdown"),
                             "strike_selector_breakdown must not be NULL")
        self.assertIsNotNone(row.get("chop_regime_at_rejection"),
                             "chop_regime_at_rejection must not be NULL")
        self.assertIsNotNone(row.get("regime_context"), "regime_context must not be NULL")
        self.assertIsNotNone(row.get("target_strike_attempted"),
                             "target_strike_attempted must not be NULL")

        # reason_detail contains filter elimination counts
        self.assertIn("candidates rejected", row["reason_detail"])
        self.assertIn("liquidity=5", row["reason_detail"])
        self.assertIn("delta_band=4", row["reason_detail"])

        # chain_snapshot is valid JSON list
        snap = json.loads(row["chain_snapshot"])
        self.assertIsInstance(snap, list)
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["strike"], 360.0)

        # strike_selector_breakdown is valid JSON list with per-strike entries
        bd = json.loads(row["strike_selector_breakdown"])
        self.assertIsInstance(bd, list)
        self.assertEqual(len(bd), 2)
        filter_names = {e["filter_killed"] for e in bd}
        self.assertIn("LIQUIDITY", filter_names)
        self.assertIn("DELTA_BAND", filter_names)

        # regime_context parses correctly
        rc = json.loads(row["regime_context"])
        self.assertEqual(rc["macro_regime"], "RISK_OFF")
        self.assertEqual(rc["correlation_regime"], "NORMAL")

    def test_publisher_path_strike_selector_exception(self):
        """Simulate publisher's emit_rejection on exception path.

        direction, confidence, spot, chop, regime must all be populated.
        chain_snapshot and strike_selector_breakdown may be absent (exception
        means we never got a result back from the selector).
        """
        heartbeat.emit_rejection(
            model="MACRO",
            opt_type="PUT",
            archetype="DIRECTIONAL_STD",
            reason="strike_selector_exception:ConnectionError()",
            expiry="2026-04-24",
            model_id="MACRO",
            direction="BEARISH",
            confidence=0.72,
            ticker="TSLA",
            option_type="PUT",
            expiration_date="2026-04-24",
            reason_code="strike_selector_exception",
            reason_detail="ConnectionError()",
            spot_at_rejection=361.00,
            chop_regime_at_rejection="TRENDING",
            regime_context=json.dumps({
                "macro_regime": "RISK_OFF",
                "correlation_regime": "MACRO_LOCKED",
            }),
            db_path=self.db_path,
        )

        row = self._fetch_last_row()
        self.assertIsNotNone(row.get("direction"), "direction must not be NULL on exception path")
        self.assertIsNotNone(row.get("confidence"), "confidence must not be NULL on exception path")
        self.assertIsNotNone(row.get("spot_at_rejection"),
                             "spot_at_rejection must not be NULL on exception path")
        self.assertIsNotNone(row.get("chop_regime_at_rejection"),
                             "chop_regime_at_rejection must not be NULL on exception path")
        self.assertIsNotNone(row.get("regime_context"),
                             "regime_context must not be NULL on exception path")
        # chain_snapshot / strike_selector_breakdown are NULL on exception path — acceptable
        self.assertEqual(row.get("direction"), "BEARISH")
        self.assertAlmostEqual(row.get("confidence"), 0.72)


if __name__ == "__main__":
    unittest.main()
