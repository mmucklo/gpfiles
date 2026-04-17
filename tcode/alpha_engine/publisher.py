"""
TSLA Alpha Engine: NATS Signal Publisher
Broadcasts validated consensus signals to the Go-based execution engine.
"""
import asyncio
import dataclasses
import json
import os
import nats
import random
import time
from ib_insync import util as ib_util
ib_util.patchAsyncio()  # allow ib.connect() from within a running event loop
from datetime import date as _date
from prometheus_client import start_http_server, Counter, Gauge, Histogram
from consensus import ModelSignal, SignalDirection, ModelType, compute_expiry, find_best_expiry, find_best_expiry_for_archetype
from ingestion.pricing import MultiSourcePricing
from ingestion.options_chain import get_chain_cache
from ingestion.tv_feed import validate_spot_price, get_tv_cache, TVFeedError
from ingestion.ibkr_feed import get_ibkr_feed
from data.logger import DataLogger
from config.archetypes import get_archetype, MODEL_ARCHETYPE_MAP, ARCHETYPES
from strike_selector import select_strike, StrikeSelection, StrikeSelectionResult
from heartbeat import emit_heartbeat_async, set_nats_conn, emit_rejection

# ── Notional account size: NEVER use portfolio NAV for sizing. ───────────────
# Default $25k represents the small-account discipline target for live trading.
# Override at runtime with NOTIONAL_ACCOUNT_SIZE env var.
NOTIONAL: int = int(os.getenv("NOTIONAL_ACCOUNT_SIZE", "25000"))

# Gross outstanding cap: total option premium outstanding must not exceed this
# fraction of notional. Prevents over-allocation during fast-firing periods.
_GROSS_OUTSTANDING_CAP_PCT = 0.06  # 6% of notional

# ── Phase 14: Liquidity floor env vars ───────────────────────────────────────
# Runtime-configurable — override in .tsla-alpha.env without redeploy.
# Publisher uses these for Layer-1 gate inside strike_selector.
_LIQ_MIN_OI    = int(os.getenv("MIN_OPTION_OPEN_INTEREST", "500"))
_LIQ_MIN_VOL   = int(os.getenv("MIN_OPTION_VOLUME_TODAY",  "50"))
_LIQ_MAX_SPR   = float(os.getenv("MAX_BID_ASK_PCT",         "0.15"))
_LIQ_MIN_BID   = float(os.getenv("MIN_ABSOLUTE_BID",        "0.10"))

# ── Phase 14: Chop gating — per-archetype multipliers ─────────────────────
# CHOPPY archetypes that block long-premium:
_CHOP_BLOCK_ARCHETYPES = {
    "DIRECTIONAL_STRONG", "DIRECTIONAL_STD", "MEAN_REVERT", "SCALP_0DTE",
}
# MIXED confidence multipliers:
_CHOP_MIXED_MULT = {
    "DIRECTIONAL_STRONG": 0.7,
    "DIRECTIONAL_STD": 0.7,
    "MEAN_REVERT": 0.7,
    "SCALP_0DTE": 0.6,
    "VOL_PLAY": 1.1,  # vega benefits from compression
}

# Chop regime cache (publisher fetches once per signal-gen cycle, cached 60s)
_chop_cache_ts: float = 0.0
_chop_cache_val: dict = {"regime": "TRENDING", "score": 0.0}
_CHOP_CACHE_TTL = 60

STALENESS_MAX_SECONDS = 300  # 5 minutes
DIVERGENCE_MAX_PCT = 0.5     # 0.5% max IBKR vs TV divergence

# ── Phase 16: Human-in-the-loop approval queue ───────────────────────────────
# When HUMAN_APPROVAL_REQUIRED=1 (default), signals are emitted to
# tsla.alpha.proposals (with TTL) instead of tsla.alpha.signals.
# The Go engine holds proposals in an approval queue; the user must click
# EXECUTE before an order is placed.
HUMAN_APPROVAL_REQUIRED: bool = os.getenv("HUMAN_APPROVAL_REQUIRED", "1") == "1"
PROPOSAL_TTL_SEC: int = int(os.getenv("PROPOSAL_TTL_SEC", "60"))

# ── Phase 16.1: API pause gate ────────────────────────────────────────────────
# Default: paused on startup. User must click ACTIVATE in the dashboard to start
# polling. This prevents runaway Tradier API calls when nobody is watching.
# State is stored in a shared JSON file; the Go API writes it, publisher reads it.
_PAUSE_STATE_FILE = os.getenv("PUBLISHER_PAUSE_STATE_FILE", "/tmp/tsla_alpha_pause_state.json")
_PUBLISHER_AUTO_PAUSE: bool = os.getenv("PUBLISHER_AUTO_PAUSE", "true").lower() != "false"


def _read_pause_state() -> dict:
    """Read pause state from shared file. Returns {"paused": bool, "unpause_until": str|None}."""
    import datetime as _dt
    try:
        with open(_PAUSE_STATE_FILE) as _f:
            state = json.load(_f)
        # Check if unpause_until has expired
        unpause_until_str = state.get("unpause_until")
        if not state.get("paused", True) and unpause_until_str:
            unpause_until = _dt.datetime.fromisoformat(unpause_until_str.replace("Z", "+00:00"))
            now_utc = _dt.datetime.now(_dt.timezone.utc)
            if now_utc > unpause_until:
                # Timer expired — re-pause and write back
                _write_pause_state(paused=True, unpause_until=None)
                return {"paused": True, "unpause_until": None}
        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        # No state file → default to paused
        return {"paused": True, "unpause_until": None}


def _write_pause_state(paused: bool, unpause_until) -> None:
    """Write pause state to shared file."""
    state = {
        "paused": paused,
        "unpause_until": unpause_until.isoformat().replace("+00:00", "Z") if unpause_until else None,
    }
    try:
        with open(_PAUSE_STATE_FILE, "w") as _f:
            json.dump(state, _f)
    except Exception as _e:
        print(f"[PAUSE] Failed to write pause state: {_e}")

def check_data_gates(spot_sources: dict) -> tuple[bool, str]:
    """Return (ok, reason). If not ok, do not publish signal."""
    import time
    now = time.time()

    # Staleness gate
    for src, info in spot_sources.items():
        if isinstance(info, dict):
            ts = info.get("timestamp", 0)
            age = now - ts if ts else 9999
            if age > STALENESS_MAX_SECONDS:
                return False, f"STALE: {src} data is {age:.0f}s old (max {STALENESS_MAX_SECONDS}s)"

    # Divergence gate
    prices = {k: v.get("price") for k, v in spot_sources.items() if isinstance(v, dict) and v.get("price")}
    if len(prices) >= 2:
        vals = list(prices.values())
        pct_diff = abs(vals[0] - vals[1]) / vals[0] * 100 if vals[0] != 0 else 0
        if pct_diff > DIVERGENCE_MAX_PCT:
            return False, f"DIVERGENCE: {list(prices.keys())} differ by {pct_diff:.2f}% (max {DIVERGENCE_MAX_PCT}%)"

    return True, ""

# Task 2: Observability Metrics for Intelligence Engine
# Wrapped in try/except to tolerate duplicate imports in test environments where
# both "publisher" and "alpha_engine.publisher" are loaded as separate modules.
def _safe_counter(name, doc):
    try:
        return Counter(name, doc)
    except ValueError:
        from prometheus_client import REGISTRY
        return REGISTRY._names_to_collectors.get(name) or Counter.__new__(Counter)

def _safe_gauge(name, doc):
    try:
        return Gauge(name, doc)
    except ValueError:
        from prometheus_client import REGISTRY
        return REGISTRY._names_to_collectors.get(name) or Gauge.__new__(Gauge)

def _safe_histogram(name, doc):
    try:
        return Histogram(name, doc)
    except ValueError:
        from prometheus_client import REGISTRY
        return REGISTRY._names_to_collectors.get(name) or Histogram.__new__(Histogram)

SIGNAL_SENT_COUNT = _safe_counter('alpha_signal_sent_total', 'Total signals published to NATS')
SIGNAL_CONFIDENCE_GAUGE = _safe_gauge('alpha_intelligence_confidence', 'Confidence score of the latest published signal')
INFERENCE_LATENCY = _safe_histogram('alpha_inference_latency_seconds', 'Inference latency for intelligence models')

# Commission viability gate
SIGNAL_REJECTED_COMMISSION = _safe_counter(
    'signals_rejected_commission_total',
    'Signals suppressed because round-trip IBKR commissions make net profit at TP non-positive',
)

# Min-edge floor gate
SIGNAL_REJECTED_MINEDGE = _safe_counter(
    'signals_rejected_minedge_total',
    'Signals suppressed because expected net profit is below the minimum edge floor',
)

# IBKR Pro options commission schedule (USD).
IBKR_OPTION_FEE_PER_CONTRACT: float = 0.65   # per contract, per leg
IBKR_OPTION_MIN_PER_LEG: float = 1.00         # minimum charge per order/leg
_SINGLE_LEG_ROUND_TRIP: int = 2               # open + close = 2 legs
_SPREAD_ROUND_TRIP: int = 4                    # 2 legs per side × open + close = 4 legs

# In-process counters for writing to the metrics file (Prometheus Counter values
# cannot be read back from the counter object itself in all versions).
_rejected_commission_total: int = 0
_rejected_minedge_total: int = 0
_PUBLISHER_METRICS_PATH = "/tmp/publisher_metrics.json"


def compute_round_trip_commission(qty: int, is_spread: bool = False) -> float:
    """Return estimated IBKR round-trip commission for an options trade.

    Args:
        qty: Number of contracts.
        is_spread: True for two-legged spread orders (doubles the leg count).

    Returns:
        Estimated total commission in USD for a full round trip (open + close).
    """
    legs = _SPREAD_ROUND_TRIP if is_spread else _SINGLE_LEG_ROUND_TRIP
    per_leg = max(IBKR_OPTION_FEE_PER_CONTRACT * qty, IBKR_OPTION_MIN_PER_LEG)
    return per_leg * legs


def signal_is_commission_viable(
    limit_price: float,
    take_profit_price: float,
    stop_loss_price: float,  # noqa: ARG001 — reserved for EV check (Phase 3)
    qty: int,
    is_spread: bool = False,
) -> tuple[bool, str]:
    """Return (viable, reason_string) for a signal.

    A signal is viable only when the net profit at the take-profit price remains
    positive after deducting full round-trip IBKR commissions.

    Handles both debit and credit trades:
    - Debit (BUY): profit = (TP - limit) * 100 * qty  (TP > limit)
    - Credit (SELL/spread): profit = (limit - TP) * 100 * qty  (TP < limit)

    In both cases gross_profit = abs(TP - limit) * 100 * qty.

    The EV check (weighting profit/loss by confidence) is a Phase 3 TODO; for
    now we gate solely on net_profit_at_tp > 0.
    """
    gross_profit_at_tp = abs(take_profit_price - limit_price) * 100 * qty
    commission = compute_round_trip_commission(qty, is_spread=is_spread)
    net_profit_at_tp = gross_profit_at_tp - commission
    if net_profit_at_tp <= 0:
        return False, (
            f"commission-negative at TP: gross={gross_profit_at_tp:.2f}, "
            f"commission={commission:.2f}, net={net_profit_at_tp:.2f}"
        )
    return True, ""


def compute_notional_sizing(
    notional: int,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    premium: float,
    is_spread: bool = False,
) -> tuple[int, str]:
    """Compute position size from notional risk budget.

    Returns (qty, rejection_reason). rejection_reason is "" if viable.

    Sizing logic:
      max_loss_dollars  = notional * risk_pct
      per_contract_loss = (entry_price - stop_loss_price) * 100
      qty               = max(1, floor(max_loss_dollars / per_contract_loss))

    Gross outstanding cap:  qty * premium * 100 <= notional * GROSS_CAP
    """
    max_loss_dollars = notional * risk_pct
    per_contract_loss = abs(entry_price - stop_loss_price) * 100
    if per_contract_loss <= 0:
        return 1, ""  # degenerate stop → use minimum qty

    qty = max(1, int(max_loss_dollars // per_contract_loss))

    gross_outstanding = qty * premium * 100
    gross_cap = notional * _GROSS_OUTSTANDING_CAP_PCT
    if gross_outstanding > gross_cap:
        qty = max(1, int(gross_cap // (premium * 100)))
        gross_outstanding = qty * premium * 100

    return qty, ""


def compute_min_edge_floor(notional: int, qty: int, is_spread: bool = False) -> float:
    """Return the minimum acceptable net profit for a signal to be published.

    Floor = max(notional * 0.0025,  5 * round_trip_commission)
          = max(0.25% of notional, 5× what we'd pay IBKR per round trip)
    """
    commission_5x = 5 * compute_round_trip_commission(qty, is_spread=is_spread)
    return max(notional * 0.0025, commission_5x)


def signal_passes_min_edge(
    limit_price: float,
    take_profit_price: float,
    qty: int,
    notional: int,
    is_spread: bool = False,
) -> tuple[bool, str]:
    """Return (passes, reason_string).

    Net expected profit = gross_profit_at_tp - round_trip_commission.
    Must exceed the min-edge floor.
    """
    gross_profit_at_tp = abs(take_profit_price - limit_price) * 100 * qty
    commission = compute_round_trip_commission(qty, is_spread=is_spread)
    net_profit = gross_profit_at_tp - commission
    floor = compute_min_edge_floor(notional, qty, is_spread=is_spread)
    if net_profit < floor:
        return False, (
            f"min-edge floor: net={net_profit:.2f} < floor={floor:.2f} "
            f"(notional={notional}, qty={qty}, "
            f"gross={gross_profit_at_tp:.2f}, commission={commission:.2f})"
        )
    return True, ""


def _write_publisher_metrics() -> None:
    """Persist the in-process rejected-signal counters to a JSON file so the
    Go API can serve them to the dashboard without scraping Python's Prometheus."""
    try:
        with open(_PUBLISHER_METRICS_PATH, "w") as fh:
            json.dump({
                "signals_rejected_commission_total": _rejected_commission_total,
                "signals_rejected_minedge_total": _rejected_minedge_total,
                "notional_account_size": NOTIONAL,
                "ts": time.time(),
            }, fh)
    except OSError:
        pass  # non-fatal — metrics file is best-effort

def _chain_row_to_dict(r) -> dict:
    """Serialize an OptionRow to a JSON-safe dict for chain_snapshot storage."""
    return {
        "strike": r.strike,
        "option_type": r.option_type,
        "volume": r.volume,
        "open_interest": r.open_interest,
        "bid": r.bid,
        "ask": r.ask,
        "delta": r.delta,
        "gamma": r.gamma,
        "theta": r.theta,
        "vega": r.vega,
        "iv": r.implied_volatility,
    }


class SignalPublisher:
    """
    Publishes signals to the 'tsla.alpha.signals' NATS subject.
    Using asynchronous I/O to maintain non-blocking market data ingestion.
    """
    def __init__(self, nats_url: str = "nats://127.0.0.1:4222"):
        self.nats_url = nats_url
        self.nc = None
        self.pricing = MultiSourcePricing()
        # Start Prometheus metrics server on port 8000
        try:
            start_http_server(8000)
        except Exception:
            pass # Handle case where server is already running in same process

    async def connect(self):
        """Establishing high-speed connection to the NATS broker."""
        print(f"Connecting to NATS at {self.nats_url}...")
        self.nc = await nats.connect(self.nats_url)
        set_nats_conn(self.nc)
        print("Connected to NATS.")

    async def publish_signal(self, signal: ModelSignal, spot_sources: dict = None):
        """
        Broadcasting the Conviction Signal.
        Payload includes the consensus direction and confidence score for Kelly sizing.
        """
        if not self.nc:
            await self.connect()

        # Validation guard: recompute stale expiration dates before broadcast
        try:
            if _date.fromisoformat(signal.expiration_date) < _date.today():
                print(f"[WARN] Recomputing stale expiration_date {signal.expiration_date} for {signal.recommended_expiry}")
                try:
                    _days = int(str(signal.recommended_expiry).replace('DTE', '').strip())
                except (ValueError, AttributeError):
                    _days = 7
                signal = dataclasses.replace(signal, expiration_date=find_best_expiry(_days))
        except (ValueError, AttributeError):
            pass

        payload = {
            "model_id": signal.model_id.name,
            "direction": signal.direction.name,
            "confidence": signal.confidence,
            "timestamp": signal.timestamp,
            "ticker": signal.ticker,
            "underlying_price": signal.underlying_price,
            "price_source": signal.price_source,
            "strategy_code": signal.strategy_code,
            "recommended_strike": signal.recommended_strike,
            "short_strike": signal.short_strike,
            "long_strike": signal.long_strike,
            "is_spread": signal.is_spread,
            "recommended_expiry": signal.recommended_expiry,
            "option_type": signal.option_type,
            "action": signal.action,
            "expiration_date": signal.expiration_date,
            "target_limit_price": signal.target_limit_price,
            "take_profit_price": signal.take_profit_price,
            "stop_loss_price": signal.stop_loss_price,
            "stop_loss_underlying_price": getattr(signal, "stop_loss_underlying_price", 0.0),
            "kelly_wager_pct": signal.kelly_wager_pct,
            "quantity": signal.quantity,
            "confidence_rationale": signal.confidence_rationale,
            "implied_volatility": signal.implied_volatility,
            "spot_sources": spot_sources or {},
            "strike_selection_meta": getattr(signal, "strike_selection_meta", None),
        }
        
        if HUMAN_APPROVAL_REQUIRED:
            # Phase 16: emit to proposals subject — human must click EXECUTE
            import uuid as _uuid
            from datetime import timezone as _tz
            from datetime import datetime as _dt
            proposal_id = str(_uuid.uuid4())
            now_iso = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            expires_iso = _dt.fromtimestamp(
                _dt.now(_tz.utc).timestamp() + PROPOSAL_TTL_SEC, tz=_tz.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Fetch regime snapshot for context
            regime_snapshot: dict = {}
            try:
                from regime_classifier import get_cached
                regime_snapshot = get_cached() or {}
            except Exception:
                pass

            # Determine strategy label from strategy_code
            _strategy_label_map = {
                "STRAT-001": "GAMMA_SCALP",
                "STRAT-002": "MOMENTUM",
                "STRAT-003": "MOMENTUM",
                "STRAT-004": "MOMENTUM",
                "STRAT-005": "WAVE_RIDER",
                "STRAT-006": "MOMENTUM",
                "STRAT-007": "MOMENTUM",
            }
            strategy_label = _strategy_label_map.get(
                str(payload.get("strategy_code", "")).split(" ")[0],
                regime_snapshot.get("recommended_strategy", "MOMENTUM"),
            )

            # Build leg spec (single-leg for now; multi-leg added by ibkr_order.py)
            legs = [{
                "strike": payload.get("recommended_strike"),
                "type": payload.get("option_type", "CALL"),
                "action": payload.get("action", "BUY"),
                "quantity": payload.get("quantity", 1),
                "fill_price": None,  # filled on execute
            }]
            if payload.get("is_spread") and payload.get("long_strike"):
                legs.append({
                    "strike": payload.get("long_strike"),
                    "type": payload.get("option_type", "CALL"),
                    "action": "SELL" if payload.get("action") == "BUY" else "BUY",
                    "quantity": payload.get("quantity", 1),
                    "fill_price": None,
                })

            proposal = {
                "id": proposal_id,
                "ts_created": now_iso,
                "ts_expires": expires_iso,
                "status": "pending",
                "strategy": strategy_label,
                "direction": payload.get("direction", "BULLISH"),
                "legs": legs,
                "entry_price": payload.get("target_limit_price"),
                "stop_price": payload.get("stop_loss_price"),
                "target_price": payload.get("take_profit_price"),
                "kelly_fraction": payload.get("kelly_wager_pct"),
                "quantity": payload.get("quantity"),
                "confidence": payload.get("confidence"),
                "regime_snapshot": regime_snapshot,
                "signals_contributing": [payload.get("model_id", "")],
                "raw_signal": payload,
            }
            await self.nc.publish("tsla.alpha.proposals", json.dumps(proposal).encode())
        else:
            await self.nc.publish("tsla.alpha.signals", json.dumps(payload).encode())

        # Update Task 2 Metrics
        SIGNAL_SENT_COUNT.inc()
        SIGNAL_CONFIDENCE_GAUGE.set(signal.confidence)

        # Ensure message is dispatched
        await self.nc.flush()

    async def close(self):
        """Graceful shutdown of the publisher."""
        if self.nc:
            await self.nc.close()

async def broadcast_loop():
    """
    Simulates an active Intelligence Engine scanning the market.
    Periodically generates signals from different models to demonstrate life.
    """
    publisher = SignalPublisher()
    await publisher.connect()

    # Data logger: persist all signals + periodic snapshots to SQLite
    _logger = DataLogger()
    await _logger.start()

    # Periodic snapshot task (every 5 minutes)
    async def _snapshot_loop():
        while True:
            await asyncio.sleep(300)
            try:
                spot = publisher.pricing.get_consensus_price()
                bar = {"ts": None, "open": spot, "high": spot, "low": spot, "close": spot, "volume": 0}
                import time as _t; bar["ts"] = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime())
                await _logger.log_price_bar("TSLA", "consensus", bar)
            except Exception as _e:
                pass
            try:
                from ingestion.ibkr_account import get_account_summary
                acct = get_account_summary()
                if acct:
                    await _logger.log_account_snapshot(acct)
            except Exception:
                pass

    asyncio.create_task(_snapshot_loop())


    models = [ModelType.SENTIMENT, ModelType.OPTIONS_FLOW, ModelType.MACRO, ModelType.VOLATILITY, ModelType.CONTRARIAN, ModelType.EV_SECTOR, ModelType.PREMARKET]
    strategies = {
        ModelType.SENTIMENT: "STRAT-003 (NLP Sentiment)",
        ModelType.OPTIONS_FLOW: "STRAT-004 (Whale Sweep)",
        ModelType.MACRO: "STRAT-002 (NDX Correlation)",
        ModelType.VOLATILITY: "STRAT-001 (IV Arb)",
        ModelType.CONTRARIAN: "STRAT-005 (Mean Reversion)",
        ModelType.EV_SECTOR: "STRAT-006 (EV Sector)",
        ModelType.PREMARKET: "STRAT-007 (Pre-Market)"
    }

    rationales = {
        ModelType.SENTIMENT: "High-volume NLP mention of Giga Berlin expansion correlated with positive TSLA bias.",
        ModelType.OPTIONS_FLOW: "Aggressive $420 Call sweeps detected; premium size exceeds $15M cumulative.",
        ModelType.MACRO: "Nasdaq 100 futures leading TSLA; historical correlation coefficient at 0.88.",
        ModelType.VOLATILITY: "IV Term Structure in backwardation; front-month premium overpriced by 12%.",
        ModelType.CONTRARIAN: "CONTRARIAN: Price overextended — buying cheap OTM for mean reversion snap-back.",
        ModelType.EV_SECTOR: "EV sector correlation — tracking RIVN, LCID, BYD, and DRIV ETF for sector-wide moves.",
        ModelType.PREMARKET: "Pre-market futures and overnight European session data."
    }

    print("Intelligence Engine: SELECTIVE SNIPER Mode Active (REAL PRICING).")

    # Phase 16.1: Initialize pause state file on startup (paused by default)
    if _PUBLISHER_AUTO_PAUSE:
        _initial_state = _read_pause_state()
        if not os.path.exists(_PAUSE_STATE_FILE):
            # First run — write the default paused state
            _write_pause_state(paused=True, unpause_until=None)
            print("[PAUSE] Publisher starting in PAUSED state. Click ACTIVATE in the dashboard to begin polling.")
        elif not _initial_state.get("paused", True):
            # State file exists and says active — respect it (user may have pre-activated)
            print("[PAUSE] Publisher starting ACTIVE (pre-existing pause state file).")
        else:
            print("[PAUSE] Publisher starting in PAUSED state. Click ACTIVATE in the dashboard to begin polling.")

    # Startup: attempt IBKR connection (primary data source)
    try:
        _ibkr = get_ibkr_feed()
        _ibkr_ok = _ibkr.connect()
        if _ibkr_ok:
            print("[IBKR] Connected to IB Gateway (paper trading, port 4002)")
        else:
            print("[IBKR] IB Gateway not available — falling back to TV/YF feeds")
    except Exception as _ibkr_exc:
        print(f"[IBKR] Startup connection skipped: {_ibkr_exc}")

    # Startup: cross-validate price sources
    try:
        _audit = validate_spot_price("TSLA")
        if _audit["ok"]:
            print(f"[AUDIT] Spot validation OK: TV={_audit['tv']:.2f} YF={_audit['yf']:.2f} div={_audit['divergence_pct']:.3f}%")
        else:
            print(f"[AUDIT] {_audit['warning']}")
    except Exception as _exc:
        print(f"[AUDIT] Startup validation error: {_exc}")

    # Dedup: track recently fired signals to prevent spamming
    _recent_signals: dict[str, float] = {}  # key -> timestamp
    SIGNAL_COOLDOWN = 300  # 5 minutes between same signal

    while True:
        # Phase 16.1: Pause gate — skip all external API calls when paused.
        # Heartbeat still emits so the system health panel stays green.
        _pause = _read_pause_state()
        if _pause.get("paused", True):
            await emit_heartbeat_async("publisher", status="ok", detail="PAUSED — awaiting user activation", logger=_logger)
            await asyncio.sleep(5)
            continue

        # Reload NOTIONAL_ACCOUNT_SIZE if the Go API wrote a reload marker
        global NOTIONAL
        _reload_path = "/tmp/notional_reload"
        if os.path.exists(_reload_path):
            try:
                with open(_reload_path) as _rf:
                    _new_notional = int(_rf.read().strip())
                if _new_notional != NOTIONAL and 5000 <= _new_notional <= 250000:
                    print(f"[NOTIONAL] Reloaded: {NOTIONAL} → {_new_notional}")
                    NOTIONAL = _new_notional
                os.remove(_reload_path)
            except Exception as _re:
                print(f"[NOTIONAL] Reload error: {_re}")

        # Step 1: Fetch consensus REAL price
        try:
            spot = publisher.pricing.get_consensus_price()
        except Exception:
            spot = 390.45

        # Per-cycle spot divergence and staleness guards
        spot_sources: dict = {}
        try:
            # Note: validate_spot_price now returns a more detailed dict
            # including timestamps for each source.
            _val = validate_spot_price("TSLA")
            spot_sources = _val.get("sources", {})
            spot_sources['divergence_pct'] = _val.get('divergence_pct')

            ok, reason = check_data_gates(spot_sources)
            if not ok:
                print(f"[GATE BLOCKED] {reason}")
                await emit_heartbeat_async("publisher", status="degraded", detail=f"gate_blocked:{reason}", logger=_logger)
                await asyncio.sleep(random.uniform(10, 20))
                continue
        except Exception as _exc:
            print(f"[WARN] Data validation gates skipped: {_exc}")

        # DATA-DRIVEN SIGNAL GENERATION
        # Each model checks its own data and fires only when it has real conviction
        intel = {}
        try:
            from ingestion.intel import get_intel
            intel = get_intel()
        except Exception as _ie:
            print(f"[INTEL] Fetch failed: {_ie}")
            await emit_heartbeat_async("publisher", status="error", detail=f"intel_fetch_failed:{_ie}", logger=_logger)
            await asyncio.sleep(random.uniform(10, 20))
            continue

        for model in models:
            direction = None
            confidence = 0.0
            action = "BUY"
            is_spread = False
            short_strike = 0.0
            long_strike = 0.0
            rationale = ""
            moneyness = 1.05  # default

            # ── SENTIMENT: driven by news sentiment + Musk mentions ──
            if model == ModelType.SENTIMENT:
                news = intel.get("news", {})
                catalyst = intel.get("catalyst", {})
                news_sent = news.get("sentiment_score", 0.0)
                musk_sent = catalyst.get("musk_sentiment", 0.0)
                analyst = catalyst.get("analyst_consensus", "N/A")

                combined = news_sent * 0.4 + musk_sent * 0.3
                if analyst == "BUY":
                    combined += 0.3
                elif analyst == "SELL":
                    combined -= 0.3

                if combined > 0.3:
                    direction = SignalDirection.BULLISH
                    confidence = min(0.95, 0.6 + combined * 0.5)
                elif combined < -0.3:
                    direction = SignalDirection.BEARISH
                    confidence = min(0.95, 0.6 + abs(combined) * 0.5)
                else:
                    continue  # No conviction

                rationale = f"Sentiment: news={news_sent:.2f}, Musk={musk_sent:.2f}, analyst={analyst}"

            # ── OPTIONS_FLOW: driven by put/call ratio + institutional flow ──
            elif model == ModelType.OPTIONS_FLOW:
                flow = intel.get("options_flow", {})
                inst = intel.get("institutional", {})
                pc_ratio = flow.get("pc_ratio", 1.0)
                insider = inst.get("net_insider_sentiment", "NEUTRAL")

                if pc_ratio > 1.3:  # Heavy put buying = bearish
                    direction = SignalDirection.BEARISH
                    confidence = min(0.90, 0.5 + (pc_ratio - 1.0) * 0.3)
                elif pc_ratio < 0.7:  # Heavy call buying = bullish
                    direction = SignalDirection.BULLISH
                    confidence = min(0.90, 0.5 + (1.0 - pc_ratio) * 0.3)
                else:
                    continue  # No conviction

                if insider == "BULLISH" and direction == SignalDirection.BULLISH:
                    confidence = min(0.95, confidence * 1.1)
                elif insider == "BEARISH" and direction == SignalDirection.BULLISH:
                    confidence *= 0.8

                rationale = f"Options flow: P/C={pc_ratio:.2f}, insider={insider}"

            # ── MACRO: driven by regime detection ──
            elif model == ModelType.MACRO:
                macro = intel.get("macro_regime", {})
                regime = macro.get("regime", "NEUTRAL")
                spy_trend = macro.get("spy_trend", "NEUTRAL")
                vix = macro.get("vix_spot", 20) or 20

                if regime == "RISK_ON" and spy_trend == "BULLISH":
                    direction = SignalDirection.BULLISH
                    confidence = min(0.85, 0.6 + (30 - vix) / 50.0)
                elif regime == "RISK_OFF":
                    direction = SignalDirection.BEARISH
                    confidence = min(0.90, 0.5 + vix / 80.0)
                else:
                    continue

                rationale = f"Macro: regime={regime}, SPY={spy_trend}, VIX={vix:.1f}"

            # ── VOLATILITY: driven by IV term structure ──
            elif model == ModelType.VOLATILITY:
                macro = intel.get("macro_regime", {})
                vix = macro.get("vix_spot", 0) or 0
                vix9d = macro.get("vix_9d", 0) or 0
                term = macro.get("term_structure", "CONTANGO")

                if term == "BACKWARDATION" and vix > 25:
                    direction = SignalDirection.BEARISH
                    confidence = min(0.92, 0.6 + (vix - 20) / 30.0)
                    action = "SELL"  # Sell premium in high IV
                elif term == "CONTANGO" and vix < 18:
                    direction = SignalDirection.BULLISH
                    confidence = min(0.80, 0.55 + (18 - vix) / 20.0)
                else:
                    continue

                rationale = f"Volatility: VIX={vix:.1f}, 9D={vix9d:.1f}, term={term}"

            # ── CONTRARIAN: flip direction when sentiment is extreme ──
            elif model == ModelType.CONTRARIAN:
                news_sent = intel.get("news", {}).get("sentiment_score", 0.0)
                macro = intel.get("macro_regime", {})
                regime = macro.get("regime", "NEUTRAL")

                if news_sent > 0.6 and regime == "RISK_ON":
                    direction = SignalDirection.BEARISH  # Bet against euphoria
                    confidence = 0.55 + min(0.35, (abs(news_sent) - 0.6) * 1.75)
                elif news_sent < -0.6 and regime == "RISK_OFF":
                    direction = SignalDirection.BULLISH  # Bet on panic reversal
                    confidence = 0.55 + min(0.35, (abs(news_sent) - 0.6) * 1.75)
                else:
                    continue

                action = "BUY"
                is_spread = False
                otm_pct = 0.10
                opt_type = "CALL" if direction == SignalDirection.BULLISH else "PUT"
                moneyness = 1.0 + otm_pct if opt_type == "CALL" else 1.0 - otm_pct
                rationale = f"CONTRARIAN: {otm_pct*100:.0f}% OTM — mean reversion on extreme sentiment ({news_sent:.2f})"

            # ── EV_SECTOR: driven by competitor moves ──
            elif model == ModelType.EV_SECTOR:
                ev = intel.get("ev_sector", {})
                sector_dir = ev.get("sector_direction", "NEUTRAL")
                rel_strength = ev.get("tsla_relative_strength", 0)

                if sector_dir == "BULLISH":
                    direction = SignalDirection.BULLISH
                elif sector_dir == "BEARISH":
                    direction = SignalDirection.BEARISH
                elif sector_dir == "DIVERGING" and abs(rel_strength) > 2:
                    direction = SignalDirection.BEARISH if rel_strength > 0 else SignalDirection.BULLISH
                else:
                    continue

                confidence = min(0.85, max(0.5, abs(rel_strength) / 5.0 + 0.5))
                action = "BUY"
                is_spread = False
                moneyness = 1.03 if direction == SignalDirection.BULLISH else 0.97
                rationale = f"EV Sector {sector_dir}: relative strength {rel_strength:+.1f}%"

            # ── PREMARKET: composite bias from international indices + FX ──
            elif model == ModelType.PREMARKET:
                pm = intel.get("premarket", {})
                if not pm.get("is_signal_window", False):
                    continue

                # Use composite_bias (new multi-region scoring) with fallback to legacy futures_bias
                composite_bias = pm.get("composite_bias") or pm.get("futures_bias", "FLAT")
                if composite_bias == "BULLISH":
                    direction = SignalDirection.BULLISH
                elif composite_bias == "BEARISH":
                    direction = SignalDirection.BEARISH
                else:
                    continue

                # Confidence from the composite scoring engine (includes FX override)
                pm_confidence = pm.get("confidence", 0.0)
                if pm_confidence > 0.0:
                    confidence = pm_confidence
                else:
                    # Legacy fallback: derive from NQ magnitude
                    futures_mag = abs(pm.get("nq_change_pct", 0))
                    confidence = min(0.90, max(0.55, futures_mag / 3.0 + 0.5))

                action = "BUY"
                is_spread = False
                moneyness = 1.03 if direction == SignalDirection.BULLISH else 0.97
                pm_rationale = pm.get("rationale", "")
                rationale = (
                    f"PRE-MARKET: {pm_rationale}" if pm_rationale
                    else f"PRE-MARKET: NQ {pm.get('nq_change_pct', 0):+.1f}%, ES {pm.get('es_change_pct', 0):+.1f}%"
                )

            else:
                continue

            # Skip if no direction or low confidence
            if direction is None or direction == SignalDirection.NEUTRAL:
                continue
            if confidence < 0.55:
                continue

            # ── Archetype config for this model ──
            archetype_cfg = get_archetype(model.name)
            target_delta = archetype_cfg["delta"]
            archetype_risk_pct = archetype_cfg["risk_pct"]
            archetype_rr = archetype_cfg["rr"]
            archetype_expiry_str = archetype_cfg["expiry"]
            archetype_name = next(
                k for k, v in ARCHETYPES.items() if v is archetype_cfg
            ) if archetype_cfg in ARCHETYPES.values() else "DIRECTIONAL_STD"
            # Resolve archetype name from MODEL_ARCHETYPE_MAP
            archetype_name = MODEL_ARCHETYPE_MAP.get(model.name, "DIRECTIONAL_STD")

            # ── Correlation regime confidence adjustment ──────────────────────
            # IDIOSYNCRATIC: TSLA decoupled from index → amplify SENTIMENT/CONTRARIAN,
            #                dampen MACRO signals (idiosyncratic factors dominate)
            # MACRO_LOCKED:  TSLA hyper-correlated → amplify MACRO, dampen SENTIMENT
            corr_regime = intel.get("correlation_regime", {}).get("regime", "NORMAL")
            if corr_regime == "IDIOSYNCRATIC":
                if model in (ModelType.SENTIMENT, ModelType.CONTRARIAN):
                    confidence = min(0.95, confidence * 1.20)
                elif model == ModelType.MACRO:
                    confidence *= 0.80
            elif corr_regime == "MACRO_LOCKED":
                if model == ModelType.SENTIMENT:
                    confidence *= 0.80
                elif model == ModelType.MACRO:
                    confidence = min(0.95, confidence * 1.20)

            # ── Congress trades SENTIMENT confidence adjustment ───────────────
            # Committee-weighted buying in last 48h boosts SENTIMENT confidence;
            # committee-weighted selling dampens it.
            if model == ModelType.SENTIMENT:
                congress_mult = intel.get("congress", {}).get("sentiment_multiplier", 1.0)
                confidence = min(0.95, confidence * congress_mult)

            # ── Phase 14: Chop regime gating ─────────────────────────────────
            global _chop_cache_ts, _chop_cache_val
            now_ts_chop = time.time()
            if now_ts_chop - _chop_cache_ts > _CHOP_CACHE_TTL:
                try:
                    _chop_cache_val = intel.get("chop_regime", {"regime": "TRENDING", "score": 0.0})
                    _chop_cache_ts = now_ts_chop
                except Exception:
                    pass
            chop_regime_result = _chop_cache_val
            chop_label = chop_regime_result.get("regime", "TRENDING")
            chop_score = chop_regime_result.get("score", 0.0)
            rv_iv_ratio = chop_regime_result.get("components", {}).get("rv_iv_ratio", 1.0) or 1.0

            if chop_label == "CHOPPY":
                if archetype_name in _CHOP_BLOCK_ARCHETYPES:
                    print(f"[CHOP-BLOCK] archetype={archetype_name} model={model.name} "
                          f"chop_score={chop_score:.2f}")
                    continue
                if archetype_name == "VOL_PLAY" and rv_iv_ratio < 0.7:
                    print(f"[CHOP-BLOCK] archetype=VOL_PLAY model={model.name} "
                          f"rv_iv_ratio={rv_iv_ratio:.3f}<0.7 chop_score={chop_score:.2f}")
                    continue
            elif chop_label == "MIXED":
                mult = _CHOP_MIXED_MULT.get(archetype_name, 0.7)
                if mult < 1.0:
                    print(f"[CHOP-DOWNWEIGHT] archetype={archetype_name} model={model.name} "
                          f"confidence {confidence:.3f} × {mult:.1f} chop_score={chop_score:.2f}")
                elif mult > 1.0:
                    print(f"[CHOP-BOOST] archetype={archetype_name} model={model.name} "
                          f"confidence {confidence:.3f} × {mult:.1f} chop_score={chop_score:.2f}")
                confidence = min(0.95, confidence * mult)

            # ── Strike selection ──
            opt_type = "CALL" if direction == SignalDirection.BULLISH else "PUT"

            # Non-contrarian/sector/premarket: determine action from VIX
            if model not in (ModelType.CONTRARIAN, ModelType.EV_SECTOR, ModelType.PREMARKET):
                if model != ModelType.VOLATILITY:
                    vix_now = intel.get("macro_regime", {}).get("vix_spot", 20) or 20
                    action = "SELL" if vix_now > 25 and confidence > 0.8 else "BUY"

            # ── Phase 14: Greeks-aware strike selection ──────────────────────
            # Use find_best_expiry_for_archetype so the expiry is always a real
            # Tradier-listed date matching the archetype's DTE preference, rather
            # than a guessed Friday that may not exist in the chain.
            chain_expiry = find_best_expiry_for_archetype(archetype_name)
            strike_meta: StrikeSelection | None = None
            chain_iv = 0.0
            strike = 0.0
            chain_rows: list = []  # pre-init so exception handler can reference it

            try:
                chain_rows = get_chain_cache().get_chain(chain_expiry)
                sel_direction = (
                    "LONG_CALL" if opt_type == "CALL" and action == "BUY"
                    else "SHORT_CALL" if opt_type == "CALL" and action == "SELL"
                    else "LONG_PUT" if opt_type == "PUT" and action == "BUY"
                    else "SHORT_PUT"
                )
                _sel_result: StrikeSelectionResult = select_strike(
                    chain_rows=chain_rows,
                    archetype_name=archetype_name,
                    spot=spot,
                    direction=sel_direction,
                    expiry=chain_expiry,
                    min_open_interest=_LIQ_MIN_OI,
                    min_volume_today=_LIQ_MIN_VOL,
                    max_bid_ask_pct=_LIQ_MAX_SPR,
                    min_absolute_bid=_LIQ_MIN_BID,
                )
                strike_meta = _sel_result.selected
                if strike_meta is None:
                    print(f"[STRIKE-REJECT] {model.name} {opt_type} {archetype_name}: "
                          f"no strike passed all filters (expiry={chain_expiry})")
                    # Build rich reason_detail from rejection audit
                    _audit = _sel_result.rejection_audit
                    _elim = _audit.get("filter_eliminations", {})
                    _total = _audit.get("total_candidates", 0)
                    _parts = [f"{k}={v}" for k, v in _elim.items() if v > 0]
                    _reason_detail = (
                        f"{_total} candidates rejected: {', '.join(_parts)}"
                        if _parts else f"{_total} candidates, all rejected by unknown filter"
                    )
                    _top20 = sorted(chain_rows, key=lambda r: r.open_interest, reverse=True)[:20]
                    _macro_str = intel.get("macro_regime", {}).get("regime", "NEUTRAL")
                    emit_rejection(
                        model=model.name, opt_type=opt_type, archetype=archetype_name,
                        reason="no_strike_passed_filters", expiry=chain_expiry,
                        model_id=model.name,
                        direction=direction.name,
                        confidence=confidence,
                        ticker="TSLA",
                        option_type=opt_type,
                        expiration_date=chain_expiry,
                        reason_code="no_strike_passed_filters",
                        reason_detail=_reason_detail,
                        spot_at_rejection=spot,
                        target_strike_attempted=_sel_result.target_strike_attempted,
                        chain_snapshot=json.dumps([_chain_row_to_dict(r) for r in _top20]),
                        strike_selector_breakdown=json.dumps(_audit.get("per_strike", [])),
                        chop_regime_at_rejection=chop_label,
                        regime_context=json.dumps({
                            "macro_regime": _macro_str,
                            "correlation_regime": corr_regime,
                        }),
                    )
                    continue
                strike = strike_meta.strike
                chain_iv = strike_meta.iv
            except Exception as _se:
                # Chain unavailable — log [STRIKE-SELECT-FAIL] and DROP the signal.
                # Per Phase 14 mandate: never emit a signal with an unvalidated strike.
                # The old moneyness fallback path is intentionally removed.
                print(
                    f"[STRIKE-SELECT-FAIL] {model.name} {opt_type} {archetype_name}: "
                    f"strike_selector raised exception (expiry={chain_expiry}): {_se}"
                )
                _macro_str_exc = intel.get("macro_regime", {}).get("regime", "NEUTRAL")
                emit_rejection(
                    model=model.name, opt_type=opt_type, archetype=archetype_name,
                    reason=f"strike_selector_exception:{_se}", expiry=chain_expiry,
                    model_id=model.name,
                    direction=direction.name,
                    confidence=confidence,
                    ticker="TSLA",
                    option_type=opt_type,
                    expiration_date=chain_expiry,
                    reason_code="strike_selector_exception",
                    reason_detail=str(_se),
                    spot_at_rejection=spot,
                    chop_regime_at_rejection=chop_label,
                    regime_context=json.dumps({
                        "macro_regime": _macro_str_exc,
                        "correlation_regime": corr_regime,
                    }),
                )
                continue

            # ── MANDATE: Never sell naked. All SELL actions → credit spread ──
            if action == "SELL":
                is_spread = True
                short_strike = strike
                wing_offset = 5.0
                long_target = strike + wing_offset if opt_type == "CALL" else strike - wing_offset
                try:
                    chain = get_chain_cache().get_chain(chain_expiry if chain_expiry else "")
                    wing_candidates = [r for r in chain if r.option_type == opt_type and r.open_interest >= 50]
                    long_strike = (
                        min(wing_candidates, key=lambda r: abs(r.strike - long_target)).strike
                        if wing_candidates else round(long_target / 5.0) * 5.0
                    )
                except Exception:
                    long_strike = round(long_target / 5.0) * 5.0

                try:
                    chain = get_chain_cache().get_chain(chain_expiry if chain_expiry else "")
                    short_row = next((r for r in chain if r.option_type == opt_type and abs(r.strike - short_strike) < 0.5), None)
                    long_row = next((r for r in chain if r.option_type == opt_type and abs(r.strike - long_strike) < 0.5), None)
                    limit_price = round(max(0.01, short_row.bid - long_row.ask), 2) if short_row and long_row else round(abs(short_strike - long_strike) * 0.15, 2)
                except Exception:
                    limit_price = round(abs(short_strike - long_strike) * 0.15, 2)
                # Credit spreads: TP = 50% of credit (buy back cheap), SL = 2× credit
                take_profit_price = round(limit_price * 0.5, 2)
                stop_loss_price = round(limit_price * 2.0, 2)
            else:
                # Single leg — real chain mid price
                try:
                    chain = get_chain_cache().get_chain(chain_expiry if chain_expiry else "")
                    opt_row = next((r for r in chain if r.option_type == opt_type and abs(r.strike - strike) < 0.5), None)
                    limit_price = round(opt_row.mid_price, 2) if opt_row else max(0.05, round(chain_iv * abs(strike - spot) * 0.1, 2))
                except Exception:
                    limit_price = max(0.05, round(abs(strike - spot) * 0.01 + 0.10, 2))

                # ── Asymmetric TP/SL from archetype R:R ─────────────────────
                # stop_loss set at 100% loss of premium (option goes to near-zero)
                # take_profit = entry + rr * (entry - stop_loss)
                stop_loss_price = round(max(0.01, limit_price * 0.10), 2)  # ~90% loss
                take_profit_price = round(
                    limit_price + archetype_rr * (limit_price - stop_loss_price), 2
                )

            # ── Regime-conditional Kelly + vol-targeting ─────────────────────
            # Formula: final_risk_pct = archetype.risk_pct × vix_mult × regime_mult
            # capped at 2% of notional (Phase 10 hard cap).
            # kelly_wager_pct tracks the Kelly fraction for audit; notional sizing
            # uses the archetype-adjusted final_risk_pct via compute_notional_sizing.
            # Refs: Thorp (2006), AQR Vol-Targeting (2012), Man AHL (2025).
            full_kelly = max(0.0, 2 * confidence - 1)

            macro_data = intel.get("macro_regime", {})
            vix_now = macro_data.get("vix_spot", 20) or 20
            regime   = macro_data.get("regime", "NEUTRAL")
            realized_vol = macro_data.get("tsla_realized_vol", 0.0) or 0.0
            # ATM IV from the selected option row; use chain_iv computed above
            implied_vol_for_sizing = chain_iv if chain_iv > 0 else 0.0

            # VIX-tiered base fraction (quarter-Kelly staircase)
            if vix_now > 30:
                kelly_base_fraction = 0.20  # HIGH_VIX: 1/5 full Kelly
            elif vix_now > 20:
                kelly_base_fraction = 0.35  # MED_VIX
            else:
                kelly_base_fraction = 0.50  # LOW_VIX: half Kelly max

            # Vol-targeting ratio: if IV > realized, options are "rich" → size down
            if implied_vol_for_sizing > 0 and realized_vol > 0:
                vol_ratio = min(1.0, realized_vol / implied_vol_for_sizing)
            else:
                vol_ratio = 1.0

            # RISK_OFF override: half position in risk-off regime regardless of VIX
            regime_multiplier = 0.5 if regime == "RISK_OFF" else 1.0

            if model == ModelType.CONTRARIAN:
                kelly = full_kelly * 0.02
                final_multiplier = 0.02
                final_risk_pct = min(0.02, archetype_risk_pct * 0.02)
            else:
                final_multiplier = kelly_base_fraction * vol_ratio * regime_multiplier
                kelly = full_kelly * final_multiplier
                # Archetype risk_pct scaled by same VIX/regime multipliers, hard-capped 2%
                final_risk_pct = min(0.02, archetype_risk_pct * kelly_base_fraction * regime_multiplier)

            # ── Notional-risk sizing ─────────────────────────────────────────
            qty, _size_reason = compute_notional_sizing(
                notional=NOTIONAL,
                risk_pct=final_risk_pct,
                entry_price=limit_price,
                stop_loss_price=stop_loss_price,
                premium=limit_price,
                is_spread=is_spread,
            )

            # Log sizing decision to fills_audit (non-blocking: fire-and-forget via queue)
            try:
                import uuid as _uuid, json as _json
                _audit_row = {
                    "id": str(_uuid.uuid4()),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                    "model_id": model.name,
                    "regime": regime,
                    "vix": vix_now,
                    "kelly_base_fraction": kelly_base_fraction if model != ModelType.CONTRARIAN else 0.02,
                    "vol_ratio": vol_ratio,
                    "regime_multiplier": regime_multiplier,
                    "final_multiplier": final_multiplier,
                    "contracts_sized": qty,
                    "kelly_wager_pct": kelly,
                    "confidence": confidence,
                    "raw_json": _json.dumps({
                        "model": model.name, "direction": direction.name,
                        "strike": float(strike) if strike else 0,
                        "implied_vol": implied_vol_for_sizing,
                        "realized_vol": realized_vol,
                    }),
                }
                await _logger.log_kelly_audit(_audit_row)
            except Exception as _ae:
                pass  # Audit failure must never block signal emission

            # Validate strikes at $5 chain increments
            strike = round(strike / 5.0) * 5.0
            if is_spread:
                short_strike = round(short_strike / 5.0) * 5.0
                long_strike = round(long_strike / 5.0) * 5.0

            # Dedup check: skip if same model+direction+strike fired recently
            sig_key = f"{model.name}_{direction.name}_{opt_type}_{strike}"
            now_ts = time.time()
            if sig_key in _recent_signals and now_ts - _recent_signals[sig_key] < SIGNAL_COOLDOWN:
                continue  # Skip — same signal fired within cooldown
            _recent_signals[sig_key] = now_ts
            # Clean old entries
            _recent_signals = {k: v for k, v in _recent_signals.items() if now_ts - v < SIGNAL_COOLDOWN}

            scan_sig = ModelSignal(
                model_id=model,
                direction=direction,
                confidence=confidence,
                timestamp=time.time(),
                ticker="TSLA",
                underlying_price=spot,
                price_source="TRIPLE-CONSENSUS (YF, GOOG, CNBC)",
                strategy_code=strategies[model],
                recommended_strike=float(strike),
                short_strike=float(short_strike),
                long_strike=float(long_strike),
                is_spread=is_spread,
                recommended_expiry=archetype_expiry_str,
                option_type=opt_type,
                action=action,
                expiration_date=chain_expiry if chain_expiry else find_best_expiry_for_archetype(archetype_name),
                target_limit_price=float(limit_price),
                take_profit_price=float(take_profit_price),
                stop_loss_price=float(stop_loss_price),
                kelly_wager_pct=float(kelly),
                quantity=qty,
                confidence_rationale=f"SNIPER ALERT: {rationale}",
                implied_volatility=float(chain_iv),
            )
            # Phase 14: attach strike selection meta for UI drill-down + attribution
            if strike_meta is not None:
                import dataclasses as _dc
                scan_sig.strike_selection_meta = _dc.asdict(strike_meta)
            # Tag signal with chop regime so logger can persist it for attribution
            scan_sig.chop_label = chop_label

            # ── Commission viability gate ─────────────────────────────────────
            if scan_sig.quantity > 0 and scan_sig.take_profit_price > 0:
                global _rejected_commission_total
                viable, reason = signal_is_commission_viable(
                    scan_sig.target_limit_price,
                    scan_sig.take_profit_price,
                    scan_sig.stop_loss_price,
                    scan_sig.quantity,
                    is_spread=scan_sig.is_spread,
                )
                if not viable:
                    print(f"[REJECT] commission-negative signal ({model.name} {opt_type} ${strike:.0f}): {reason}")
                    SIGNAL_REJECTED_COMMISSION.inc()
                    _rejected_commission_total += 1
                    _write_publisher_metrics()
                    continue

            # ── Minimum-edge floor gate ───────────────────────────────────────
            if scan_sig.quantity > 0 and scan_sig.take_profit_price > 0:
                global _rejected_minedge_total
                edge_ok, edge_reason = signal_passes_min_edge(
                    scan_sig.target_limit_price,
                    scan_sig.take_profit_price,
                    scan_sig.quantity,
                    notional=NOTIONAL,
                    is_spread=scan_sig.is_spread,
                )
                if not edge_ok:
                    print(f"[REJECT] min-edge floor ({model.name} {opt_type} ${strike:.0f}): {edge_reason}")
                    SIGNAL_REJECTED_MINEDGE.inc()
                    _rejected_minedge_total += 1
                    _write_publisher_metrics()
                    continue

            try:
                await publisher.publish_signal(scan_sig, spot_sources=spot_sources)
                await _logger.log_signal(scan_sig)
                print(f"[SIGNAL] {model.name} → {direction.name} {opt_type} ${strike:.0f} conf={confidence:.2f}")
            except Exception as e:
                print(f"Broadcasting error: {e}")
                await asyncio.sleep(5)
                await publisher.connect()

        # Process heartbeat — always emitted, even when no model fires.
        # This is a liveness pulse to SQLite + NATS; NOT a trading signal.
        await emit_heartbeat_async("publisher", status="ok", logger=_logger)

        # Scan interval: 10-20 seconds between full model sweeps
        await asyncio.sleep(random.uniform(10, 20))


if __name__ == "__main__":
    try:
        asyncio.run(broadcast_loop())
    except KeyboardInterrupt:
        print("Intelligence Engine: Shutdown requested.")
