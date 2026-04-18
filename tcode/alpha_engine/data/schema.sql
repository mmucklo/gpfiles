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
  selection_score REAL,      -- Phase 14: strike-selector composite score (0–1)
  chop_regime TEXT,          -- Phase 14: TRENDING / MIXED / CHOPPY at emission time
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

-- process_heartbeats: liveness pulses from every long-running component.
-- Each component writes one row per cycle; the API reads the latest per component.
CREATE TABLE IF NOT EXISTS process_heartbeats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    component   TEXT    NOT NULL,   -- "publisher" | "intel_refresh" | "options_chain_api" | ...
    ts          TEXT    NOT NULL,   -- ISO 8601 UTC
    status      TEXT    NOT NULL,   -- "ok" | "degraded" | "error"
    detail      TEXT,               -- last error / note, nullable
    pid         INTEGER,
    uptime_sec  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_process_heartbeats_component_ts
    ON process_heartbeats(component, ts DESC);

-- system_alerts: written when any component transitions to RED status.
-- Surfaced in the dashboard event feed.
CREATE TABLE IF NOT EXISTS system_alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,   -- ISO 8601 UTC
    component TEXT    NOT NULL,
    status    TEXT    NOT NULL,   -- "error" | "degraded"
    message   TEXT    NOT NULL
);

-- trade_ledger: Phase 16 — human-approved trade record with full context.
-- Every executed trade flows through the approval queue before landing here.
CREATE TABLE IF NOT EXISTS trade_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_entry TEXT NOT NULL,
    ts_exit TEXT,
    strategy TEXT NOT NULL,          -- MOMENTUM | CONDOR | VERTICAL | JADE_LIZARD | STRADDLE | GAMMA_SCALP
    regime_at_entry TEXT,            -- TRENDING | FLAT | CHOPPY | EVENT_DRIVEN | UNCERTAIN
    regime_at_exit TEXT,
    direction TEXT,                  -- BULLISH | BEARISH | NEUTRAL
    legs TEXT NOT NULL,              -- JSON array of {strike, type, action, quantity, fill_price}
    entry_price REAL,               -- net debit/credit per unit
    exit_price REAL,
    quantity INTEGER,
    commission REAL,
    gross_pnl REAL,
    net_pnl REAL,
    hold_duration_sec INTEGER,
    stop_type TEXT,                  -- TP | SL | TIME_STOP | MANUAL | TRAILING
    signals_contributing TEXT,       -- JSON array of signal fingerprints
    confidence_at_entry REAL,
    kelly_fraction REAL,
    human_override TEXT,             -- "approved" | "adjusted:qty=5" | "skipped" | "override:direction=BEARISH"
    slippage REAL,                   -- expected fill - actual fill
    tags TEXT,                       -- JSON array: ["winner", "cut-early", "hit-TP"]
    notes TEXT                       -- user's post-trade comment
);
CREATE INDEX IF NOT EXISTS idx_trade_ledger_ts ON trade_ledger(ts_entry);
CREATE INDEX IF NOT EXISTS idx_trade_ledger_strategy ON trade_ledger(strategy);

-- trade_proposals: Phase 16 — incoming proposals awaiting human approval.
-- Proposals expire after PROPOSAL_TTL_SEC (default 60s) if not acted on.
CREATE TABLE IF NOT EXISTS trade_proposals (
    id TEXT PRIMARY KEY,             -- UUID
    ts_created TEXT NOT NULL,        -- ISO 8601 UTC
    ts_expires TEXT NOT NULL,        -- ts_created + TTL
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | executed | skipped | expired | adjusted
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    legs TEXT NOT NULL,              -- JSON array of leg specs
    entry_price REAL,
    stop_price REAL,
    target_price REAL,
    kelly_fraction REAL,
    quantity INTEGER,
    confidence REAL,
    regime_snapshot TEXT,            -- JSON regime state at proposal time
    signals_contributing TEXT,       -- JSON array
    raw_signal TEXT                  -- original NATS message JSON
);
CREATE INDEX IF NOT EXISTS idx_trade_proposals_status ON trade_proposals(status, ts_created DESC);

-- selected_strategy: Phase 16 — user's locked strategy for the session.
CREATE TABLE IF NOT EXISTS selected_strategy (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    strategy TEXT NOT NULL,
    locked_at TEXT NOT NULL,
    locked_by TEXT NOT NULL DEFAULT 'user'
);

-- Phase 17 — realtime_1min_bars: persisted 1-min TSLA bars from Tradier timesales.
-- The source column distinguishes from other price_bars sources.
-- price_bars already exists; this view makes Phase 17 bars queryable by ts range.
CREATE TABLE IF NOT EXISTS realtime_1min_bars (
    ts      TEXT    NOT NULL,   -- ISO 8601 local (Tradier format)
    open    REAL    NOT NULL,
    high    REAL    NOT NULL,
    low     REAL    NOT NULL,
    close   REAL    NOT NULL,
    volume  INTEGER NOT NULL,
    vwap    REAL,
    PRIMARY KEY (ts)
);
CREATE INDEX IF NOT EXISTS idx_realtime_bars_ts ON realtime_1min_bars(ts DESC);

-- Phase 17 — managed_positions: tracks ATR stops, trailing levels, time stops.
-- One row per open/closed managed position (linked to trade_ledger.id).
CREATE TABLE IF NOT EXISTS managed_positions (
    trade_id        INTEGER PRIMARY KEY,  -- FK to trade_ledger.id
    entry_price     REAL    NOT NULL,
    entry_time      TEXT    NOT NULL,
    quantity        INTEGER NOT NULL,
    direction       TEXT    NOT NULL,   -- LONG | SHORT
    strategy        TEXT    NOT NULL,
    initial_stop    REAL,
    current_stop    REAL,
    target          REAL,
    time_stop_at    TEXT    NOT NULL,
    stop_multiplier REAL,
    target_multiplier REAL,
    trailing_engaged INTEGER NOT NULL DEFAULT 0,
    is_open         INTEGER NOT NULL DEFAULT 1,
    exit_price      REAL,
    exit_time       TEXT,
    stop_type       TEXT                -- TP | SL | TIME_STOP | TRAILING | MANUAL
);
