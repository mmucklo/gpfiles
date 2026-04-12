"""
TSLA Alpha Engine: IBKR Account + Positions Fetcher
Retrieves live account summary, positions, fills, and P&L from IB Gateway.

CLI modes (for Go subprocess calls):
  python -m ingestion.ibkr_account account
  python -m ingestion.ibkr_account positions
  python -m ingestion.ibkr_account fills [hours]
  python -m ingestion.ibkr_account pnl
"""
import json
import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger("IBKRAccount")

DB_PATH = os.path.expanduser("~/tsla_alpha.db")


def _nan_to_zero(v) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
        return 0.0 if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return 0.0


def _get_ib():
    from ingestion.ibkr_feed import get_ibkr_feed
    feed = get_ibkr_feed()
    if not feed.is_connected():
        ok = feed.connect()
        if not ok:
            raise ConnectionError("IB Gateway not reachable on port " + str(feed.port))
    return feed._get_ib()


def get_account_summary() -> dict:
    """
    Returns: net_liquidation, cash_balance, buying_power,
             unrealized_pnl, realized_pnl, equity_with_loan, ts
    """
    ib = _get_ib()
    summary = ib.accountSummary()

    result = {
        "net_liquidation":  0.0,
        "cash_balance":     0.0,
        "buying_power":     0.0,
        "unrealized_pnl":   0.0,
        "realized_pnl":     0.0,
        "equity_with_loan": 0.0,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    key_map = {
        "NetLiquidation":       "net_liquidation",
        "TotalCashValue":       "cash_balance",
        "BuyingPower":          "buying_power",
        "UnrealizedPnL":        "unrealized_pnl",
        "RealizedPnL":          "realized_pnl",
        "EquityWithLoanValue":  "equity_with_loan",
    }
    for item in summary:
        if item.tag in key_map and item.currency == "USD":
            result[key_map[item.tag]] = _nan_to_zero(item.value)

    return result


def get_positions() -> list:
    """
    Returns list of position dicts.
    signal_id and catalyst are looked up from the signals table by ticker+strike+expiry.
    """
    ib = _get_ib()
    ib.reqMarketDataType(3)  # delayed data for paper account
    positions = ib.positions()
    result = []

    for pos in positions:
        contract = pos.contract
        ticker   = contract.symbol
        qty      = pos.position
        avg_cost = _nan_to_zero(pos.avgCost)

        # Get current price
        current_price = 0.0
        try:
            from ib_insync import Stock, Option
            if contract.secType == "OPT":
                mkt = ib.reqMktData(contract, "", False, False)
            else:
                mkt = ib.reqMktData(contract, "", False, False)
            ib.sleep(1.0)
            p = mkt.last or mkt.close or (
                (mkt.bid + mkt.ask) / 2
                if mkt.bid and mkt.ask and not math.isnan(mkt.bid) and not math.isnan(mkt.ask)
                else None
            )
            current_price = _nan_to_zero(p)
            ib.cancelMktData(contract)
        except Exception as e:
            logger.debug(f"Price fetch for {ticker} failed: {e}")

        multiplier = float(contract.multiplier or 100) if contract.secType == "OPT" else 1.0
        unrealized_pnl = (current_price - avg_cost) * qty * multiplier if current_price else 0.0
        market_value   = current_price * qty * multiplier if current_price else 0.0

        # Parse option fields
        strike      = 0
        expiration  = ""
        option_type = ""
        delta       = 0.0
        iv          = 0.0
        if contract.secType == "OPT":
            strike      = int(float(contract.strike or 0))
            expiration  = _fmt_expiry(contract.lastTradeDateOrContractMonth)
            option_type = "CALL" if contract.right == "C" else "PUT"

        # Look up matching signal from DB
        signal_id = ""
        catalyst  = ""
        model_name = ""
        try:
            signal_id, catalyst, model_name = _lookup_signal(ticker, strike, expiration, option_type)
        except Exception:
            pass

        result.append({
            "ticker":         ticker,
            "sec_type":       contract.secType,
            "qty":            int(qty),
            "avg_cost":       avg_cost,
            "current_price":  current_price,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "market_value":   round(market_value, 2),
            "option_type":    option_type,
            "strike":         strike,
            "expiration":     expiration,
            "delta":          delta,
            "iv":             iv,
            "signal_id":      signal_id,
            "catalyst":       catalyst,
            "model_id":       model_name,
        })

    return result


def get_fills(since_hours: int = 24) -> list:
    """Returns recent executions from ib.reqExecutions()."""
    ib = _get_ib()

    from ib_insync import ExecutionFilter
    since_ts = time.time() - since_hours * 3600
    fills_raw = ib.reqExecutions(ExecutionFilter())
    result = []
    for fill in fills_raw:
        exec_ = fill.execution
        comm  = fill.commissionReport
        try:
            ts_str = exec_.time  # format: "20260401  12:34:56"
            ts_dt  = datetime.strptime(ts_str.strip(), "%Y%m%d  %H:%M:%S")
            ts_epoch = ts_dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            ts_epoch = time.time()
            ts_str   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if ts_epoch < since_ts:
            continue

        result.append({
            "order_id":   str(exec_.orderId),
            "ticker":     fill.contract.symbol,
            "side":       exec_.side,
            "qty":        int(exec_.shares),
            "fill_price": _nan_to_zero(exec_.price),
            "commission": _nan_to_zero(comm.commission) if comm else 0.0,
            "account":    exec_.acctNumber,
            "ts":         ts_str,
        })
    return result


def get_pnl() -> dict:
    """Returns {daily_pnl, unrealized_pnl, realized_pnl}."""
    ib = _get_ib()
    pnl_list = ib.pnl()
    result = {"daily_pnl": 0.0, "unrealized_pnl": 0.0, "realized_pnl": 0.0}
    for p in pnl_list:
        result["daily_pnl"]      += _nan_to_zero(p.dailyPnL)
        result["unrealized_pnl"] += _nan_to_zero(p.unrealizedPnL)
        result["realized_pnl"]   += _nan_to_zero(p.realizedPnL)
    return result


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_expiry(raw: str) -> str:
    """Convert YYYYMMDD → YYYY-MM-DD."""
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _lookup_signal(ticker: str, strike: int, expiration: str, option_type: str):
    """Return (signal_id, catalyst, model_id) from DB. Best-effort."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT id, raw_json, model_id FROM signals
               WHERE ticker=? AND strike=? AND expiration_date=? AND option_type=?
               ORDER BY ts DESC LIMIT 1""",
            (ticker, strike, expiration, option_type),
        ).fetchone()
        conn.close()
        if row:
            raw = json.loads(row["raw_json"] or "{}")
            catalyst = raw.get("confidence_rationale", "")
            return row["id"], catalyst, row["model_id"] or ""
    except Exception:
        pass
    return "", "", ""


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    mode = sys.argv[1] if len(sys.argv) > 1 else "account"

    try:
        if mode == "account":
            print(json.dumps(get_account_summary()))
        elif mode == "positions":
            print(json.dumps(get_positions()))
        elif mode == "fills":
            hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
            print(json.dumps(get_fills(hours)))
        elif mode == "pnl":
            print(json.dumps(get_pnl()))
        else:
            print(json.dumps({"error": f"Unknown mode: {mode}"}))
    except ConnectionError as e:
        print(json.dumps({"error": str(e), "ibkr_connected": False}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
