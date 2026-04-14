"""
TSLA Alpha Engine: Async Data Logger
Persists signals, fills, price bars, account snapshots, and options snapshots
to ~/tsla_alpha.db (SQLite with WAL mode for concurrent reads).

All writes go through an asyncio queue so the publisher hot path never blocks.
"""
import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from consensus import ModelSignal

logger = logging.getLogger("DataLogger")

DB_PATH = os.path.expanduser("~/tsla_alpha.db")


class DataLogger:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    def init(self):
        """Open DB and start background writer. Call once at startup."""
        from data.init_db import init_db
        self._conn = init_db(self.db_path)
        logger.info(f"DataLogger: connected to {self.db_path}")

    async def start(self):
        """Start the async writer loop. Must be called inside an event loop."""
        if self._conn is None:
            self.init()
        self._task = asyncio.create_task(self._writer_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._conn:
            self._conn.close()

    # ── public API ────────────────────────────────────────────────────────

    async def log_signal(self, signal: "ModelSignal") -> str:
        """Persist a ModelSignal. Returns the generated UUID."""
        sid = str(uuid.uuid4())
        # Phase 14: extract strike-selection score and chop regime if set on the signal
        strike_meta = getattr(signal, "strike_selection_meta", None) or {}
        selection_score = strike_meta.get("score") if isinstance(strike_meta, dict) else None
        payload = {
            "id":                sid,
            "ts":                _isotime(signal.timestamp),
            "model_id":          signal.model_id.name if hasattr(signal.model_id, "name") else str(signal.model_id),
            "direction":         signal.direction.name if hasattr(signal.direction, "name") else str(signal.direction),
            "confidence":        signal.confidence,
            "ticker":            getattr(signal, "ticker", "TSLA"),
            "underlying_price":  getattr(signal, "underlying_price", 0.0),
            "price_source":      getattr(signal, "price_source", ""),
            "strike":            int(getattr(signal, "recommended_strike", 0) or 0),
            "option_type":       getattr(signal, "option_type", ""),
            "expiration_date":   getattr(signal, "expiration_date", ""),
            "implied_volatility": getattr(signal, "implied_volatility", 0.0),
            "kelly_wager_pct":   getattr(signal, "kelly_wager_pct", 0.0),
            "quantity":          getattr(signal, "quantity", 0),
            "strategy_code":     getattr(signal, "strategy_code", ""),
            "selection_score":   selection_score,
            "chop_regime":       getattr(signal, "chop_label", None),
            "raw_json":          None,
        }
        # Store full signal as raw_json
        try:
            raw = {k: (v.name if hasattr(v, "name") else v) for k, v in signal.__dict__.items()}
            payload["raw_json"] = json.dumps(raw)
        except Exception:
            pass

        await self._queue.put(("signal", payload))
        return sid

    async def log_fill(self, fill_dict: dict) -> str:
        """Persist an IBKR fill."""
        fid = fill_dict.get("id") or str(uuid.uuid4())
        payload = {
            "id":         fid,
            "ts":         fill_dict.get("ts", _isotime(time.time())),
            "order_id":   fill_dict.get("order_id", ""),
            "signal_id":  fill_dict.get("signal_id", ""),
            "ticker":     fill_dict.get("ticker", ""),
            "side":       fill_dict.get("side", ""),
            "qty":        fill_dict.get("qty", 0),
            "fill_price": fill_dict.get("fill_price", 0.0),
            "commission": fill_dict.get("commission", 0.0),
            "account":    fill_dict.get("account", ""),
            "raw_json":   json.dumps(fill_dict),
        }
        await self._queue.put(("fill", payload))
        return fid

    async def log_price_bar(self, ticker: str, source: str, bar: dict):
        """Persist a 1-minute OHLCV bar."""
        payload = {
            "ts":     bar.get("ts", _isotime(time.time())),
            "ticker": ticker,
            "source": source,
            "open":   bar.get("open", 0.0),
            "high":   bar.get("high", 0.0),
            "low":    bar.get("low", 0.0),
            "close":  bar.get("close", 0.0),
            "volume": bar.get("volume", 0),
        }
        await self._queue.put(("price_bar", payload))

    async def log_account_snapshot(self, account: dict):
        """Persist an account snapshot (net liq, cash, buying power, P&L)."""
        payload = {
            "ts":               account.get("ts", _isotime(time.time())),
            "net_liquidation":  account.get("net_liquidation", 0.0),
            "cash_balance":     account.get("cash_balance", 0.0),
            "buying_power":     account.get("buying_power", 0.0),
            "unrealized_pnl":   account.get("unrealized_pnl", 0.0),
            "realized_pnl":     account.get("realized_pnl", 0.0),
            "equity_with_loan": account.get("equity_with_loan", 0.0),
        }
        await self._queue.put(("account_snapshot", payload))

    async def log_kelly_audit(self, audit: dict) -> None:
        """Persist a Kelly sizing decision to fills_audit for post-trade attribution."""
        await self._queue.put(("fills_audit", audit))

    async def log_heartbeat(self, component: str, status: str = "ok",
                            detail: str | None = None,
                            pid: int | None = None,
                            uptime_sec: int | None = None) -> None:
        """Persist a process heartbeat row (non-blocking, queued)."""
        payload = {
            "component":  component,
            "ts":         _isotime(time.time()),
            "status":     status,
            "detail":     detail,
            "pid":        pid,
            "uptime_sec": uptime_sec,
        }
        await self._queue.put(("heartbeat", payload))

    async def log_system_alert(self, component: str, status: str, message: str) -> None:
        """Persist a system alert when a component goes RED."""
        payload = {
            "ts":        _isotime(time.time()),
            "component": component,
            "status":    status,
            "message":   message,
        }
        await self._queue.put(("system_alert", payload))

    async def log_options_snapshot(self, chain: list):
        """Persist a list of options chain rows."""
        ts = _isotime(time.time())
        for row in chain:
            payload = {
                "ts":              ts,
                "ticker":          row.get("ticker", "TSLA"),
                "strike":          int(row.get("strike", 0)),
                "expiration_date": row.get("expiry", ""),
                "option_type":     row.get("option_type", ""),
                "iv":              row.get("iv", 0.0),
                "bid":             row.get("bid", 0.0),
                "ask":             row.get("ask", 0.0),
                "oi":              row.get("oi", 0),
                "delta":           row.get("delta", 0.0),
            }
            await self._queue.put(("options_snapshot", payload))

    # ── internal writer ───────────────────────────────────────────────────

    async def _writer_loop(self):
        while True:
            try:
                kind, payload = await self._queue.get()
                self._write(kind, payload)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"DataLogger write error ({kind}): {e}")

    def _write(self, kind: str, payload: dict):
        c = self._conn
        if c is None:
            return
        try:
            if kind == "signal":
                c.execute(
                    """INSERT OR IGNORE INTO signals
                       (id,ts,model_id,direction,confidence,ticker,underlying_price,
                        price_source,strike,option_type,expiration_date,
                        implied_volatility,kelly_wager_pct,quantity,strategy_code,
                        selection_score,chop_regime,raw_json)
                       VALUES (:id,:ts,:model_id,:direction,:confidence,:ticker,
                               :underlying_price,:price_source,:strike,:option_type,
                               :expiration_date,:implied_volatility,:kelly_wager_pct,
                               :quantity,:strategy_code,
                               :selection_score,:chop_regime,:raw_json)""",
                    payload,
                )
            elif kind == "fill":
                c.execute(
                    """INSERT OR IGNORE INTO fills
                       (id,ts,order_id,signal_id,ticker,side,qty,fill_price,commission,account,raw_json)
                       VALUES (:id,:ts,:order_id,:signal_id,:ticker,:side,:qty,
                               :fill_price,:commission,:account,:raw_json)""",
                    payload,
                )
            elif kind == "price_bar":
                c.execute(
                    """INSERT OR REPLACE INTO price_bars
                       (ts,ticker,source,open,high,low,close,volume)
                       VALUES (:ts,:ticker,:source,:open,:high,:low,:close,:volume)""",
                    payload,
                )
            elif kind == "account_snapshot":
                c.execute(
                    """INSERT OR REPLACE INTO account_snapshots
                       (ts,net_liquidation,cash_balance,buying_power,
                        unrealized_pnl,realized_pnl,equity_with_loan)
                       VALUES (:ts,:net_liquidation,:cash_balance,:buying_power,
                               :unrealized_pnl,:realized_pnl,:equity_with_loan)""",
                    payload,
                )
            elif kind == "fills_audit":
                c.execute(
                    """INSERT OR IGNORE INTO fills_audit
                       (id,ts,model_id,regime,vix,kelly_base_fraction,vol_ratio,
                        regime_multiplier,final_multiplier,contracts_sized,
                        kelly_wager_pct,confidence,raw_json)
                       VALUES (:id,:ts,:model_id,:regime,:vix,:kelly_base_fraction,
                               :vol_ratio,:regime_multiplier,:final_multiplier,
                               :contracts_sized,:kelly_wager_pct,:confidence,:raw_json)""",
                    payload,
                )
            elif kind == "options_snapshot":
                c.execute(
                    """INSERT OR REPLACE INTO options_snapshots
                       (ts,ticker,strike,expiration_date,option_type,iv,bid,ask,oi,delta)
                       VALUES (:ts,:ticker,:strike,:expiration_date,:option_type,
                               :iv,:bid,:ask,:oi,:delta)""",
                    payload,
                )
            elif kind == "heartbeat":
                c.execute(
                    """INSERT INTO process_heartbeats
                       (component,ts,status,detail,pid,uptime_sec)
                       VALUES (:component,:ts,:status,:detail,:pid,:uptime_sec)""",
                    payload,
                )
            elif kind == "system_alert":
                c.execute(
                    """INSERT INTO system_alerts (ts,component,status,message)
                       VALUES (:ts,:component,:status,:message)""",
                    payload,
                )
            c.commit()
        except Exception as e:
            logger.warning(f"DB write failed ({kind}): {e}")


def _isotime(ts: float) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
