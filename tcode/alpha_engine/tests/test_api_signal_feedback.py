"""
Integration tests for the signal feedback API endpoints.

Tests the Python subprocess (signal_feedback.py) as called by the Go API
handlers, validating the full add/get/recent/digest/resolve cycle.

Also validates:
- SQL injection resistance (comment stored verbatim via parameterized queries)
- Required fields enforcement
- Action + tag enum validation
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

# Path to the CLI entry point
SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'ingestion', 'signal_feedback.py')
PYTHON = os.path.join(os.path.dirname(__file__), '..', 'venv', 'bin', 'python')
if not os.path.exists(PYTHON):
    PYTHON = sys.executable  # fallback for test environments


def _run(subcommand: str, args: dict, db_path: str) -> dict:
    """Call signal_feedback.py <subcommand> <json> and return parsed result."""
    args['db_path'] = db_path
    result = subprocess.run(
        [PYTHON, SCRIPT, subcommand, json.dumps(args)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script exited {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


class TestAPISignalFeedbackAdd(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from data.init_db import init_db
        conn = init_db(self.db_path)
        conn.close()

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_add_comment_roundtrip(self):
        result = _run('add', {
            'signal_id': 'TSLA_CALL_2026-04-18_BUY_365.00_1',
            'signal_snapshot': {'model_id': 'SENTIMENT'},
            'user_comment': 'strike was too deep OTM for low-vol regime',
            'action': 'COMMENT',
            'tag': 'bad_strike',
        }, self.db_path)
        self.assertIn('id', result)
        self.assertIn('ts_feedback', result)
        self.assertNotIn('error', result)

    def test_add_cancel_action(self):
        result = _run('add', {
            'signal_id': 'SIG_CANCEL',
            'signal_snapshot': {},
            'user_comment': 'move already ran',
            'action': 'CANCEL',
        }, self.db_path)
        self.assertEqual(result['action'], 'CANCEL')
        self.assertNotIn('error', result)

    def test_validation_empty_signal_id(self):
        result = _run('add', {
            'signal_id': '',
            'signal_snapshot': {},
            'user_comment': 'test',
            'action': 'COMMENT',
        }, self.db_path)
        self.assertIn('error', result)

    def test_validation_empty_comment(self):
        result = _run('add', {
            'signal_id': 'SIG_1',
            'signal_snapshot': {},
            'user_comment': '',
            'action': 'COMMENT',
        }, self.db_path)
        self.assertIn('error', result)

    def test_validation_invalid_action(self):
        result = _run('add', {
            'signal_id': 'SIG_1',
            'signal_snapshot': {},
            'user_comment': 'test',
            'action': 'EXECUTE_TRADE',
        }, self.db_path)
        self.assertIn('error', result)

    def test_validation_invalid_tag(self):
        result = _run('add', {
            'signal_id': 'SIG_1',
            'signal_snapshot': {},
            'user_comment': 'test',
            'action': 'COMMENT',
            'tag': 'not_a_real_tag',
        }, self.db_path)
        self.assertIn('error', result)

    def test_sql_injection_in_comment(self):
        """SQL injection must be stored verbatim, not executed."""
        injection = "'); DROP TABLE signal_feedback; --"
        result = _run('add', {
            'signal_id': 'SIG_INJECT',
            'signal_snapshot': {},
            'user_comment': injection,
            'action': 'COMMENT',
        }, self.db_path)
        self.assertNotIn('error', result)
        # Retrieve the stored value
        rows = _run('get_for_signal', {'signal_id': 'SIG_INJECT'}, self.db_path)
        if isinstance(rows, dict):
            rows = rows.get('rows', rows) if 'rows' in rows else list(rows.values())[0] if rows else []
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user_comment'], injection)

    def test_sql_injection_in_signal_id(self):
        """SQL injection in signal_id stored verbatim via parameterized query."""
        injection_id = "'; DROP TABLE signal_feedback; --"
        result = _run('add', {
            'signal_id': injection_id,
            'signal_snapshot': {},
            'user_comment': 'test',
            'action': 'COMMENT',
        }, self.db_path)
        # Should succeed
        self.assertNotIn('error', result)


class TestAPISignalFeedbackGetForSignal(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from data.init_db import init_db
        conn = init_db(self.db_path)
        conn.close()
        self.signal_id = 'TSLA_CALL_2026-04-18_BUY_370.00_2'
        for i in range(3):
            _run('add', {
                'signal_id': self.signal_id,
                'signal_snapshot': {'model_id': 'MACRO'},
                'user_comment': f'feedback {i}',
                'action': 'COMMENT',
            }, self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_get_for_signal_returns_list(self):
        result = _run('get_for_signal', {'signal_id': self.signal_id}, self.db_path)
        rows = result if isinstance(result, list) else result.get('rows', [])
        self.assertEqual(len(rows), 3)

    def test_get_for_signal_newest_first(self):
        result = _run('get_for_signal', {'signal_id': self.signal_id}, self.db_path)
        rows = result if isinstance(result, list) else result.get('rows', [])
        timestamps = [r['ts_feedback'] for r in rows]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))


class TestAPISignalFeedbackRecent(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from data.init_db import init_db
        conn = init_db(self.db_path)
        conn.close()
        for action in ['COMMENT', 'CANCEL', 'MARK_WINNER']:
            _run('add', {
                'signal_id': f'SIG_{action}',
                'signal_snapshot': {'model_id': 'SENTIMENT'},
                'user_comment': f'test {action}',
                'action': action,
            }, self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_recent_returns_all(self):
        result = _run('get_recent', {}, self.db_path)
        self.assertEqual(result['total'], 3)

    def test_recent_filter_by_action(self):
        result = _run('get_recent', {'action': 'CANCEL'}, self.db_path)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['rows'][0]['action'], 'CANCEL')

    def test_recent_pagination(self):
        result = _run('get_recent', {'limit': 2, 'offset': 0}, self.db_path)
        self.assertEqual(len(result['rows']), 2)


class TestAPISignalFeedbackDigest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from data.init_db import init_db
        conn = init_db(self.db_path)
        conn.close()
        for _ in range(3):
            _run('add', {
                'signal_id': 'SIG_A', 'signal_snapshot': {'model_id': 'SENTIMENT'},
                'user_comment': 'bad', 'action': 'COMMENT', 'tag': 'bad_strike',
            }, self.db_path)
        _run('add', {
            'signal_id': 'SIG_B', 'signal_snapshot': {'model_id': 'MACRO'},
            'user_comment': 'cancel', 'action': 'CANCEL',
        }, self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_digest_structure(self):
        result = _run('get_digest', {}, self.db_path)
        self.assertIn('total_feedback', result)
        self.assertIn('by_tag', result)
        self.assertIn('cancelled_signals', result)
        self.assertIn('unresolved_comments', result)

    def test_digest_cancelled_list(self):
        result = _run('get_digest', {}, self.db_path)
        self.assertIn('SIG_B', result['cancelled_signals'])

    def test_digest_by_tag(self):
        result = _run('get_digest', {}, self.db_path)
        self.assertEqual(result['by_tag'].get('bad_strike', 0), 3)


class TestAPISignalFeedbackResolve(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from data.init_db import init_db
        conn = init_db(self.db_path)
        conn.close()
        result = _run('add', {
            'signal_id': 'SIG_RESOLVE',
            'signal_snapshot': {},
            'user_comment': 'needs fix',
            'action': 'COMMENT',
        }, self.db_path)
        self.row_id = result['id']

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_resolve_marks_row(self):
        result = _run('resolve', {'id': self.row_id, 'resolved_by': 'PR #13'}, self.db_path)
        self.assertNotIn('error', result)
        self.assertEqual(result['resolved_by'], 'PR #13')

    def test_resolve_requires_resolved_by(self):
        result = _run('resolve', {'id': self.row_id, 'resolved_by': ''}, self.db_path)
        self.assertIn('error', result)

    def test_resolve_unknown_id_returns_error(self):
        result = _run('resolve', {'id': 99999, 'resolved_by': 'PR #X'}, self.db_path)
        self.assertIn('error', result)

    def test_resolve_does_not_delete_row(self):
        _run('resolve', {'id': self.row_id, 'resolved_by': 'PR #13'}, self.db_path)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        count = conn.execute('SELECT COUNT(*) FROM signal_feedback').fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
