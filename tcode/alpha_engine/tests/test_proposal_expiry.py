"""
test_proposal_expiry.py — Phase 16

Tests for trade proposal TTL / expiry logic in proposal_store.py.
Uses an in-memory SQLite database to avoid touching production files.
"""

import sys, os, json, sqlite3, importlib.util, tempfile, pathlib
from datetime import datetime, timezone, timedelta
import pytest


def _load_proposal_store(tmp_db: pathlib.Path):
    """Load proposal_store.py with _DB_PATH_OVERRIDE patched to a temp file."""
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'proposal_store.py')
    spec = importlib.util.spec_from_file_location('proposal_store', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Set override AFTER exec so module-level assignment doesn't reset it
    mod._DB_PATH_OVERRIDE = str(tmp_db)
    return mod


@pytest.fixture()
def store(tmp_path):
    db_path = tmp_path / 'test_alpha.db'
    ps = _load_proposal_store(db_path)
    ps._ensure_tables()
    return ps, db_path


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _future_iso(seconds: int = 60):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _past_iso(seconds: int = 10):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _make_proposal(id_: str = 'p-001', status: str = 'pending',
                   expires_in: int = 60) -> dict:
    return {
        'id': id_,
        'ts_created': _now_iso(),
        'ts_expires': _future_iso(expires_in),
        'status': status,
        'strategy': 'MOMENTUM',
        'direction': 'BULLISH',
        'legs': '[]',
        'entry_price': 2.50,
        'stop_price': 2.00,
        'target_price': 3.50,
        'kelly_fraction': 0.25,
        'quantity': 5,
        'confidence': 0.72,
        'regime_snapshot': '{}',
        'signals_contributing': '[]',
        'raw_signal': '{}',
    }


def test_upsert_and_retrieve(store):
    ps, db_path = store
    prop = _make_proposal()
    ps.upsert(prop)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id, status FROM trade_proposals WHERE id=?", (prop['id'],)).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == prop['id']
    assert row[1] == 'pending'


def test_pending_proposals_excludes_expired(store):
    ps, db_path = store

    # Insert one pending (valid) and one expired
    valid = _make_proposal('p-valid', expires_in=300)
    expired = _make_proposal('p-expired', expires_in=60)
    # Manually set ts_expires in the past for expired proposal
    expired['ts_expires'] = _past_iso(5)

    ps.upsert(valid)
    ps.upsert(expired)

    # get_pending should only return non-expired pending proposals
    pending = ps.get_pending()
    ids = [p['id'] for p in pending]
    assert 'p-valid' in ids
    assert 'p-expired' not in ids


def test_upsert_updates_existing_row(store):
    ps, db_path = store
    prop = _make_proposal()
    ps.upsert(prop)

    # Update to executed
    prop['status'] = 'executed'
    ps.upsert(prop)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM trade_proposals WHERE id=?", (prop['id'],)).fetchone()
    conn.close()
    assert row[0] == 'executed'


def test_multiple_proposals_stored(store):
    ps, _ = store
    for i in range(5):
        ps.upsert(_make_proposal(f'p-{i:03d}'))

    pending = ps.get_pending()
    assert len(pending) == 5


def test_skipped_not_in_pending(store):
    ps, _ = store
    prop = _make_proposal('p-skip')
    ps.upsert(prop)

    prop['status'] = 'skipped'
    ps.upsert(prop)

    pending = ps.get_pending()
    assert not any(p['id'] == 'p-skip' for p in pending)
