"""
test_approval_flow.py — Phase 16

Integration test for the human-in-the-loop approval flow:
  emit proposal → proposal_store.upsert → pending list → execute action

Tests the entire data flow through proposal_store.py using a temp SQLite DB.
Does NOT require a live Go server or IBKR connection.
"""

import sys, os, json, sqlite3, importlib.util, pathlib
from datetime import datetime, timezone, timedelta
import pytest


def _load_proposal_store(db_path: pathlib.Path):
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'proposal_store.py')
    spec = importlib.util.spec_from_file_location('proposal_store', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Set override AFTER exec so module-level assignment doesn't reset it
    mod._DB_PATH_OVERRIDE = str(db_path)
    return mod


@pytest.fixture()
def ps(tmp_path):
    db_path = tmp_path / 'alpha.db'
    mod = _load_proposal_store(db_path)
    mod._ensure_tables()
    return mod, db_path


def _proposal(id_: str = 'flow-001', ttl_sec: int = 60) -> dict:
    now = datetime.now(timezone.utc)
    return {
        'id': id_,
        'ts_created': now.isoformat(),
        'ts_expires': (now + timedelta(seconds=ttl_sec)).isoformat(),
        'status': 'pending',
        'strategy': 'MOMENTUM',
        'direction': 'BULLISH',
        'legs': json.dumps([{'right': 'C', 'strike': 220.0, 'expiry': '20260117', 'action': 'BUY'}]),
        'entry_price': 2.50,
        'stop_price': 2.00,
        'target_price': 3.50,
        'kelly_fraction': 0.25,
        'quantity': 5,
        'confidence': 0.72,
        'regime_snapshot': json.dumps({'regime': 'TRENDING', 'confidence': 0.8}),
        'signals_contributing': json.dumps(['MOMENTUM', 'MACD', 'VWAP']),
        'raw_signal': '{}',
    }


# ── Scenario 1: happy path execute ───────────────────────────────────────────

def test_proposal_appears_in_pending(ps):
    mod, _ = ps
    mod.upsert(_proposal('flow-001'))
    pending = mod.get_pending()
    assert any(p['id'] == 'flow-001' for p in pending)


def test_execute_removes_from_pending(ps):
    mod, db_path = ps
    mod.upsert(_proposal('flow-002'))

    # Simulate execute: update status to 'executed'
    prop = _proposal('flow-002')
    prop['status'] = 'executed'
    mod.upsert(prop)

    pending = mod.get_pending()
    assert not any(p['id'] == 'flow-002' for p in pending)

    # Verify it's in the DB as executed
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM trade_proposals WHERE id='flow-002'").fetchone()
    conn.close()
    assert row[0] == 'executed'


def test_skip_removes_from_pending(ps):
    mod, _ = ps
    mod.upsert(_proposal('flow-003'))

    prop = _proposal('flow-003')
    prop['status'] = 'skipped'
    mod.upsert(prop)

    pending = mod.get_pending()
    assert not any(p['id'] == 'flow-003' for p in pending)


def test_expired_proposal_not_in_pending(ps):
    mod, _ = ps
    prop = _proposal('flow-004', ttl_sec=60)
    # Override to already-expired timestamp
    prop['ts_expires'] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    mod.upsert(prop)

    pending = mod.get_pending()
    assert not any(p['id'] == 'flow-004' for p in pending)


# ── Scenario 2: ledger write after execution ─────────────────────────────────

def test_ledger_upsert_and_retrieve(ps):
    mod, db_path = ps
    fill = {
        'strategy': 'MOMENTUM',
        'direction': 'BULLISH',
        'legs': '[]',
        'fill_price': 2.55,
        'quantity': 5,
        'entry_ts': datetime.now(timezone.utc).isoformat(),
        'pnl_realised': 0.0,
        'pnl_unrealised': 0.0,
        'regime_at_entry': 'TRENDING',
        'kelly_fraction': 0.25,
        'human_override': 0,
        'notes': '',
    }
    row_id = mod.upsert_ledger(fill)
    assert isinstance(row_id, int) and row_id > 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT strategy, entry_price FROM trade_ledger WHERE rowid=?", (row_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 'MOMENTUM'
    assert abs(row[1] - 2.55) < 0.001


# ── Scenario 3: strategy selector ────────────────────────────────────────────

def test_set_and_get_strategy(ps):
    mod, _ = ps
    mod.set_strategy('IRON_CONDOR', locked_by='user')
    result = mod.get_strategy()
    assert result['strategy'] == 'IRON_CONDOR'
    assert result['locked_by'] == 'user'


def test_strategy_defaults_to_none(ps):
    mod, _ = ps
    result = mod.get_strategy()
    # Should not raise; returns None or empty dict if not set
    assert result is None or result.get('strategy') is None
