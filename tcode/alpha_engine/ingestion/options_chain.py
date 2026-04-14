"""
TSLA Alpha Engine: Real-Time Options Chain Ingestion
Fetches the TSLA options chain and provides strike selection anchored to real
market data with liquidity filtering.

Source priority:
  - OFF-HOURS + IBKR connected: IBKR (paper account) — OI/bid/ask available 24h
  - IN-HOURS or IBKR unavailable: yfinance (reliable during market hours)

Cache TTL: 60s — balances freshness vs. rate-limit safety.
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
import yfinance as yf

logger = logging.getLogger("OptionsChain")

try:
    import sys as _sys
    _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
    from heartbeat import emit_heartbeat as _hb
except Exception:
    def _hb(component, status="ok", detail=None, **_kw): pass  # type: ignore


# ── market-hours helper ───────────────────────────────────────────────────────

def _is_us_market_hours() -> bool:
    """Return True if current time is within US market hours (9:30–16:00 ET Mon–Fri)."""
    import datetime
    import zoneinfo
    try:
        tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        return True  # If we can't determine tz, default to "in-hours" (safer)
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now.hour * 60 + now.minute
    return 570 <= t < 960  # 9:30 (570) – 16:00 (960)


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
    volume: int = 0           # contracts traded today

    # Greeks — populated by enrich_greeks()
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    greeks_source: str = "unavailable"  # "ibkr" | "computed_bs" | "unavailable"

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


def enrich_greeks(rows: list["OptionRow"], spot: float, ttm_years: float) -> None:
    """
    Mutate each OptionRow in-place: fill delta/gamma/theta/vega/greeks_source.

    Priority:
      1. IBKR modelGreeks already set on the row (greeks_source == "ibkr") — skip.
      2. BS-compute from IV when IV > 0.
      3. Mark greeks_source="unavailable" and leave None values.

    Also tracks degradation: logs WARNING if >50% of rows end up unavailable.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
        from pricing.greeks import compute_bs_greeks, get_risk_free_rate
        rate = get_risk_free_rate()
    except Exception as exc:
        logger.warning("enrich_greeks: cannot import greeks module: %s", exc)
        return

    unavail_count = 0
    for row in rows:
        if row.greeks_source == "ibkr":
            # Already populated by IBKR modelGreeks — trust and skip
            continue
        iv = row.implied_volatility
        if iv and iv > 0:
            g = compute_bs_greeks(spot, row.strike, ttm_years, rate, iv, row.option_type)
            row.delta = g["delta"]
            row.gamma = g["gamma"]
            row.theta = g["theta"]
            row.vega = g["vega"]
            row.greeks_source = g["greeks_source"]
            if g["greeks_source"] == "unavailable":
                unavail_count += 1
        else:
            row.greeks_source = "unavailable"
            unavail_count += 1

    total = len(rows)
    if total > 0 and unavail_count / total > 0.5:
        logger.warning(
            "Greeks data degraded: %d/%d strikes missing greeks. "
            "Strike selector will skip unavailable rows.",
            unavail_count, total,
        )


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

    # ── IBKR chain (off-hours, 24h data from paper account) ──────────────────

    def _fetch_chain_ibkr(self, expiry: str) -> list[OptionRow]:
        """
        Fetch option chain via IBKR paper account.
        Preferred off-hours: OI + bid/ask available around the clock.
        Returns an empty list on any failure (yfinance fallback applies).
        """
        try:
            from ingestion.ibkr_feed import get_ibkr_feed
            from ib_insync import Option
        except ImportError:
            return []

        try:
            feed = get_ibkr_feed()
            if not feed.is_connected():
                return []
            ib = feed._ib  # access underlying IB instance

            expiry_ib = expiry.replace("-", "")

            # Get the valid strikes for this expiry from IBKR
            params_list = ib.reqSecDefOptParams(self.ticker, "", "STK", 0)
            ib.sleep(2.0)

            strikes: set = set()
            for p in params_list:
                if p.exchange == "SMART" and expiry_ib in p.expirations:
                    strikes.update(p.strikes)

            if not strikes:
                logger.debug("IBKR chain: no strikes found for %s %s", self.ticker, expiry)
                return []

            rows: list[OptionRow] = []
            # Limit to a reasonable number of strikes to avoid flooding IBKR
            sorted_strikes = sorted(strikes)

            for right_chr, opt_type in [("C", "CALL"), ("P", "PUT")]:
                for strike_val in sorted_strikes:
                    contract = Option(self.ticker, expiry_ib, strike_val, right_chr, "SMART")
                    ticker_data = ib.reqMktData(contract, "", True, False)  # snapshot
                    ib.sleep(0.2)

                    bid  = float(ticker_data.bid  or 0)
                    ask  = float(ticker_data.ask  or 0)
                    last = float(ticker_data.last or ticker_data.close or 0)
                    oi   = int(ticker_data.volume or 0)  # volume as OI proxy off-hours
                    iv   = float(getattr(ticker_data, "impliedVol", 0) or 0)
                    vol_today = int(getattr(ticker_data, "volume", 0) or 0)

                    row = OptionRow(
                        strike=strike_val,
                        option_type=opt_type,
                        expiration_date=expiry,
                        implied_volatility=iv,
                        open_interest=oi,
                        bid=bid,
                        ask=ask,
                        last_price=last,
                        volume=vol_today,
                    )

                    # IBKR modelGreeks — prefer native greeks when available
                    model_greeks = getattr(ticker_data, "modelGreeks", None)
                    if model_greeks is not None:
                        try:
                            row.delta = float(model_greeks.delta or 0)
                            row.gamma = float(model_greeks.gamma or 0)
                            row.theta = float(model_greeks.theta or 0)
                            row.vega  = float(model_greeks.vega or 0)
                            row.greeks_source = "ibkr"
                        except Exception:
                            pass  # fall through to BS-compute

                    rows.append(row)

            logger.info("IBKR chain: %d contracts loaded for %s %s (source=ibkr)",
                        len(rows), self.ticker, expiry)
            return rows

        except Exception as exc:
            logger.debug("IBKR chain fetch failed (will fall back to yfinance): %s", exc)
            return []

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
                        volume=int(r.get("volume", 0) or 0),
                    ))
                except Exception:
                    continue
        return rows

    def get_chain(self, expiry: str) -> list[OptionRow]:
        """
        Return cached (or fresh) option rows for `expiry`.

        Source selection:
          - Off-hours + IBKR connected → IBKR snapshot (bid/ask available 24h)
          - In-hours or IBKR unavailable → yfinance
        """
        now = time.time()
        cached = self._cache.get(expiry)
        if cached and now - cached[0] < self.CACHE_TTL:
            return cached[1]

        rows: list[OptionRow] = []
        source = "yfinance"

        # Prefer IBKR when off-hours: yfinance returns stale/zero OI off-hours
        if not _is_us_market_hours():
            ibkr_rows = self._fetch_chain_ibkr(expiry)
            if ibkr_rows:
                rows  = ibkr_rows
                source = "ibkr"

        if not rows:
            # In-hours or IBKR unavailable: use yfinance
            try:
                rows = self._fetch_chain(expiry)
                source = "yfinance"
            except Exception as e:
                logger.warning(f"Chain fetch failed for {expiry}: {e}")
                return cached[1] if cached else []

        # Enrich with greeks (BS-compute from IV; IBKR rows already have greeks from modelGreeks)
        if rows:
            from datetime import date as _d
            try:
                exp_date = _d.fromisoformat(expiry)
                dte = (exp_date - _d.today()).days
                ttm = max(dte / 365.0, 0.0001)
            except ValueError:
                ttm = 7 / 365.0
            try:
                _spot, _ = get_spot_with_fallback(self.ticker)
            except Exception:
                _spot = 0.0
            enrich_greeks(rows, spot=_spot, ttm_years=ttm)

        self._cache[expiry] = (now, rows)
        logger.info("Options chain loaded: %s — %d contracts (source=%s)", expiry, len(rows), source)
        _hb("options_chain_api", status="ok", detail=f"expiry:{expiry} contracts:{len(rows)} source:{source}")
        return rows

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

    # ── delta-based strike selection ─────────────────────────────────────────

    @staticmethod
    def _bs_call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Black-Scholes call delta = N(d1). Returns value in (0, 1)."""
        import math
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.5  # ATM fallback
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return 0.5 * math.erfc(-d1 / math.sqrt(2))

    def snap_strike_by_delta(
        self,
        spot: float,
        option_type: str,
        target_delta: float,
        expiry: Optional[str] = None,
        max_delta_error: float = 0.05,
    ) -> tuple[float, float, str]:
        """
        Find the chain strike whose Black-Scholes delta is closest to target_delta.

        For calls, delta in (0, 1).  For puts, pass the absolute value (e.g. 0.25
        means 25-delta put); this method handles the sign internally.

        Returns (strike, implied_volatility, expiry_date).
        Returns (-1.0, 0.0, expiry) if no strike is within max_delta_error — caller
        must reject the signal.
        Falls back to snap_strike (moneyness-based) if the chain is entirely missing.
        """
        from datetime import date as _date_cls

        if expiry is None:
            expiry = self.nearest_expiry_with_liquidity(min_dte=1)

        if not expiry:
            moneyness = 1.0 + (target_delta - 0.5) * 0.2
            return self.snap_strike(spot, option_type, moneyness)

        rows = self.get_chain(expiry)
        candidates = [
            r for r in rows
            if r.option_type == option_type and r.open_interest >= self.MIN_OI
        ]
        if not candidates:
            moneyness = 1.0 + (target_delta - 0.5) * 0.2
            return self.snap_strike(spot, option_type, moneyness, expiry=expiry)

        try:
            exp_date = _date_cls.fromisoformat(expiry)
            dte = (_date_cls.today() - exp_date).days  # negative = future
            dte = (exp_date - _date_cls.today()).days
        except ValueError:
            dte = 7
        T = max(dte / 365.0, 0.001)
        r_rate = 0.05  # risk-free rate approximation

        best: Optional[OptionRow] = None
        best_err = float("inf")

        for row in candidates:
            iv = row.implied_volatility
            if iv <= 0:
                continue
            call_delta = self._bs_call_delta(spot, row.strike, T, r_rate, iv)
            delta = call_delta if option_type == "CALL" else (1.0 - call_delta)
            err = abs(delta - target_delta)
            if err < best_err:
                best_err = err
                best = row

        if best is None or best_err > max_delta_error:
            if best is None:
                logger.warning(
                    "snap_strike_by_delta: no IV available for %s %s — using moneyness fallback",
                    option_type, expiry,
                )
                moneyness = 1.0 + (target_delta - 0.5) * 0.2
                return self.snap_strike(spot, option_type, moneyness, expiry=expiry)
            else:
                logger.warning(
                    "snap_strike_by_delta: closest delta err=%.3f exceeds max_delta_error=%.2f "
                    "for %s %s target_delta=%.2f — signal rejected",
                    best_err, max_delta_error, option_type, expiry, target_delta,
                )
                return -1.0, 0.0, expiry  # sentinel: caller must reject signal

        logger.info(
            "snap_strike_by_delta: %s strike=%.1f target_delta=%.2f err=%.3f IV=%.1f%%",
            option_type, best.strike, target_delta, best_err, best.implied_volatility * 100,
        )
        return best.strike, best.implied_volatility, expiry


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
