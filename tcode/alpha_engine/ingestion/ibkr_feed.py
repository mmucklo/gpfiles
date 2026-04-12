"""
TSLA Alpha Engine: IBKR Market Data Feed
Primary data source via Interactive Brokers TWS / IB Gateway using ib_insync.

Cache TTL: 30s (real-time data — tighter than yfinance/TV 60s TTL).
Falls back gracefully when IB Gateway is not running (IBKRNotConnectedError).
"""
import math
import time
import threading
import logging
import os
from typing import Optional
from dotenv import load_dotenv

logger = logging.getLogger("IBKRFeed")


class IBKRNotConnectedError(Exception):
    """Raised when IB Gateway is unreachable or disconnected."""
    pass


class IBKRFeed:
    CACHE_TTL = 30        # seconds — real-time data ages faster than yfinance
    REQUEST_TIMEOUT = 10  # seconds per market data request

    def __init__(self):
        load_dotenv()
        self.host = "127.0.0.1"
        self.port = int(os.getenv("IBKR_PORT", "7497"))
        self.client_id = 1
        self._connected = False
        self._lock = threading.Lock()
        self._spot_cache: Optional[tuple] = None   # (timestamp, dict)
        self._chain_cache: Optional[tuple] = None  # (timestamp, list)
        self._ib = None  # created lazily

    # ── connection ──────────────────────────────────────────────────────────

    def _get_ib(self):
        if self._ib is None:
            from ib_insync import IB
            self._ib = IB()
        return self._ib

    def connect(self) -> bool:
        """Attempt to connect to IB Gateway/TWS. Returns True on success."""
        try:
            ib = self._get_ib()
            ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.REQUEST_TIMEOUT)
            self._connected = True
            logger.info(f"IBKR connected at {self.host}:{self.port} (paper trading)")
            return True
        except Exception as e:
            logger.warning(f"IBKR connect failed ({self.host}:{self.port}): {e}")
            self._connected = False
            return False

    def disconnect(self):
        if self._connected and self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._connected = False

    def is_connected(self) -> bool:
        if not self._connected or self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    # ── spot price ──────────────────────────────────────────────────────────

    def get_spot(self, symbol: str = "TSLA") -> dict:
        """
        Returns {price, bid, ask, source, fetched_at}.
        Uses 30s TTL cache. Raises IBKRNotConnectedError if not connected.
        """
        with self._lock:
            now = time.time()
            if self._spot_cache and now - self._spot_cache[0] < self.CACHE_TTL:
                logger.debug("IBKR spot: cache hit")
                return self._spot_cache[1]

        if not self.is_connected():
            raise IBKRNotConnectedError("IB Gateway not connected — run ~/bin/start_ibgw.sh")

        try:
            from ib_insync import Stock
            ib = self._get_ib()
            # Request delayed market data (type 3) — avoids real-time subscription requirement
            ib.reqMarketDataType(3)
            contract = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "", False, False)

            def _valid(v) -> bool:
                return v is not None and not (isinstance(v, float) and math.isnan(v)) and v > 0

            # Poll up to REQUEST_TIMEOUT for a valid price
            deadline = time.time() + self.REQUEST_TIMEOUT
            while time.time() < deadline:
                ib.sleep(0.1)
                price = (ticker.last if _valid(ticker.last) else
                         ticker.close if _valid(ticker.close) else
                         ((ticker.bid + ticker.ask) / 2
                          if _valid(ticker.bid) and _valid(ticker.ask) else None))
                if price and price > 0:
                    break

            ib.cancelMktData(contract)

            price = (ticker.last if _valid(ticker.last) else
                     ticker.close if _valid(ticker.close) else
                     ((ticker.bid + ticker.ask) / 2
                      if _valid(ticker.bid) and _valid(ticker.ask) else 0.0))
            result = {
                "price":      float(price) if price else 0.0,
                "bid":        float(ticker.bid) if _valid(ticker.bid) else 0.0,
                "ask":        float(ticker.ask) if _valid(ticker.ask) else 0.0,
                "source":     "ibkr",
                "fetched_at": time.time(),
            }
            with self._lock:
                self._spot_cache = (time.time(), result)
            logger.info(f"IBKR spot {symbol}: ${result['price']:.2f}")
            return result
        except IBKRNotConnectedError:
            raise
        except Exception as e:
            raise IBKRNotConnectedError(f"IBKR market data error: {e}")

    # ── options chain ───────────────────────────────────────────────────────

    def get_options_chain(self, symbol: str = "TSLA", expiry: Optional[str] = None) -> list:
        """
        Returns list of {strike, expiry, option_type, iv, bid, ask, oi, source}.
        expiry should be YYYY-MM-DD or None (auto-select nearest).
        Raises IBKRNotConnectedError if not connected.
        """
        with self._lock:
            now = time.time()
            if self._chain_cache and now - self._chain_cache[0] < self.CACHE_TTL:
                logger.debug("IBKR chain: cache hit")
                return self._chain_cache[1]

        if not self.is_connected():
            raise IBKRNotConnectedError("IB Gateway not connected")

        try:
            from ib_insync import Stock, Option
            ib = self._get_ib()

            stock = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(stock)

            chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
            if not chains:
                raise IBKRNotConnectedError("No options chain data from IBKR")

            chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

            # Auto-select nearest expiry if not specified (IBKR uses YYYYMMDD)
            if expiry is None:
                from datetime import date
                today_str = date.today().strftime("%Y%m%d")
                expiry_ib = next(
                    (e for e in sorted(chain.expirations) if e >= today_str),
                    sorted(chain.expirations)[0] if chain.expirations else None,
                )
            else:
                expiry_ib = expiry.replace("-", "")

            if not expiry_ib:
                return []

            # Get current spot to center strike selection
            spot_approx = 380.0
            try:
                spot_data = self.get_spot(symbol)
                spot_approx = spot_data["price"] or spot_approx
            except Exception:
                pass

            # Select 20 strikes closest to ATM to avoid rate limits
            atm_strikes = sorted(chain.strikes, key=lambda s: abs(s - spot_approx))[:20]
            expiry_fmt = f"{expiry_ib[:4]}-{expiry_ib[4:6]}-{expiry_ib[6:]}"

            rows = []
            for strike in atm_strikes:
                for right, opt_type in [("C", "CALL"), ("P", "PUT")]:
                    try:
                        opt = Option(symbol, expiry_ib, strike, right, "SMART")
                        ib.qualifyContracts(opt)
                        ticker = ib.reqMktData(opt, "106", False, False)  # 106 = implied vol
                        ib.sleep(0.2)
                        rows.append({
                            "strike":      float(strike),
                            "expiry":      expiry_fmt,
                            "option_type": opt_type,
                            "iv":          float(ticker.impliedVolatility) if ticker.impliedVolatility else 0.0,
                            "bid":         float(ticker.bid) if ticker.bid else 0.0,
                            "ask":         float(ticker.ask) if ticker.ask else 0.0,
                            "oi":          int(ticker.volume or 0),
                            "source":      "ibkr",
                        })
                        ib.cancelMktData(opt)
                    except Exception as e:
                        logger.debug(f"Skip {opt_type} {strike}: {e}")
                        continue

            with self._lock:
                self._chain_cache = (time.time(), rows)
            logger.info(f"IBKR chain {symbol} {expiry_fmt}: {len(rows)} contracts")
            return rows
        except IBKRNotConnectedError:
            raise
        except Exception as e:
            raise IBKRNotConnectedError(f"IBKR chain error: {e}")

    # ── context manager ─────────────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()


# ── module-level singleton ───────────────────────────────────────────────────

_ibkr_feed: Optional[IBKRFeed] = None
_ibkr_singleton_lock = threading.Lock()


def get_ibkr_feed() -> IBKRFeed:
    global _ibkr_feed
    with _ibkr_singleton_lock:
        if _ibkr_feed is None:
            _ibkr_feed = IBKRFeed()
        return _ibkr_feed


# ── CLI entry point (used by ingestion/audit.py) ────────────────────────────

if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.WARNING)

    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    feed = IBKRFeed()

    result = {"connected": False, "spot": 0.0, "bid": 0.0, "ask": 0.0, "error": None}

    if feed.connect():
        try:
            spot = feed.get_spot(symbol)
            result["connected"] = True
            result["spot"] = spot["price"]
            result["bid"]   = spot.get("bid", 0.0)
            result["ask"]   = spot.get("ask", 0.0)
        except Exception as e:
            result["connected"] = feed.is_connected()
            result["error"] = str(e)
        finally:
            feed.disconnect()
    else:
        result["error"] = f"IB Gateway not running on {feed.host}:{feed.port}"

    print(json.dumps(result))
