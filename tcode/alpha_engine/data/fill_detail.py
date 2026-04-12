#!/usr/bin/env python3
"""Return JSON detail for a fill/trade, joining fills + signals + closed_trades."""
import json, sqlite3, sys, os
from pathlib import Path

DB = Path.home() / "tsla_alpha.db"

def get_fill_detail(fill_id: str) -> dict:
    if not DB.exists():
        return {"error": "no database"}
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Try closed_trades first (most complete)
    c.execute("SELECT * FROM closed_trades WHERE id=? OR signal_id=?", (fill_id, fill_id))
    ct = c.fetchone()

    # Fills record
    c.execute("SELECT * FROM fills WHERE id=? OR signal_id=?", (fill_id, fill_id))
    fill = c.fetchone()

    # Signal record
    signal = None
    sig_id = (ct and ct["signal_id"]) or (fill and fill["signal_id"]) or fill_id
    if sig_id:
        c.execute("SELECT * FROM signals WHERE id=?", (sig_id,))
        signal = c.fetchone()

    # Options snapshot near entry time
    snap = None
    if ct and ct["ticker"] and ct["strike"] and ct["entry_ts"]:
        c.execute("""SELECT * FROM options_snapshots
                     WHERE ticker=? AND strike=? AND option_type=?
                     ORDER BY ABS(julianday(ts) - julianday(?)) LIMIT 1""",
                  (ct["ticker"], ct["strike"], ct["option_type"] or "CALL", ct["entry_ts"]))
        snap = c.fetchone()

    conn.close()

    def row(r): return dict(r) if r else None

    return {
        "fill": row(fill),
        "closed_trade": row(ct),
        "signal": row(signal),
        "options_snapshot": row(snap),
    }

def list_fills(limit: int = 50) -> list:
    """List recent fills/closed trades for the log."""
    if not DB.exists():
        return []
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Prefer closed_trades (has entry+exit), fall back to fills
    c.execute("""
        SELECT id, signal_id, ticker, option_type, strike, expiration_date,
               entry_ts as ts, entry_price, exit_price, qty, pnl, pnl_pct,
               win, catalyst, model_id, confidence_at_entry, exit_reason,
               'closed_trade' as source
        FROM closed_trades ORDER BY entry_ts DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    if not rows:
        # Fall back to fills table
        c.execute("""
            SELECT id, signal_id, ticker, NULL as option_type, NULL as strike,
                   NULL as expiration_date, ts, fill_price as entry_price,
                   NULL as exit_price, qty, NULL as pnl, NULL as pnl_pct,
                   NULL as win, NULL as catalyst, NULL as model_id,
                   NULL as confidence_at_entry, NULL as exit_reason,
                   'fill' as source
            FROM fills ORDER BY ts DESC LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(list_fills()))
    elif cmd == "detail" and len(sys.argv) > 2:
        print(json.dumps(get_fill_detail(sys.argv[2])))
    else:
        print(json.dumps({"error": "usage: fill_detail.py [list|detail <id>]"}))
