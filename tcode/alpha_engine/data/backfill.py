#!/usr/bin/env python3
"""
Historical data backfill: downloads 2 years of price, macro, and sector data
into SQLite for backtesting. Idempotent (INSERT OR IGNORE).
"""
import sys
import json
import logging
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

logger = logging.getLogger("Backfill")
DB_PATH = "/home/builder/tsla_alpha.db"


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn):
    """Create backfill-specific tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS historical_prices (
            ts TEXT NOT NULL,
            ticker TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (ts, ticker)
        );
        CREATE TABLE IF NOT EXISTS macro_snapshots (
            ts TEXT PRIMARY KEY,
            vix REAL, vix_9d REAL, spy REAL, treasury_10y REAL,
            fxi REAL, regime TEXT
        );
        CREATE TABLE IF NOT EXISTS ev_sector_snapshots (
            ts TEXT PRIMARY KEY,
            tsla REAL, rivn REAL, lcid REAL, byd REAL, driv REAL
        );
        CREATE TABLE IF NOT EXISTS catalyst_events (
            ts TEXT PRIMARY KEY,
            event_type TEXT,
            description TEXT,
            impact_score REAL
        );
    """)
    conn.commit()


def backfill_prices(conn, tickers=None, period="2y"):
    """Backfill daily OHLCV for given tickers."""
    import yfinance as yf

    if tickers is None:
        tickers = ["TSLA", "RIVN", "LCID", "1211.HK", "DRIV", "SPY", "^VIX", "ES=F", "NQ=F", "^TNX", "FXI", "^STOXX50E"]

    for symbol in tickers:
        try:
            logger.info(f"Backfilling {symbol}...")
            data = yf.download(symbol, period=period, progress=False)
            if data.empty:
                logger.warning(f"No data for {symbol}")
                continue

            # Flatten MultiIndex columns (yfinance >= 0.2 returns (field, ticker) tuples)
            if isinstance(data.columns, __import__('pandas').MultiIndex):
                data.columns = [col[0] for col in data.columns]

            rows = []
            for ts, row in data.iterrows():
                date_str = ts.strftime("%Y-%m-%d")
                rows.append((
                    date_str, symbol,
                    round(float(row.get("Open", 0) or 0), 2),
                    round(float(row.get("High", 0) or 0), 2),
                    round(float(row.get("Low", 0) or 0), 2),
                    round(float(row.get("Close", 0) or 0), 2),
                    int(row.get("Volume", 0) or 0),
                ))

            conn.executemany(
                "INSERT OR IGNORE INTO historical_prices (ts, ticker, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows
            )
            conn.commit()
            logger.info(f"  {symbol}: {len(rows)} rows")
        except Exception as e:
            logger.warning(f"  {symbol} failed: {e}")


def backfill_macro_snapshots(conn):
    """Build daily macro regime snapshots from historical_prices."""
    cursor = conn.execute("""
        SELECT DISTINCT hp.ts
        FROM historical_prices hp
        WHERE hp.ticker = 'SPY'
        ORDER BY hp.ts
    """)
    dates = [row[0] for row in cursor.fetchall()]

    rows = []
    for date in dates:
        vix = conn.execute("SELECT close FROM historical_prices WHERE ts=? AND ticker='^VIX'", (date,)).fetchone()
        spy = conn.execute("SELECT close FROM historical_prices WHERE ts=? AND ticker='SPY'", (date,)).fetchone()
        tnx = conn.execute("SELECT close FROM historical_prices WHERE ts=? AND ticker='^TNX'", (date,)).fetchone()
        fxi = conn.execute("SELECT close FROM historical_prices WHERE ts=? AND ticker='FXI'", (date,)).fetchone()

        vix_val = vix[0] if vix else 0
        spy_val = spy[0] if spy else 0
        tnx_val = tnx[0] if tnx else 0
        fxi_val = fxi[0] if fxi else 0

        # Simple regime classification
        regime = "NEUTRAL"
        if vix_val > 30:
            regime = "RISK_OFF"
        elif vix_val < 15 and spy_val > 0:
            regime = "RISK_ON"

        rows.append((date, vix_val, 0, spy_val, tnx_val, fxi_val, regime))

    conn.executemany(
        "INSERT OR IGNORE INTO macro_snapshots (ts, vix, vix_9d, spy, treasury_10y, fxi, regime) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    logger.info(f"Macro snapshots: {len(rows)} rows")


def backfill_ev_snapshots(conn):
    """Build daily EV sector snapshots from historical_prices."""
    cursor = conn.execute("""
        SELECT DISTINCT ts FROM historical_prices WHERE ticker='TSLA' ORDER BY ts
    """)
    dates = [row[0] for row in cursor.fetchall()]

    rows = []
    for date in dates:
        vals = {}
        for ticker, col in [("TSLA", "tsla"), ("RIVN", "rivn"), ("LCID", "lcid"), ("1211.HK", "byd"), ("DRIV", "driv")]:
            row = conn.execute("SELECT close FROM historical_prices WHERE ts=? AND ticker=?", (date, ticker)).fetchone()
            vals[col] = row[0] if row else 0

        rows.append((date, vals["tsla"], vals["rivn"], vals["lcid"], vals["byd"], vals["driv"]))

    conn.executemany(
        "INSERT OR IGNORE INTO ev_sector_snapshots (ts, tsla, rivn, lcid, byd, driv) VALUES (?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    logger.info(f"EV sector snapshots: {len(rows)} rows")


def backfill_all(period="2y"):
    """Run full backfill pipeline."""
    conn = _get_db()
    _ensure_tables(conn)

    print("=== Historical Data Backfill ===")
    print(f"Period: {period}")
    print(f"Database: {DB_PATH}\n")

    print("--- Prices ---")
    backfill_prices(conn, period=period)

    print("\n--- Macro Snapshots ---")
    backfill_macro_snapshots(conn)

    print("\n--- EV Sector Snapshots ---")
    backfill_ev_snapshots(conn)

    # Summary
    for table in ["historical_prices", "macro_snapshots", "ev_sector_snapshots"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"\n{table}: {count} rows")

    conn.close()
    print("\n=== Backfill Complete ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    period = sys.argv[1] if len(sys.argv) > 1 else "2y"
    backfill_all(period)
