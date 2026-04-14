"""
Unit tests for alpha_engine/ingestion/signal_feedback.py

Tests: add, get_for_signal, get_digest, resolve subcommands.
SQL injection resistance: comment text is stored via parameterized queries.
"""
import json
import os
import sqlite3
import tempfile
import unittest


def _init_db(path: str) -> sqlite3.Connection:
    """Initialize the DB at path using init_db (runs schema.sql)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from data.init_db import init_db
    return init_db(path)


class TestSignalFeedbackAdd(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        _init_db(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _add(self, **kwargs):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from ingestion.signal_feedback import cmd_add
        base = {
            'signal_id': 'TSLA_CALL_2026-04-18_BUY_365.00_1',
            'signal_snapshot': {'model_id': 'SENTIMENT', 'confidence': 0.9},
            'user_comment': 'test comment',
            'action': 'COMMENT',
            'db_path': self.db_path,
        }
        base.update(kwargs)
        return cmd_add(base)

    def test_add_returns_id_and_ts(self):
        result = self._add()
        self.assertIn('id', result)
        self.assertIn('ts_feedback', result)
        self.assertIsInstance(result['id'], int)

    def test_add_stores_comment_verbatim(self):
        # Comment must not be trimmed or normalized
        comment = '   extra spaces and\nnewlines\t\there   '
        self._add(user_comment=comment)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute('SELECT user_comment FROM signal_feedback WHERE id=1').fetchone()
        conn.close()
        self.assertEqual(row[0], comment)  # verbatim — no stripping

    def test_add_rejects_empty_signal_id(self):
        result = self._add(signal_id='')
        self.assertIn('error', result)

    def test_add_rejects_empty_comment(self):
        result = self._add(user_comment='')
        self.assertIn('error', result)

    def test_add_rejects_invalid_action(self):
        result = self._add(action='INVALID_ACTION')
        self.assertIn('error', result)

    def test_add_rejects_invalid_tag(self):
        result = self._add(tag='not_a_real_tag')
        self.assertIn('error', result)

    def test_add_valid_tag(self):
        result = self._add(tag='bad_strike')
        self.assertNotIn('error', result)

    def test_add_cancel_action(self):
        result = self._add(action='CANCEL')
        self.assertNotIn('error', result)
        self.assertEqual(result['action'], 'CANCEL')

    def test_add_mark_winner_action(self):
        result = self._add(action='MARK_WINNER')
        self.assertNotIn('error', result)

    def test_sql_injection_resistance(self):
        """SQL injection in comment must not break the DB or execute."""
        malicious_comment = "'; DROP TABLE signal_feedback; --"
        result = self._add(user_comment=malicious_comment)
        # Should succeed without error
        self.assertNotIn('error', result)
        # Table must still exist
        conn = sqlite3.connect(self.db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        self.assertIn('signal_feedback', tables)
        # Comment stored verbatim
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute('SELECT user_comment FROM signal_feedback').fetchone()[0]
        conn.close()
        self.assertEqual(stored, malicious_comment)


class TestSignalFeedbackGetForSignal(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        _init_db(self.db_path)
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from ingestion.signal_feedback import cmd_add
        self.signal_id = 'TSLA_CALL_2026-04-18_BUY_365.00_1'
        for i in range(3):
            cmd_add({
                'signal_id': self.signal_id,
                'signal_snapshot': {},
                'user_comment': f'comment {i}',
                'action': 'COMMENT',
                'db_path': self.db_path,
            })
        # Add a row for a different signal
        cmd_add({
            'signal_id': 'OTHER_SIG',
            'signal_snapshot': {},
            'user_comment': 'other signal comment',
            'action': 'COMMENT',
            'db_path': self.db_path,
        })

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_returns_only_matching_signal(self):
        from ingestion.signal_feedback import cmd_get_for_signal
        rows = cmd_get_for_signal({'signal_id': self.signal_id, 'db_path': self.db_path})
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row['signal_id'], self.signal_id)

    def test_returns_newest_first(self):
        from ingestion.signal_feedback import cmd_get_for_signal
        rows = cmd_get_for_signal({'signal_id': self.signal_id, 'db_path': self.db_path})
        # ts_feedback descending
        timestamps = [r['ts_feedback'] for r in rows]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_empty_for_unknown_signal(self):
        from ingestion.signal_feedback import cmd_get_for_signal
        rows = cmd_get_for_signal({'signal_id': 'NO_SUCH_SIGNAL', 'db_path': self.db_path})
        self.assertEqual(rows, [])


class TestSignalFeedbackGetRecent(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        _init_db(self.db_path)
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from ingestion.signal_feedback import cmd_add
        for action in ['COMMENT', 'CANCEL', 'MARK_WINNER']:
            cmd_add({
                'signal_id': f'SIG_{action}',
                'signal_snapshot': {'model_id': 'MACRO'},
                'user_comment': f'comment for {action}',
                'action': action,
                'tag': 'bad_strike' if action == 'COMMENT' else None,
                'db_path': self.db_path,
            })

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_returns_all_without_filters(self):
        from ingestion.signal_feedback import cmd_get_recent
        result = cmd_get_recent({'db_path': self.db_path})
        self.assertEqual(len(result['rows']), 3)
        self.assertEqual(result['total'], 3)

    def test_filter_by_action(self):
        from ingestion.signal_feedback import cmd_get_recent
        result = cmd_get_recent({'action': 'CANCEL', 'db_path': self.db_path})
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['action'], 'CANCEL')

    def test_filter_by_tag(self):
        from ingestion.signal_feedback import cmd_get_recent
        result = cmd_get_recent({'tag': 'bad_strike', 'db_path': self.db_path})
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['tag'], 'bad_strike')

    def test_unresolved_count(self):
        from ingestion.signal_feedback import cmd_get_recent
        result = cmd_get_recent({'db_path': self.db_path})
        self.assertEqual(result['unresolved'], 3)  # none resolved yet


class TestSignalFeedbackGetDigest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        _init_db(self.db_path)
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from ingestion.signal_feedback import cmd_add
        # 2 bad_strike, 1 good_signal, 1 CANCEL
        for tag in ['bad_strike', 'bad_strike', 'good_signal']:
            cmd_add({
                'signal_id': 'SIG_A',
                'signal_snapshot': {'model_id': 'SENTIMENT'},
                'user_comment': 'feedback',
                'action': 'COMMENT',
                'tag': tag,
                'db_path': self.db_path,
            })
        cmd_add({
            'signal_id': 'SIG_B',
            'signal_snapshot': {'model_id': 'MACRO'},
            'user_comment': 'cancelling',
            'action': 'CANCEL',
            'db_path': self.db_path,
        })

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_digest_structure(self):
        from ingestion.signal_feedback import cmd_get_digest
        result = cmd_get_digest({'db_path': self.db_path})
        self.assertIn('total_feedback', result)
        self.assertIn('by_tag', result)
        self.assertIn('cancelled_signals', result)
        self.assertIn('unresolved_comments', result)

    def test_digest_by_tag_counts(self):
        from ingestion.signal_feedback import cmd_get_digest
        result = cmd_get_digest({'db_path': self.db_path})
        self.assertEqual(result['by_tag'].get('bad_strike', 0), 2)
        self.assertEqual(result['by_tag'].get('good_signal', 0), 1)

    def test_digest_cancelled_signals(self):
        from ingestion.signal_feedback import cmd_get_digest
        result = cmd_get_digest({'db_path': self.db_path})
        self.assertIn('SIG_B', result['cancelled_signals'])
        self.assertNotIn('SIG_A', result['cancelled_signals'])

    def test_digest_total_count(self):
        from ingestion.signal_feedback import cmd_get_digest
        result = cmd_get_digest({'db_path': self.db_path})
        self.assertEqual(result['total_feedback'], 4)


class TestSignalFeedbackResolve(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        _init_db(self.db_path)
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from ingestion.signal_feedback import cmd_add
        result = cmd_add({
            'signal_id': 'SIG_X',
            'signal_snapshot': {},
            'user_comment': 'needs resolution',
            'action': 'COMMENT',
            'db_path': self.db_path,
        })
        self.row_id = result['id']

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_resolve_sets_resolved_by(self):
        from ingestion.signal_feedback import cmd_resolve
        result = cmd_resolve({'id': self.row_id, 'resolved_by': 'PR #13', 'db_path': self.db_path})
        self.assertNotIn('error', result)
        self.assertEqual(result['resolved_by'], 'PR #13')
        self.assertIn('resolved_at', result)

    def test_resolve_rejects_missing_resolved_by(self):
        from ingestion.signal_feedback import cmd_resolve
        result = cmd_resolve({'id': self.row_id, 'resolved_by': '', 'db_path': self.db_path})
        self.assertIn('error', result)

    def test_resolve_rejects_unknown_id(self):
        from ingestion.signal_feedback import cmd_resolve
        result = cmd_resolve({'id': 99999, 'resolved_by': 'PR #X', 'db_path': self.db_path})
        self.assertIn('error', result)

    def test_rows_are_never_deleted(self):
        """Resolve must not delete the row — it only updates resolved_by."""
        from ingestion.signal_feedback import cmd_resolve
        cmd_resolve({'id': self.row_id, 'resolved_by': 'PR #13', 'db_path': self.db_path})
        conn = sqlite3.connect(self.db_path)
        count = conn.execute('SELECT COUNT(*) FROM signal_feedback').fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)  # row still exists


if __name__ == '__main__':
    unittest.main()
