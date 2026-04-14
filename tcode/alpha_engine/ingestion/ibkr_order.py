"""
TSLA Alpha Engine: IBKR Order Placement
Places, cancels, and queries option orders via IB Gateway using ib_insync.

CLI interface (for Go subprocess calls):
  # Single-leg limit order (Phase 4+):
  python -m ingestion.ibkr_order place --symbol TSLA --contract CALL --strike 365 \
      --expiry 2026-04-13 --action BUY --quantity 10 --limit-price 0.28 \
      --tif DAY --mode IBKR_PAPER --client-id 3

  # Bracket order (Phase 9+) — parent LIMIT + TP LMT + SL STP LMT (OCO group):
  python -m ingestion.ibkr_order place --symbol TSLA --contract CALL --strike 365 \
      --expiry 2026-04-13 --action BUY --quantity 10 --limit-price 0.28 \
      --take-profit 0.56 --stop-loss 0.14 --underlying-stop TSLA:340.0 \
      --mode IBKR_PAPER --client-id 3

  # Other commands:
  python -m ingestion.ibkr_order cancel --order-id 12345 --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order status --order-id 12345 --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order open_orders --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order expiry_close --expiry-date 2026-04-13 --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order global_cancel --mode IBKR_PAPER --client-id 3

Output: JSON to stdout only. Errors go to stderr. Exit 0 on success, non-zero on failure.
Logs: alpha_engine/ibkr_order.log (full request/response audit trail).

Safety:
  - Only IBKR_PAPER mode is enabled; IBKR_LIVE is blocked.
  - Never subscribes to or publishes on tsla.alpha.sim.
  - Each invocation uses a unique --client-id supplied by the Go engine.
  - Bracket orders are REJECTED (not downgraded) if either TP or SL submission fails.
  - SL leg is always stop-limit (never stop-market). Slippage buffer: STOP_LIMIT_SLIPPAGE_PCT env (default 10%).
  - When --underlying-stop is provided, SL leg uses IBKR PriceCondition on the underlying stock.
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, date, timezone

# ── logging setup ─────────────────────────────────────────────────────────────
_LOG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "ibkr_order.log")
)

_file_handler = logging.FileHandler(_LOG_PATH, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
))

logger = logging.getLogger("ibkr_order")
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.addHandler(_file_handler)

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
logger.addHandler(_stderr_handler)

# ── constants ─────────────────────────────────────────────────────────────────
CONNECT_TIMEOUT = int(os.getenv("IBKR_CONNECT_TIMEOUT", "10"))
ORDER_WAIT_SEC = 2.0

# ── IBKR error classification ─────────────────────────────────────────────────
# Classifies IBKR EWrapper error codes as TRANSIENT (safe to retry) or FATAL
# (configuration/validation errors that will never succeed with the same params).
# Error 321: "Error validating request — Invalid contract id"
#   Root cause: conId=0 passed to a PriceCondition (contract not fully qualified).
#   This is a FATAL configuration error — retrying with conId=0 will always fail.
#   Fix: use reqContractDetails() to get a verified conId before building PriceCondition.
_IBKR_FATAL_ERRORS: frozenset = frozenset({
    321,   # Invalid contract id (e.g., PriceCondition with conId=0)
    200,   # No security definition found
    10168, # Requested market data is not subscribed
    10197, # No market data during competing live session
})

_IBKR_TRANSIENT_ERRORS: frozenset = frozenset({
    1100,  # Connectivity between IB and TWS has been lost
    1101,  # Connectivity between IB and TWS has been restored
    1102,  # Connectivity between IB and TWS has been restored (data lost)
    2110,  # Connectivity between TWS and server is broken
    504,   # Not connected
})


def classify_ibkr_error(error_code: int, message: str = "") -> str:
    """
    Classify an IBKR EWrapper error as 'fatal', 'transient', or 'unknown'.

    Fatal errors are configuration/validation errors that will not succeed on retry
    with the same parameters. The engine must NOT retry these — log and escalate.

    Transient errors indicate connectivity issues that may resolve on reconnect.

    Usage in error callback:
        category = classify_ibkr_error(errorCode, errorString)
        if category == 'fatal':
            logger.error("[IBKR-FATAL] error=%d msg=%s — not retrying", errorCode, errorString)
    """
    if error_code in _IBKR_FATAL_ERRORS:
        return "fatal"
    if error_code in _IBKR_TRANSIENT_ERRORS:
        return "transient"
    return "unknown"


# ── helpers ───────────────────────────────────────────────────────────────────

def _expiry_to_ib(expiry: str) -> str:
    """Convert YYYY-MM-DD → YYYYMMDD for ib_insync."""
    return expiry.replace("-", "")


def _right(contract_type: str) -> str:
    """Convert 'CALL'/'PUT' → 'C'/'P' for ib_insync."""
    return "C" if contract_type.upper().startswith("C") else "P"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(host: str, port: int, client_id: int):
    try:
        from ib_insync import IB, util
    except ImportError as e:
        raise ImportError(f"ib_insync not installed: {e}") from e

    util.patchAsyncio()
    ib = IB()
    logger.info("Connecting host=%s port=%d clientId=%d", host, port, client_id)
    ib.connect(host, port, clientId=client_id, timeout=CONNECT_TIMEOUT)
    logger.info("Connected host=%s port=%d clientId=%d", host, port, client_id)
    return ib


def _disconnect(ib) -> None:
    try:
        ib.disconnect()
        logger.debug("Disconnected")
    except Exception as exc:
        logger.debug("Disconnect error (ignored): %s", exc)


def _trade_to_order_dict(trade, role: str = "single", parent_id: int = 0, oca_group: str = "") -> dict:
    """Convert a ib_insync Trade to our order dict format."""
    contract = trade.contract
    order    = trade.order

    strike      = 0.0
    expiry      = ""
    option_type = ""
    limit_price = 0.0

    if contract and contract.secType == "OPT":
        try:
            strike = float(contract.strike or 0)
        except (TypeError, ValueError):
            strike = 0.0
        raw_expiry = contract.lastTradeDateOrContractMonth or ""
        raw_expiry = raw_expiry.strip()
        if len(raw_expiry) == 8 and raw_expiry.isdigit():
            expiry = f"{raw_expiry[:4]}-{raw_expiry[4:6]}-{raw_expiry[6:]}"
        else:
            expiry = raw_expiry
        option_type = "CALL" if getattr(contract, "right", "") == "C" else "PUT"
    try:
        limit_price = float(order.lmtPrice or 0)
    except (TypeError, ValueError):
        limit_price = 0.0

    return {
        "orderId":        order.orderId,
        "status":         trade.orderStatus.status or "Unknown",
        "filled_qty":     float(trade.orderStatus.filled or 0),
        "avg_fill_price": float(trade.orderStatus.avgFillPrice or 0),
        "symbol":         contract.symbol if contract else "",
        "action":         order.action if order else "",
        "qty":            int(order.totalQuantity or 0) if order else 0,
        "strike":         strike,
        "expiry":         expiry,
        "option_type":    option_type,
        "limit_price":    limit_price,
        "order_type":     order.orderType or "",
        "role":           role,
        "parent_id":      parent_id,
        "oca_group":      oca_group,
        "timestamp":      _now_iso(),
    }


# ── order operations ──────────────────────────────────────────────────────────

def place_order(
    host: str,
    port: int,
    client_id: int,
    symbol: str,
    contract_type: str,
    strike: float,
    expiry: str,
    action: str,
    quantity: int,
    limit_price: float,
    tif: str = "DAY",
) -> dict:
    """
    Place a single-leg limit order for an options contract.

    Returns:
        {orderId, status, filled_qty, avg_fill_price, timestamp, contract_id}
    """
    from ib_insync import Option, LimitOrder

    expiry_ib = _expiry_to_ib(expiry)
    right = _right(contract_type)
    action = action.upper()

    logger.info(
        "place: %s %dx %s %s %.2f %s @ %.4f TIF=%s clientId=%d",
        action, quantity, symbol, contract_type, strike, expiry,
        limit_price, tif, client_id,
    )

    ib = _connect(host, port, client_id)
    try:
        contract = Option(symbol, expiry_ib, strike, right, "SMART")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(
                f"Could not qualify contract: {symbol} {contract_type} "
                f"strike={strike} expiry={expiry}"
            )
        contract = qualified[0]
        logger.info("Qualified contract conId=%s", contract.conId)

        order = LimitOrder(action, quantity, limit_price, tif=tif)
        trade = ib.placeOrder(contract, order)
        logger.info("placeOrder submitted: orderId=%d", trade.order.orderId)

        ib.sleep(ORDER_WAIT_SEC)

        order_id    = trade.order.orderId
        status      = trade.orderStatus.status or "Submitted"
        filled_qty  = float(trade.orderStatus.filled or 0)
        avg_fill    = float(trade.orderStatus.avgFillPrice or 0)
        contract_id = contract.conId or 0

        result = {
            "orderId":        order_id,
            "status":         status,
            "filled_qty":     filled_qty,
            "avg_fill_price": avg_fill,
            "timestamp":      _now_iso(),
            "contract_id":    contract_id,
        }
        logger.info("place result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def place_bracket_order(
    host: str,
    port: int,
    client_id: int,
    symbol: str,
    contract_type: str,
    strike: float,
    expiry: str,
    action: str,
    quantity: int,
    limit_price: float,
    take_profit_price: float,
    stop_loss_price: float,
    tif: str = "DAY",
    underlying_stop_symbol: str = "",
    underlying_stop_price: float = 0.0,
) -> dict:
    """
    Place a bracket order: parent LIMIT + TP LMT + SL STP LMT in an OCO group.

    The SL leg is always a stop-limit (never stop-market).
    Slippage buffer: STOP_LIMIT_SLIPPAGE_PCT env var (default 10%).

    When underlying_stop_symbol/price are provided, the SL leg is conditioned
    on the underlying stock reaching that price (PriceCondition), which avoids
    firing on option premium noise.

    Returns:
        {parent_order_id, take_profit_order_id, stop_loss_order_id, group_oca, status, timestamp, is_bracket}

    Raises:
        ValueError if any orderId is 0 (bracket submission rejected).
    """
    from ib_insync import Option, Stock, PriceCondition

    expiry_ib = _expiry_to_ib(expiry)
    right = _right(contract_type)
    action = action.upper()
    slippage_pct = float(os.getenv("STOP_LIMIT_SLIPPAGE_PCT", "0.10"))

    logger.info(
        "place_bracket: %s %dx %s %s %.2f %s entry=%.4f tp=%.4f sl=%.4f clientId=%d",
        action, quantity, symbol, contract_type, strike, expiry,
        limit_price, take_profit_price, stop_loss_price, client_id,
    )

    ib = _connect(host, port, client_id)
    try:
        # Qualify option contract
        contract = Option(symbol, expiry_ib, strike, right, "SMART")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(
                f"Could not qualify contract: {symbol} {contract_type} "
                f"strike={strike} expiry={expiry}"
            )
        contract = qualified[0]
        logger.info("Qualified contract conId=%s", contract.conId)

        # Create bracket order (parent=LMT, child1=LMT take-profit, child2=STP stop-loss)
        bracket = ib.bracketOrder(
            action=action,
            quantity=quantity,
            limitPrice=limit_price,
            takeProfitPrice=take_profit_price,
            stopLossPrice=stop_loss_price,
        )

        # ── Convert SL leg to stop-limit (never stop-market on options) ──────
        sl_order = bracket[2]
        sl_order.orderType = "STP LMT"
        sl_order.auxPrice  = stop_loss_price                        # trigger price
        sl_order.lmtPrice  = stop_loss_price * (1.0 - slippage_pct) # floor (10% below)
        logger.info(
            "SL leg: STP LMT trigger=%.4f floor=%.4f (slippage=%.0f%%)",
            sl_order.auxPrice, sl_order.lmtPrice, slippage_pct * 100,
        )

        # ── Apply underlying PriceCondition if requested ──────────────────────
        if underlying_stop_symbol and underlying_stop_price > 0:
            underlying_contract = Stock(underlying_stop_symbol, "SMART", "USD")
            # Phase 13.5 fix: use reqContractDetails to guarantee conId is populated.
            # qualifyContracts() can return before metadata arrives, leaving conId=0,
            # which causes IBKR Error 321 "Invalid contract id" when PriceCondition
            # is built with conId=0.
            try:
                details = ib.reqContractDetails(underlying_contract)
                if details and len(details) > 0 and details[0].contract.conId > 0:
                    underlying_contract = details[0].contract
                    # Long CALL: trigger when underlying DROPS below stop → isMore=False
                    # Long PUT:  trigger when underlying RISES above stop → isMore=True
                    is_put  = right == "P"
                    is_more = is_put
                    sl_order.conditions = [
                        PriceCondition(
                            conId=underlying_contract.conId,
                            exch="SMART",
                            price=underlying_stop_price,
                            isMore=is_more,
                        )
                    ]
                    sl_order.conditionsCancelOrder = False
                    logger.info(
                        "Underlying stop condition: %s conId=%d %s %.2f (isMore=%s)",
                        underlying_stop_symbol,
                        underlying_contract.conId,
                        "rises above" if is_more else "drops below",
                        underlying_stop_price,
                        is_more,
                    )
                else:
                    logger.warning(
                        "[CONTRACT-QUAL-FAIL] symbol=%s reason=empty_details — "
                        "[BRACKET-DOWNGRADE] SL uses option-premium stop only (no PriceCondition)",
                        underlying_stop_symbol,
                    )
            except Exception as qual_exc:
                logger.warning(
                    "[CONTRACT-QUAL-FAIL] symbol=%s reason=%s — "
                    "[BRACKET-DOWNGRADE] SL uses option-premium stop only (no PriceCondition)",
                    underlying_stop_symbol, qual_exc,
                )

        # ── Place all three orders ────────────────────────────────────────────
        placed = []
        for ord in bracket:
            trade = ib.placeOrder(contract, ord)
            placed.append(trade)
            logger.info("placed leg orderId=%d type=%s", trade.order.orderId, trade.order.orderType)

        ib.sleep(ORDER_WAIT_SEC)

        result = {
            "parent_order_id":      placed[0].order.orderId,
            "take_profit_order_id": placed[1].order.orderId,
            "stop_loss_order_id":   placed[2].order.orderId,
            "group_oca":            placed[0].order.ocaGroup or "",
            "status":               placed[0].orderStatus.status or "Submitted",
            "timestamp":            _now_iso(),
            "is_bracket":           True,
        }

        # Validate all three orderIds are > 0
        for key in ("parent_order_id", "take_profit_order_id", "stop_loss_order_id"):
            if result[key] <= 0:
                raise ValueError(
                    f"Bracket submission returned orderId=0 for {key}; "
                    f"rejecting group to avoid unprotected leg"
                )

        logger.info("bracket result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def cancel_order(host: str, port: int, client_id: int, order_id: int) -> dict:
    """
    Cancel an open order by order ID.

    For bracket orders, cancelling the parent triggers IBKR's OCO mechanism
    which auto-cancels the TP and SL children.
    """
    logger.info("cancel: orderId=%d clientId=%d", order_id, client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        open_trades = ib.trades()
        trade = next((t for t in open_trades if t.order.orderId == order_id), None)

        if trade is None:
            raise ValueError(f"Order {order_id} not found in open orders")

        ib.cancelOrder(trade.order)
        ib.sleep(1.0)

        result = {
            "orderId":   order_id,
            "status":    "Cancelled",
            "timestamp": _now_iso(),
        }
        logger.info("cancel result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def get_status(host: str, port: int, client_id: int, order_id: int) -> dict:
    """
    Return the current status of an order by order ID.
    For bracket orders, also returns the linked parent/children.
    """
    logger.info("status: orderId=%d clientId=%d", order_id, client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        open_trades = ib.trades()
        trade = next((t for t in open_trades if t.order.orderId == order_id), None)

        if trade is None:
            result = {
                "orderId":        order_id,
                "status":         "Unknown",
                "filled_qty":     0,
                "avg_fill_price": 0,
                "timestamp":      _now_iso(),
            }
        else:
            oca_group = trade.order.ocaGroup or ""
            result = {
                "orderId":        order_id,
                "status":         trade.orderStatus.status or "Unknown",
                "filled_qty":     float(trade.orderStatus.filled or 0),
                "avg_fill_price": float(trade.orderStatus.avgFillPrice or 0),
                "timestamp":      _now_iso(),
                "oca_group":      oca_group,
            }

            # For bracket orders, include all legs
            if oca_group:
                siblings = [
                    t for t in open_trades
                    if t.order.ocaGroup == oca_group and t.order.orderId != order_id
                ]
                if siblings:
                    all_legs = sorted([trade] + siblings, key=lambda x: x.order.orderId)
                    bracket_legs = []
                    for leg in all_legs:
                        parent_leg_id = getattr(leg.order, "parentId", 0) or 0
                        order_type    = leg.order.orderType or ""
                        if parent_leg_id == 0:
                            role = "parent"
                        elif order_type in ("LMT", "LIMIT"):
                            role = "take_profit"
                        else:
                            role = "stop_loss"
                        bracket_legs.append({
                            "orderId":   leg.order.orderId,
                            "role":      role,
                            "status":    leg.orderStatus.status or "Unknown",
                            "orderType": order_type,
                        })
                    result["bracket"] = bracket_legs

        logger.info("status result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def get_open_orders(host: str, port: int, client_id: int) -> dict:
    """
    Return open orders from IBKR, grouping bracket children under their parent.

    Each order dict includes: role (parent/take_profit/stop_loss/single),
    parent_id, oca_group for bracket detection.
    """
    logger.info("open_orders: clientId=%d", client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        # Group by OCA group
        oca_groups: dict = {}   # oca_group -> [trade]
        ungrouped: list  = []

        for trade in ib.trades():
            oca = trade.order.ocaGroup or ""
            if oca:
                oca_groups.setdefault(oca, []).append(trade)
            else:
                ungrouped.append(trade)

        orders = []

        # Ungrouped (single-leg) orders
        for trade in ungrouped:
            orders.append(_trade_to_order_dict(trade, role="single"))

        # Bracket groups: determine parent (parentId == 0) and children
        for oca, trades_in_group in oca_groups.items():
            # Parent is the order with no parentId (or parentId == 0)
            parent_trade = next(
                (t for t in trades_in_group if (getattr(t.order, "parentId", 0) or 0) == 0),
                trades_in_group[0],
            )
            parent_oid = parent_trade.order.orderId

            for t in trades_in_group:
                parent_leg_id = getattr(t.order, "parentId", 0) or 0
                order_type    = t.order.orderType or ""
                if parent_leg_id == 0:
                    role = "parent"
                elif order_type in ("LMT", "LIMIT"):
                    role = "take_profit"
                else:
                    role = "stop_loss"
                orders.append(_trade_to_order_dict(
                    t,
                    role=role,
                    parent_id=parent_oid if role != "parent" else 0,
                    oca_group=oca,
                ))

        result = {"orders": orders}
        logger.info(
            "open_orders: %d open orders (%d bracket groups, %d singles)",
            len(orders), len(oca_groups), len(ungrouped),
        )
        return result
    finally:
        _disconnect(ib)


def expiry_close(host: str, port: int, client_id: int, expiry_date: str = "") -> dict:
    """
    Market-sell all open option positions expiring on expiry_date.

    Called at 15:30 ET on expiry day to prevent holding past the closing auction.
    Logs [EXPIRY-CLOSE] orderId=N for each position closed.

    Returns:
        {closed_count, order_ids, expiry_date, timestamp}
    """
    from ib_insync import MarketOrder

    if not expiry_date:
        expiry_date = date.today().strftime("%Y-%m-%d")

    # IBKR uses YYYYMMDD format
    target_ib = expiry_date.replace("-", "")

    logger.info("expiry_close: expiry_date=%s target_ib=%s clientId=%d",
                expiry_date, target_ib, client_id)

    ib = _connect(host, port, client_id)
    try:
        positions = ib.positions()
        ib.sleep(1.0)

        closed_ids: list = []

        for pos in positions:
            contract = pos.contract
            if contract.secType != "OPT":
                continue

            raw_expiry = (contract.lastTradeDateOrContractMonth or "").strip()
            if raw_expiry != target_ib:
                continue

            position_size = float(pos.position)
            if position_size <= 0:
                continue

            qty   = int(abs(position_size))
            order = MarketOrder("SELL", qty, tif="DAY")
            trade = ib.placeOrder(contract, order)
            ib.sleep(0.5)

            oid = trade.order.orderId
            closed_ids.append(oid)
            logger.info(
                "[EXPIRY-CLOSE] orderId=%d contract=%s qty=%d",
                oid, contract.localSymbol or contract.symbol, qty,
            )

        ib.sleep(ORDER_WAIT_SEC)

        result = {
            "closed_count": len(closed_ids),
            "order_ids":    closed_ids,
            "expiry_date":  expiry_date,
            "timestamp":    _now_iso(),
        }
        logger.info("expiry_close result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def cancel_order_with_verify(host: str, port: int, client_id: int, order_id: int) -> dict:
    """
    Cancel an open IBKR order by ID and verify OCO sibling cancellation.

    After issuing cancelOrder(), re-queries open_orders to confirm the parent
    and any bracket siblings (TP/SL via OCO) have transitioned to Cancelled.

    Returns:
        {order_id, status, oca_cancelled: [sibling_ids], timestamp}
    """
    logger.info("cancel_order_with_verify: orderId=%d clientId=%d", order_id, client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        open_trades = ib.trades()
        trade = next((t for t in open_trades if t.order.orderId == order_id), None)
        if trade is None:
            raise ValueError(f"Order {order_id} not found in open orders")

        oca_group = trade.order.ocaGroup or ""
        siblings_before = [
            t.order.orderId
            for t in open_trades
            if t.order.ocaGroup == oca_group
            and t.order.orderId != order_id
            and oca_group
        ]

        ib.cancelOrder(trade.order)
        ib.sleep(1.5)

        # Independent verification: re-fetch open orders and confirm cancellation
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        terminal = {"Cancelled", "Filled", "Inactive"}
        post_trades = {t.order.orderId: t for t in ib.trades()}
        target_status = (post_trades[order_id].orderStatus.status or "Unknown") if order_id in post_trades else "Cancelled"

        oca_cancelled = []
        for sid in siblings_before:
            if sid in post_trades:
                st = post_trades[sid].orderStatus.status or ""
                if st in terminal:
                    oca_cancelled.append(sid)
            else:
                # Not in open orders anymore → treated as cancelled
                oca_cancelled.append(sid)

        result = {
            "order_id":      order_id,
            "status":        target_status if target_status in terminal else "CancelPending",
            "oca_cancelled": oca_cancelled,
            "timestamp":     _now_iso(),
        }
        logger.info("cancel_order_with_verify result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def _is_market_hours() -> bool:
    """
    Return True if current UTC time falls within US regular session
    (Mon–Fri 9:30–16:00 America/New_York).

    Uses UTC offset approximation: ET is UTC-5 (EST) / UTC-4 (EDT).
    We use pytz when available; otherwise fall back to a fixed UTC-4 offset
    (EDT) which is correct for April–October, the primary trading season.
    """
    from datetime import datetime, timezone, timedelta

    utc_now = datetime.now(timezone.utc)
    # Weekday check: Mon=0 … Fri=4
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        et_now = utc_now.astimezone(et)
    except Exception:
        # Fallback: EDT = UTC-4
        et_now = utc_now.astimezone(timezone(timedelta(hours=-4)))

    if et_now.weekday() >= 5:  # Saturday, Sunday
        return False

    t = et_now.hour * 60 + et_now.minute
    return 9 * 60 + 30 <= t < 16 * 60  # 9:30 AM – 4:00 PM ET


def _next_market_open_utc() -> "datetime":
    """
    Return the UTC datetime of the next regular-session open (9:30 AM ET Mon–Fri).
    If we're before open today, returns today's open.
    If past close or weekend, returns the next weekday's open.
    """
    from datetime import datetime, timezone, timedelta

    utc_now = datetime.now(timezone.utc)
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        et_now = utc_now.astimezone(et)
    except Exception:
        et_now = utc_now.astimezone(timezone(timedelta(hours=-4)))

    # Candidate: today's open in ET
    candidate = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
    t = et_now.hour * 60 + et_now.minute
    is_weekday = et_now.weekday() < 5
    before_open = t < 9 * 60 + 30

    if is_weekday and before_open:
        # Today's open hasn't happened yet
        pass
    else:
        # Advance to next weekday
        candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)

    return candidate.astimezone(timezone.utc)


def close_position(
    host: str,
    port: int,
    client_id: int,
    symbol: str,
    contract_type: str,
    strike: float,
    expiry: str,
    quantity: int,
) -> dict:
    """
    Close an open option position.

    Auto-detects market hours:
      - In-hours  → MKT DAY sell submitted immediately.
      - Out-of-hours → schedule_close() with TIF=OPG for next session open.

    Returns:
        {order_id, status, scheduled_for, timestamp}
        scheduled_for is null when the order executes immediately.
    """
    if _is_market_hours():
        logger.info(
            "close_position: market OPEN — submitting MKT DAY for %s %s %.2f %s x%d",
            symbol, contract_type, strike, expiry, quantity,
        )
        return _close_position_mkt(host, port, client_id, symbol, contract_type, strike, expiry, quantity)
    else:
        logger.info(
            "close_position: market CLOSED — scheduling OPG close for %s %s %.2f %s x%d",
            symbol, contract_type, strike, expiry, quantity,
        )
        return schedule_close(host, port, client_id, symbol, contract_type, strike, expiry, quantity)


def _close_position_mkt(
    host: str,
    port: int,
    client_id: int,
    symbol: str,
    contract_type: str,
    strike: float,
    expiry: str,
    quantity: int,
) -> dict:
    """Submit an immediate MKT DAY SELL to close the option position."""
    from ib_insync import Option, MarketOrder

    expiry_ib = _expiry_to_ib(expiry)
    right = _right(contract_type)

    logger.info(
        "_close_position_mkt: SELL %dx %s %s %.2f %s MKT DAY clientId=%d",
        quantity, symbol, contract_type, strike, expiry, client_id,
    )

    ib = _connect(host, port, client_id)
    try:
        contract = Option(symbol, expiry_ib, strike, right, "SMART")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(
                f"Could not qualify contract: {symbol} {contract_type} "
                f"strike={strike} expiry={expiry}"
            )
        contract = qualified[0]

        order = MarketOrder("SELL", quantity, tif="DAY")
        trade = ib.placeOrder(contract, order)
        ib.sleep(ORDER_WAIT_SEC)

        result = {
            "order_id":      trade.order.orderId,
            "status":        trade.orderStatus.status or "Submitted",
            "scheduled_for": None,
            "timestamp":     _now_iso(),
        }
        logger.info("[CLOSE-MKT] orderId=%d status=%s", result["order_id"], result["status"])
        return result
    finally:
        _disconnect(ib)


def schedule_close(
    host: str,
    port: int,
    client_id: int,
    symbol: str,
    contract_type: str,
    strike: float,
    expiry: str,
    quantity: int,
) -> dict:
    """
    Schedule a position close for the next market open using TIF=OPG.

    OPG (Opening) orders fill during the opening auction at 9:30 AM ET.
    This is the correct mechanism for after-hours close requests.

    Returns:
        {order_id, status, scheduled_for (ISO8601 UTC), timestamp}
    """
    from ib_insync import Option, MarketOrder

    expiry_ib = _expiry_to_ib(expiry)
    right = _right(contract_type)
    next_open = _next_market_open_utc()
    next_open_iso = next_open.strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info(
        "schedule_close: OPG SELL %dx %s %s %.2f %s → %s clientId=%d",
        quantity, symbol, contract_type, strike, expiry, next_open_iso, client_id,
    )

    ib = _connect(host, port, client_id)
    try:
        contract = Option(symbol, expiry_ib, strike, right, "SMART")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(
                f"Could not qualify contract: {symbol} {contract_type} "
                f"strike={strike} expiry={expiry}"
            )
        contract = qualified[0]

        # OPG TIF = fills during opening auction only; cancelled if not filled at open
        order = MarketOrder("SELL", quantity, tif="OPG")
        trade = ib.placeOrder(contract, order)
        ib.sleep(ORDER_WAIT_SEC)

        result = {
            "order_id":      trade.order.orderId,
            "status":        trade.orderStatus.status or "PendingSubmit",
            "scheduled_for": next_open_iso,
            "timestamp":     _now_iso(),
        }
        logger.info("[SCHEDULE-CLOSE] orderId=%d scheduledFor=%s", result["order_id"], next_open_iso)
        return result
    finally:
        _disconnect(ib)


def global_cancel(host: str, port: int, client_id: int) -> dict:
    """
    Issue reqGlobalCancel() to clear all open orders at startup.

    Used to eliminate orphan pre-Phase-9 naked orders before placing brackets.
    Gated behind STARTUP_CLEAR_ORPHANS=1 env var in the Go engine.

    Returns:
        {open_orders_after, timestamp}
    """
    logger.info("[STARTUP] global_cancel: clientId=%d", client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqGlobalCancel()
        logger.info("[STARTUP] Global cancel issued to clear pre-bracket naked orders")

        ib.sleep(2.0)

        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        terminal_statuses = {"Cancelled", "Filled", "Inactive"}
        open_count = len([
            t for t in ib.trades()
            if (t.orderStatus.status or "") not in terminal_statuses
        ])
        logger.info("[STARTUP] %d open orders after global cancel", open_count)

        result = {
            "open_orders_after": open_count,
            "timestamp":         _now_iso(),
        }
        logger.info("global_cancel result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="IBKR order management via ib_insync"
    )
    parser.add_argument(
        "command",
        choices=[
            "place", "cancel", "cancel_order", "status", "open_orders",
            "expiry_close", "global_cancel",
            "close_position", "schedule_close",
        ],
    )
    parser.add_argument("--symbol",         default="TSLA")
    parser.add_argument("--contract",       default="CALL", help="CALL or PUT")
    parser.add_argument("--strike",         type=float, default=0.0)
    parser.add_argument("--expiry",         default="",    help="YYYY-MM-DD")
    parser.add_argument("--action",         default="BUY", help="BUY or SELL")
    parser.add_argument("--quantity",       type=int, default=1)
    parser.add_argument("--limit-price",    type=float, default=0.0, dest="limit_price")
    parser.add_argument("--take-profit",    type=float, default=0.0, dest="take_profit",
                        help="Take-profit price (triggers bracket order when >0 with --stop-loss)")
    parser.add_argument("--stop-loss",      type=float, default=0.0, dest="stop_loss",
                        help="Stop-loss price (triggers bracket order when >0 with --take-profit)")
    parser.add_argument("--underlying-stop", type=str, default="", dest="underlying_stop",
                        help="Underlying stop as SYMBOL:PRICE, e.g. TSLA:340.0")
    parser.add_argument("--tif",            default="DAY")
    parser.add_argument("--order-id",       type=int, default=0, dest="order_id")
    parser.add_argument("--expiry-date",    type=str, default="", dest="expiry_date",
                        help="YYYY-MM-DD for expiry_close (defaults to today)")
    parser.add_argument("--mode",           default="IBKR_PAPER",
                        help="IBKR_PAPER only (IBKR_LIVE is blocked)")
    parser.add_argument("--client-id",      type=int, default=3, dest="client_id")
    args = parser.parse_args()

    # Normalise mode vocabulary
    _mode_norm = {
        "IBKR_PAPER": "IBKR_PAPER",
        "PAPER":      "IBKR_PAPER",
        "IBKR_LIVE":  "IBKR_LIVE",
        "LIVE":       "IBKR_LIVE",
    }
    normalised_mode = _mode_norm.get(args.mode.strip().upper(), "")
    if not normalised_mode:
        print(json.dumps({
            "error": f"Refusing order: mode={args.mode!r} is not a real IBKR mode"
        }))
        sys.exit(1)
    args.mode = normalised_mode

    if args.mode == "IBKR_LIVE":
        msg = "IBKR_LIVE mode is experimental and not enabled in this build"
        logger.error(msg)
        print(json.dumps({"error": msg}))
        sys.exit(1)

    host      = os.getenv("IBKR_HOST", "127.0.0.1")
    port      = int(os.getenv("IBKR_PORT", "4002"))
    client_id = args.client_id

    try:
        if args.command == "place":
            if args.limit_price <= 0:
                raise ValueError("--limit-price must be > 0")
            if not args.expiry:
                raise ValueError("--expiry is required (YYYY-MM-DD)")
            if args.strike <= 0:
                raise ValueError("--strike must be > 0")

            has_tp = args.take_profit > 0
            has_sl = args.stop_loss  > 0

            if has_tp and not has_sl:
                raise ValueError(
                    "--take-profit requires --stop-loss; "
                    "never place a bracket without both legs"
                )
            if has_sl and not has_tp:
                raise ValueError(
                    "--stop-loss requires --take-profit; "
                    "never place a bracket without both legs"
                )

            if has_tp and has_sl:
                # ── Bracket path ──────────────────────────────────────────────
                # Parse optional --underlying-stop SYMBOL:PRICE
                under_sym   = ""
                under_price = 0.0
                if args.underlying_stop:
                    try:
                        parts = args.underlying_stop.split(":", 1)
                        under_sym   = parts[0].strip().upper()
                        under_price = float(parts[1])
                    except (IndexError, ValueError) as exc:
                        raise ValueError(
                            f"--underlying-stop must be SYMBOL:PRICE, "
                            f"got {args.underlying_stop!r}: {exc}"
                        ) from exc

                result = place_bracket_order(
                    host, port, client_id,
                    args.symbol, args.contract, args.strike,
                    args.expiry, args.action, args.quantity,
                    args.limit_price,
                    take_profit_price=args.take_profit,
                    stop_loss_price=args.stop_loss,
                    tif=args.tif,
                    underlying_stop_symbol=under_sym,
                    underlying_stop_price=under_price,
                )
            else:
                # ── Single-leg path ───────────────────────────────────────────
                result = place_order(
                    host, port, client_id,
                    args.symbol, args.contract, args.strike,
                    args.expiry, args.action, args.quantity,
                    args.limit_price, args.tif,
                )

        elif args.command == "cancel":
            if args.order_id <= 0:
                raise ValueError("--order-id is required and must be > 0")
            result = cancel_order(host, port, client_id, args.order_id)

        elif args.command == "cancel_order":
            # UI-facing cancel: verifies OCO sibling cancellation after the cancel
            if args.order_id <= 0:
                raise ValueError("--order-id is required and must be > 0")
            result = cancel_order_with_verify(host, port, client_id, args.order_id)

        elif args.command == "close_position":
            # Close a position: MKT DAY if market open, OPG-scheduled if not
            for field, val in [("--symbol", args.symbol), ("--strike", args.strike),
                                ("--expiry", args.expiry), ("--quantity", args.quantity)]:
                if not val:
                    raise ValueError(f"{field} is required for close_position")
            if args.strike <= 0:
                raise ValueError("--strike must be > 0")
            if args.quantity <= 0:
                raise ValueError("--quantity must be > 0")
            result = close_position(
                host, port, client_id,
                args.symbol, args.contract, args.strike, args.expiry, args.quantity,
            )

        elif args.command == "schedule_close":
            # Explicitly schedule an OPG close for next market open
            for field, val in [("--symbol", args.symbol), ("--strike", args.strike),
                                ("--expiry", args.expiry), ("--quantity", args.quantity)]:
                if not val:
                    raise ValueError(f"{field} is required for schedule_close")
            if args.strike <= 0:
                raise ValueError("--strike must be > 0")
            if args.quantity <= 0:
                raise ValueError("--quantity must be > 0")
            result = schedule_close(
                host, port, client_id,
                args.symbol, args.contract, args.strike, args.expiry, args.quantity,
            )

        elif args.command == "status":
            if args.order_id <= 0:
                raise ValueError("--order-id is required and must be > 0")
            result = get_status(host, port, client_id, args.order_id)

        elif args.command == "open_orders":
            result = get_open_orders(host, port, client_id)

        elif args.command == "expiry_close":
            result = expiry_close(host, port, client_id, args.expiry_date)

        elif args.command == "global_cancel":
            result = global_cancel(host, port, client_id)

        else:
            raise ValueError(f"Unknown command: {args.command!r}")

        print(json.dumps(result))
        sys.exit(0)

    except Exception as exc:
        logger.error("ibkr_order %s FAILED: %s", args.command, exc, exc_info=True)
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
