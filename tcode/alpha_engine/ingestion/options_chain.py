"""
TSLA Alpha Engine: Real-Time Options Chain Ingestion
Fetches the TSLA options chain via yfinance and provides strike selection
anchored to real market data with liquidity filtering.

Source priority: IBKR (paper account) → TradingView → yfinance
Cache TTL: 60s — balances freshness vs. rate-limit safety.
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
import yfinance as yf

logger = logging.getLogger("OptionsChain")


def round_to_chain_increment(price: float, increment: float = 5.0) -> float:
    """Round a price to the nearest standard chain increment."""
    return round(price / increment) * increment


@dataclass
class OptionRow:
    strike: float
    option_type: str          # "CALL" or "PUT"
    expiration_date: str      # YYYY-MM-DD
    implied_volatility: float # annualised, e.g. 0.65 = 65%
    open_interest: int
    bid: float
    ask: float
    last_price: float

    @property
    def mid_price(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last_price

    @property
    def spread_pct(self) -> float:
        """Bid/ask spread as a fraction of mid. Low = liquid."""
        mid = self.mid_price
        if mid <= 0:
            return 1.0
        return (self.ask - self.bid) / mid


class OptionsChainCache:
    """
    Thin wrapper around yfinance.Ticker.option_chain() with a 60-second TTL.
    Returns the nearest expiry that has at least MIN_STRIKES liquid strikes.
    """

    MIN_OI = 100          # minimum open interest to consider a strike liquid
    MIN_STRIKES = 5       # minimum number of liquid strikes before expiry is usable
    CACHE_TTL = 60        # seconds

    def __init__(self, ticker: str = "TSLA"):
        self.ticker = ticker
        self._cache: dict = {}        # expiry_date -> (timestamp, list[OptionRow])
        self._expiry_list: list = []
        self._expiry_ts: float = 0.0

    # ── expiry list ───────────────────────────────────────────────────────────

    def _get_expiry_list(self) -> list:
        now = time.time()
        if now - self._expiry_ts < self.CACHE_TTL and self._expiry_list:
            return self._expiry_list
        try:
            t = yf.Ticker(self.ticker)
            self._expiry_list = list(t.options)
            self._expiry_ts = now
            logger.debug(f"Fetched {len(self._expiry_list)} expiry dates for {self.ticker}")
        except Exception as e:
            logger.warning(f"Failed to fetch expiry list: {e}")
        return self._expiry_list

    # ── chain for one expiry ──────────────────────────────────────────────────

    def _fetch_chain(self, expiry: str) -> list[OptionRow]:
        """Fetch and parse calls + puts for a given expiry date."""
        t = yf.Ticker(self.ticker)
        chain = t.option_chain(expiry)
        rows: list[OptionRow] = []

        for opt_type, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
            for _, r in df.iterrows():
                try:
                    rows.append(OptionRow(
                        strike=float(r["strike"]),
                        option_type=opt_type,
                        expiration_date=expiry,
                        implied_volatility=float(r.get("impliedVolatility", 0.0)),
                        open_interest=int(r.get("openInterest", 0)),
                        bid=float(r.get("bid", 0.0)),
                        ask=float(r.get("ask", 0.0)),
                        last_price=float(r.get("lastPrice", 0.0)),
                    ))
                except Exception:
                    continue
        return rows

    def get_chain(self, expiry: str) -> list[OptionRow]:
        """Return cached (or fresh) option rows for `expiry`."""
        now = time.time()
        cached = self._cache.get(expiry)
        if cached and now - cached[0] < self.CACHE_TTL:
            return cached[1]
        try:
            rows = self._fetch_chain(expiry)
            self._cache[expiry] = (now, rows)
            logger.info(f"Options chain loaded: {expiry} — {len(rows)} contracts")
            return rows
        except Exception as e:
            logger.warning(f"Chain fetch failed for {expiry}: {e}")
            return cached[1] if cached else []

    # ── public API ────────────────────────────────────────────────────────────

    def nearest_expiry_with_liquidity(self, min_dte: int = 1) -> Optional[str]:
        """
        Return the nearest expiry date that has >= MIN_STRIKES liquid strikes
        and is at least min_dte days away.
        """
        from datetime import date, timedelta
        today = date.today()
        cutoff = today + timedelta(days=min_dte)

        for expiry in self._get_expiry_list():
            try:
                exp_date = date.fromisoformat(expiry)
            except ValueError:
                continue
            if exp_date < cutoff:
                continue
            rows = self.get_chain(expiry)
            liquid = [r for r in rows if r.open_interest >= self.MIN_OI]
            if len(liquid) >= self.MIN_STRIKES:
                return expiry
        return None

    def snap_strike(
        self,
        spot: float,
        option_type: str,
        target_moneyness: float = 1.05,
        expiry: Optional[str] = None,
    ) -> tuple[float, float, str]:
        """
        Find the nearest liquid strike to `spot * target_moneyness`.

        Returns: (snapped_strike, implied_volatility, expiry_date)
        Falls back to the simple formula if no chain data is available.
        """
        if expiry is None:
            expiry = self.nearest_expiry_with_liquidity(min_dte=1)

        if not expiry:
            fallback = round_to_chain_increment(spot * target_moneyness)
            logger.warning("No liquid expiry found — using formula strike")
            return fallback, 0.0, ""

        rows = self.get_chain(expiry)
        candidates = [
            r for r in rows
            if r.option_type == option_type and r.open_interest >= self.MIN_OI
        ]
        if not candidates:
            fallback = round_to_chain_increment(spot * target_moneyness)
            logger.warning(f"No liquid {option_type} strikes for {expiry} — using formula")
            return fallback, 0.0, expiry

        target_strike = spot * target_moneyness
        best = min(candidates, key=lambda r: abs(r.strike - target_strike))
        logger.info(
            f"Snapped {option_type} strike: {best.strike} "
            f"(target={target_strike:.1f}, IV={best.implied_volatility:.2%}, "
            f"OI={best.open_interest})"
        )
        return best.strike, best.implied_volatility, expiry

    def get_iv_for_strike(
        self,
        strike: float,
        option_type: str,
        expiry: str,
    ) -> float:
        """Look up IV for an exact strike/type/expiry. Returns 0.0 if not found."""
        rows = self.get_chain(expiry)
        for r in rows:
            if r.option_type == option_type and abs(r.strike - strike) < 0.01:
                return r.implied_volatility
        return 0.0


# ── multi-source spot price with fallback chain ───────────────────────────────

def get_spot_with_fallback(symbol: str = "TSLA") -> tuple[float, str]:
    """
    Fetch spot price with 3-tier fallback: IBKR → TradingView → yfinance.
    Returns (price, source_name).
    """
    # 1. Try IBKR (primary — real-time paper account data)
    try:
        from ingestion.ibkr_feed import get_ibkr_feed, IBKRNotConnectedError
        feed = get_ibkr_feed()
        if feed.is_connected():
            spot = feed.get_spot(symbol)
            price = spot["price"]
            if price and price > 0:
                logger.info(f"Spot from IBKR: ${price:.2f}")
                return price, "ibkr"
    except Exception as e:
        logger.debug(f"IBKR spot skipped: {e}")

    # 2. Try TradingView
    try:
        from ingestion.tv_feed import get_tv_cache
        tv_price = get_tv_cache().get_spot(symbol)
        if tv_price and tv_price > 0:
            logger.info(f"Spot from TradingView: ${tv_price:.2f}")
            return tv_price, "tv"
    except Exception as e:
        logger.debug(f"TradingView spot skipped: {e}")

    # 3. Fallback to yfinance
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            logger.info(f"Spot from yfinance: ${price:.2f}")
            return price, "yfinance"
    except Exception as e:
        logger.warning(f"yfinance spot failed: {e}")

    logger.error(f"All spot sources failed for {symbol}")
    return 0.0, "unavailable"


# Module-level singleton — shared across all publisher calls
_chain_cache: Optional[OptionsChainCache] = None


def get_chain_cache() -> OptionsChainCache:
    global _chain_cache
    if _chain_cache is None:
        _chain_cache = OptionsChainCache("TSLA")
    return _chain_cache


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cache = OptionsChainCache("TSLA")
    expiry = cache.nearest_expiry_with_liquidity(min_dte=1)
    print(f"Nearest liquid expiry: {expiry}")
    if expiry:
        strike, iv, exp = cache.snap_strike(380.0, "CALL", target_moneyness=1.05, expiry=expiry)
        print(f"Snapped CALL strike: ${strike:.1f}  IV: {iv:.1%}  Expiry: {exp}")
        strike, iv, exp = cache.snap_strike(380.0, "PUT", target_moneyness=0.95, expiry=expiry)
        print(f"Snapped PUT strike:  ${strike:.1f}  IV: {iv:.1%}  Expiry: {exp}")
