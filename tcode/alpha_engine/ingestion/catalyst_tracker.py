#!/usr/bin/env python3
"""
TSLA Catalyst Tracker: Musk social mentions + analyst consensus.
Feeds into the SENTIMENT model with real data instead of random generation.
"""
import time
import logging
from typing import Optional

logger = logging.getLogger("CatalystTracker")

# Module-level cache
_catalyst_cache: Optional[dict] = None
_catalyst_cache_ts: float = 0.0
_SOCIAL_TTL = 120    # 2 minutes for social mentions
_ANALYST_TTL = 3600  # 1 hour for analyst data

_analyst_cache: Optional[dict] = None
_analyst_cache_ts: float = 0.0


def _fetch_musk_sentiment() -> dict:
    """Scan yfinance TSLA news for Musk/Elon mentions and score sentiment."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("TSLA")
        news = ticker.news or []

        musk_keywords = ["musk", "elon", "ceo", "x.com", "twitter", "spacex", "boring", "neuralink"]
        positive_keywords = ["surge", "rally", "beat", "record", "upgrade", "buy", "bullish", "growth", "profit", "deliveries"]
        negative_keywords = ["crash", "fall", "miss", "recall", "lawsuit", "sec", "investigation", "downgrade", "sell", "bearish", "cut"]

        musk_headlines = []
        bull_score = 0
        bear_score = 0

        for item in news[:20]:
            title = (item.get("title") or "").lower()
            # Check for Musk mentions
            is_musk = any(kw in title for kw in musk_keywords)
            if is_musk:
                musk_headlines.append(item.get("title", ""))
                for kw in positive_keywords:
                    if kw in title:
                        bull_score += 1
                for kw in negative_keywords:
                    if kw in title:
                        bear_score += 1

        total = bull_score + bear_score
        sentiment = (bull_score - bear_score) / max(total, 1)  # -1.0 to 1.0

        return {
            "musk_mention_count": len(musk_headlines),
            "musk_headlines": musk_headlines[:5],
            "musk_sentiment": round(sentiment, 3),
            "bull_hits": bull_score,
            "bear_hits": bear_score,
        }
    except Exception as e:
        logger.warning(f"Musk sentiment fetch failed: {e}")
        return {"musk_mention_count": 0, "musk_headlines": [], "musk_sentiment": 0.0, "bull_hits": 0, "bear_hits": 0}


def _fetch_analyst_consensus() -> dict:
    """Fetch analyst recommendations from yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("TSLA")
        recs = ticker.recommendations
        if recs is None or recs.empty:
            return {"analyst_consensus": "N/A", "recent_changes": [], "strong_buy_count": 0, "sell_count": 0}

        # Get the most recent row (current consensus)
        latest = recs.iloc[-1] if len(recs) > 0 else None
        if latest is None:
            return {"analyst_consensus": "N/A", "recent_changes": [], "strong_buy_count": 0, "sell_count": 0}

        strong_buy = int(latest.get("strongBuy", 0))
        buy = int(latest.get("buy", 0))
        hold = int(latest.get("hold", 0))
        sell = int(latest.get("sell", 0))
        strong_sell = int(latest.get("strongSell", 0))

        total = strong_buy + buy + hold + sell + strong_sell
        if total == 0:
            consensus = "N/A"
        elif (strong_buy + buy) / total > 0.6:
            consensus = "BUY"
        elif (sell + strong_sell) / total > 0.4:
            consensus = "SELL"
        else:
            consensus = "HOLD"

        # Check for recent upgrades/downgrades
        recent_changes = []
        try:
            upgrades = ticker.upgrades_downgrades
            if upgrades is not None and not upgrades.empty:
                recent = upgrades.tail(5)
                for _, row in recent.iterrows():
                    recent_changes.append({
                        "firm": row.get("Firm", "Unknown"),
                        "grade": row.get("ToGrade", ""),
                        "action": row.get("Action", ""),
                    })
        except Exception:
            pass

        return {
            "analyst_consensus": consensus,
            "strong_buy_count": strong_buy,
            "buy_count": buy,
            "hold_count": hold,
            "sell_count": sell + strong_sell,
            "total_analysts": total,
            "recent_changes": recent_changes[-3:],
        }
    except Exception as e:
        logger.warning(f"Analyst consensus fetch failed: {e}")
        return {"analyst_consensus": "N/A", "recent_changes": [], "strong_buy_count": 0, "sell_count": 0}


def get_catalyst_intel() -> dict:
    """Return combined Musk sentiment + analyst consensus. Cached with separate TTLs."""
    global _catalyst_cache, _catalyst_cache_ts, _analyst_cache, _analyst_cache_ts
    now = time.time()

    # Refresh social mentions (2 min TTL)
    if _catalyst_cache is None or now - _catalyst_cache_ts > _SOCIAL_TTL:
        _catalyst_cache = _fetch_musk_sentiment()
        _catalyst_cache_ts = now

    # Refresh analyst data (1 hour TTL)
    if _analyst_cache is None or now - _analyst_cache_ts > _ANALYST_TTL:
        _analyst_cache = _fetch_analyst_consensus()
        _analyst_cache_ts = now

    return {
        **_catalyst_cache,
        **_analyst_cache,
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_catalyst_intel()
    print(json.dumps(result, indent=2))
