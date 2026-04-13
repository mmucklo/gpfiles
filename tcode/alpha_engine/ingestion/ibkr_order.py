"""
TSLA Alpha Engine: IBKR Order Placement
Places, cancels, and queries option orders via IB Gateway using ib_insync.

CLI interface (for Go subprocess calls):
  python -m ingestion.ibkr_order place --symbol TSLA --contract CALL --strike 365 \
      --expiry 2026-04-13 --action BUY --quantity 10 --limit-price 0.28 \
      --tif DAY --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order cancel --order-id 12345 --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order status --order-id 12345 --mode IBKR_PAPER --client-id 3
  python -m ingestion.ibkr_order open_orders --mode IBKR_PAPER --client-id 3

Output: JSON to stdout only. Errors go to stderr. Exit 0 on success, non-zero on failure.
Logs: alpha_engine/ibkr_order.log (full request/response audit trail).

Safety:
  - Only IBKR_PAPER mode is enabled; IBKR_LIVE is blocked.
  - Never subscribes to or publishes on tsla.alpha.sim.
  - Each invocation uses a unique --client-id supplied by the Go engine.
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

# ── logging setup ─────────────────────────────────────────────────────────────
# Log to alpha_engine/ibkr_order.log (one level up from ingestion/).
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

# stderr gets WARNING+; stdout is strictly reserved for JSON output.
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
logger.addHandler(_stderr_handler)

# ── constants ─────────────────────────────────────────────────────────────────
CONNECT_TIMEOUT = int(os.getenv("IBKR_CONNECT_TIMEOUT", "10"))
ORDER_WAIT_SEC = 2.0   # seconds to wait for initial broker ack after placeOrder


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
    """
    Connect to IB Gateway/TWS and return the IB instance.
    Uses patchAsyncio() in case we are running inside an existing event loop.
    """
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
    Place a limit order for an options contract and return the initial status.

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

        # Wait for broker acknowledgement
        ib.sleep(ORDER_WAIT_SEC)

        order_id = trade.order.orderId
        status = trade.orderStatus.status or "Submitted"
        filled_qty = float(trade.orderStatus.filled or 0)
        avg_fill = float(trade.orderStatus.avgFillPrice or 0)
        contract_id = contract.conId or 0

        result = {
            "orderId":       order_id,
            "status":        status,
            "filled_qty":    filled_qty,
            "avg_fill_price": avg_fill,
            "timestamp":     _now_iso(),
            "contract_id":   contract_id,
        }
        logger.info("place result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def cancel_order(host: str, port: int, client_id: int, order_id: int) -> dict:
    """Cancel an open order by order ID."""
    logger.info("cancel: orderId=%d clientId=%d", order_id, client_id)

    ib = _connect(host, port, client_id)
    try:
        # Request all open orders so the trade object is populated
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
    """Return the current status of an order by order ID."""
    logger.info("status: orderId=%d clientId=%d", order_id, client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        open_trades = ib.trades()
        trade = next((t for t in open_trades if t.order.orderId == order_id), None)

        if trade is None:
            result = {
                "orderId":       order_id,
                "status":        "Unknown",
                "filled_qty":    0,
                "avg_fill_price": 0,
                "timestamp":     _now_iso(),
            }
        else:
            result = {
                "orderId":       order_id,
                "status":        trade.orderStatus.status or "Unknown",
                "filled_qty":    float(trade.orderStatus.filled or 0),
                "avg_fill_price": float(trade.orderStatus.avgFillPrice or 0),
                "timestamp":     _now_iso(),
            }

        logger.info("status result: %s", json.dumps(result))
        return result
    finally:
        _disconnect(ib)


def get_open_orders(host: str, port: int, client_id: int) -> dict:
    """Return the list of currently open orders from IBKR."""
    logger.info("open_orders: clientId=%d", client_id)

    ib = _connect(host, port, client_id)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)

        orders = []
        for trade in ib.trades():
            contract = trade.contract
            order    = trade.order
            # Parse option contract fields
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
            orders.append({
                "orderId":        trade.order.orderId,
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
                "timestamp":      _now_iso(),
            })

        result = {"orders": orders}
        logger.info("open_orders: %d open orders", len(orders))
        return result
    finally:
        _disconnect(ib)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="IBKR order management via ib_insync"
    )
    parser.add_argument("command", choices=["place", "cancel", "status", "open_orders"])
    parser.add_argument("--symbol",      default="TSLA")
    parser.add_argument("--contract",    default="CALL", help="CALL or PUT")
    parser.add_argument("--strike",      type=float, default=0.0)
    parser.add_argument("--expiry",      default="",  help="YYYY-MM-DD")
    parser.add_argument("--action",      default="BUY", help="BUY or SELL")
    parser.add_argument("--quantity",    type=int, default=1)
    parser.add_argument("--limit-price", type=float, default=0.0, dest="limit_price")
    parser.add_argument("--tif",         default="DAY")
    parser.add_argument("--order-id",    type=int, default=0, dest="order_id")
    parser.add_argument("--mode",        default="IBKR_PAPER",
                        help="IBKR_PAPER only (IBKR_LIVE is blocked)")
    parser.add_argument("--client-id",   type=int, default=3, dest="client_id")
    args = parser.parse_args()

    # Safety gate: refuse to execute unless mode is an IBKR paper mode.
    if args.mode not in ("IBKR_PAPER", "IBKR_LIVE"):
        print(json.dumps({
            "error": f"Refusing order: mode={args.mode!r} is not a real IBKR mode"
        }))
        sys.exit(1)

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
        elif args.command == "status":
            if args.order_id <= 0:
                raise ValueError("--order-id is required and must be > 0")
            result = get_status(host, port, client_id, args.order_id)
        elif args.command == "open_orders":
            result = get_open_orders(host, port, client_id)
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
