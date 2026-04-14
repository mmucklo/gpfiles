-- TSLA Alpha Engine: SQLite Schema
-- Designed to be run idempotently (CREATE TABLE IF NOT EXISTS).

-- fills_audit: immutable log of every Kelly sizing decision.
-- Purpose: audit trail for regime-conditional Kelly (Feature 3).
-- One row per signal that passed confidence threshold and entered sizing.
CREATE TABLE IF NOT EXISTS fills_audit (
  id VARCHAR(36) PRIMARY KEY,
  ts DATETIME NOT NULL,
  model_id VARCHAR(64),
  regime VARCHAR(16),         -- RISK_ON | RISK_OFF | NEUTRAL
  vix FLOAT,                  -- VIX spot at time of sizing
  kelly_base_fraction FLOAT,  -- VIX-tier fraction (0.20 / 0.35 / 0.50)
  vol_ratio FLOAT,            -- realized_vol / implied_vol
  regime_multiplier FLOAT,    -- 0.5 if RISK_OFF, else 1.0
  final_multiplier FLOAT,     -- kelly_base * min(1, vol_ratio) * regime_multiplier
  contracts_sized INT,        -- final qty after all adjustments
  kelly_wager_pct FLOAT,
  confidence FLOAT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS signals (
  id VARCHAR(36) PRIMARY KEY,
  ts DATETIME NOT NULL,
  model_id VARCHAR(64),
  direction VARCHAR(16),
  confidence FLOAT,
  ticker VARCHAR(16),
  underlying_price FLOAT,
  price_source VARCHAR(64),
  strike INT,
  option_type VARCHAR(8),
  expiration_date DATE,
  implied_volatility FLOAT,
  kelly_wager_pct FLOAT,
  quantity INT,
  strategy_code VARCHAR(64),
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS fills (
  id VARCHAR(36) PRIMARY KEY,
  ts DATETIME NOT NULL,
  order_id VARCHAR(64),
  signal_id VARCHAR(36),
  ticker VARCHAR(16),
  side VARCHAR(8),
  qty INT,
  fill_price FLOAT,
  commission FLOAT,
  account VARCHAR(32),
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS price_bars (
  ts DATETIME NOT NULL,
  ticker VARCHAR(16) NOT NULL,
  source VARCHAR(16) NOT NULL,
  open FLOAT,
  high FLOAT,
  low FLOAT,
  close FLOAT,
  volume BIGINT,
  PRIMARY KEY (ts, ticker, source)
);

CREATE TABLE IF NOT EXISTS options_snapshots (
  ts DATETIME NOT NULL,
  ticker VARCHAR(16),
  strike INT,
  expiration_date DATE,
  option_type VARCHAR(8),
  iv FLOAT,
  bid FLOAT,
  ask FLOAT,
  oi INT,
  delta FLOAT,
  PRIMARY KEY (ts, ticker, strike, expiration_date, option_type)
);

CREATE TABLE IF NOT EXISTS account_snapshots (
  ts DATETIME NOT NULL PRIMARY KEY,
  net_liquidation FLOAT,
  cash_balance FLOAT,
  buying_power FLOAT,
  unrealized_pnl FLOAT,
  realized_pnl FLOAT,
  equity_with_loan FLOAT
);

CREATE TABLE IF NOT EXISTS closed_trades (
  id VARCHAR(36) PRIMARY KEY,
  signal_id VARCHAR(36),
  ticker VARCHAR(16),
  option_type VARCHAR(8),
  strike INT,
  expiration_date DATE,
  entry_ts DATETIME,
  exit_ts DATETIME,
  entry_price FLOAT,
  exit_price FLOAT,
  qty INT,
  pnl FLOAT,
  pnl_pct FLOAT,
  win BOOLEAN,
  catalyst TEXT,
  model_id VARCHAR(64),
  confidence_at_entry FLOAT,
  exit_reason VARCHAR(64)
);

-- signal_feedback: human-in-the-loop annotations for signals.
-- Every cancel, comment, winner/loser tag, and follow-up note lives here.
-- Rows are IMMUTABLE (never deleted) — use resolved_by/resolved_at to close out.
CREATE TABLE IF NOT EXISTS signal_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,              -- fingerprint: ticker_opttype_expiry_action_strike_qty
    signal_snapshot TEXT NOT NULL,        -- JSON snapshot of the signal at feedback time
    ts_feedback TEXT NOT NULL,            -- ISO 8601 UTC
    user_comment TEXT NOT NULL,           -- verbatim; never trim or normalize
    tag TEXT,                             -- bad_entry|bad_strike|wrong_direction|right_idea_wrong_size|
                                          -- expired_worthless|late_signal|commission_dominated|good_signal|other
    action TEXT NOT NULL,                 -- COMMENT|CANCEL|FOLLOWUP|MARK_WINNER|MARK_LOSER
    reviewer TEXT NOT NULL DEFAULT 'user',
    resolved_by TEXT,                     -- PR or commit that addressed this (nullable)
    resolved_at TEXT                      -- ISO 8601 UTC when resolved (nullable)
);

CREATE INDEX IF NOT EXISTS idx_signal_feedback_ts     ON signal_feedback(ts_feedback);
CREATE INDEX IF NOT EXISTS idx_signal_feedback_tag    ON signal_feedback(tag);
CREATE INDEX IF NOT EXISTS idx_signal_feedback_action ON signal_feedback(action);
