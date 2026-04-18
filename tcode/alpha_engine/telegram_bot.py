"""
Phase 17 — Telegram Bot for mobile trade approval.

Setup:
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your personal chat ID

Features:
  A. Trade proposals — inline keyboard: ✅ Execute | ❌ Skip | 🔧 Adjust
  B. Trade exits — P&L notification
  C. Alerts — regime shift, circuit breaker, daily target
  D. Commands — /status /pause /unpause /positions

Bot always runs (even when publisher is paused) so user can unpause via Telegram.
Proposals require explicit inline button tap — NEVER auto-execute.

Implementation: long-polling via requests (no library dependency on python-telegram-bot).
Runs as a background thread started from publisher or standalone process.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("TelegramBot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
_POLL_INTERVAL_S   = 2   # long-polling timeout
_API_BASE          = "https://api.telegram.org/bot{token}/{method}"

_last_update_id: int = 0
_bot_thread: threading.Thread | None = None
_bot_running: bool = False


# ── Telegram HTTP helpers ─────────────────────────────────────────────────────

def _api_url(method: str) -> str:
    return _API_BASE.format(token=TELEGRAM_BOT_TOKEN, method=method)


def _post(method: str, payload: dict, timeout: int = 10) -> dict | None:
    if not TELEGRAM_BOT_TOKEN:
        logger.debug("Telegram: no token configured — stubbing %s", method)
        return None
    try:
        resp = requests.post(_api_url(method), json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Telegram %s failed: %s", method, exc)
        return None


def _get(method: str, params: dict | None = None, timeout: int = 30) -> dict | None:
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        resp = requests.get(_api_url(method), params=params or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Telegram %s failed: %s", method, exc)
        return None


# ── Outbound messages ─────────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "Markdown") -> None:
    """Send a plain text message to the configured chat."""
    if not TELEGRAM_CHAT_ID:
        logger.info("Telegram stub: %s", text[:80])
        return
    _post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    })


def send_proposal(proposal: dict) -> None:
    """Send a trade proposal with Execute / Skip / Adjust inline keyboard.

    proposal dict must have: id, strategy, direction, entry_price, stop_price,
    target_price, quantity, confidence, legs (JSON array)
    """
    proposal_id = proposal.get("id", "")
    strategy    = proposal.get("strategy", "UNKNOWN")
    direction   = proposal.get("direction", "UNKNOWN")
    entry       = proposal.get("entry_price", 0)
    stop_p      = proposal.get("stop_price", 0)
    target_p    = proposal.get("target_price", 0)
    qty         = proposal.get("quantity", 1)
    confidence  = proposal.get("confidence", 0)

    try:
        legs = json.loads(proposal.get("legs", "[]")) if isinstance(proposal.get("legs"), str) else (proposal.get("legs") or [])
    except Exception:
        legs = []

    # Build contract description from first leg
    leg_desc = "TSLA"
    if legs:
        leg = legs[0]
        leg_desc = f"TSLA ${leg.get('strike', '')} {leg.get('type', 'CALL')}"

    risk_dollars = round(entry * qty * 100, 0)
    text = (
        f"📊 *TRADE PROPOSAL*\n"
        f"{leg_desc} 0DTE\n"
        f"Direction: *{direction}* ({confidence:.0%})\n"
        f"Entry: `${entry:.2f}` | Stop: `${stop_p:.2f}` | Target: `${target_p:.2f}`\n"
        f"Kelly: {qty} contracts (${risk_dollars:,.0f} risk)\n"
        f"Strategy: *{strategy}*"
    )

    inline_keyboard = [[
        {"text": "✅ Execute", "callback_data": f"execute:{proposal_id}"},
        {"text": "❌ Skip",    "callback_data": f"skip:{proposal_id}"},
        {"text": "🔧 Adjust",  "callback_data": f"adjust:{proposal_id}"},
    ]]

    if not TELEGRAM_CHAT_ID:
        logger.info("Telegram stub proposal: %s", text)
        return

    _post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": inline_keyboard},
    })


def send_exit_notification(
    trade_id: int,
    contract: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    stop_type: str,
    hold_sec: int,
    daily_pnl: float,
) -> None:
    """Send exit notification after a position closes."""
    pnl = (exit_price - entry_price) * quantity * 100
    pnl_pct = (exit_price - entry_price) / entry_price * 100 if entry_price else 0
    is_winner = pnl > 0
    icon = "✅" if is_winner else "❌"
    label = "Winner" if is_winner else "Loser"
    hold_min = hold_sec // 60
    hold_s   = hold_sec % 60

    _STOP_TYPE_LABELS = {
        "TP": "Take-profit", "SL": "Stop-loss",
        "TRAILING": "Trailing stop", "TIME_STOP": "Time stop", "MANUAL": "Manual close"
    }
    exit_reason = _STOP_TYPE_LABELS.get(stop_type, stop_type)

    daily_target = float(os.getenv("DAILY_TARGET", "10000"))
    text = (
        f"{icon} *TRADE CLOSED — {label}*\n"
        f"{contract} {'+' if pnl > 0 else ''}${pnl:,.2f} ({'+' if pnl_pct > 0 else ''}{pnl_pct:.1f}%)\n"
        f"Hold: {hold_min}m {hold_s}s | Exit: {exit_reason}\n"
        f"Daily P&L: ${daily_pnl:,.0f} / ${daily_target:,.0f} target"
    )
    send_message(text)


def send_alert(message: str) -> None:
    """Send a system alert (regime shift, circuit breaker, etc.)."""
    send_message(message)


# ── Inbound command handling ──────────────────────────────────────────────────

def _handle_command(text: str, chat_id: str) -> None:
    """Process /status /pause /unpause /positions commands."""
    text = (text or "").strip()
    cmd = text.split()[0].lower().lstrip("/")

    if cmd == "status":
        _cmd_status(chat_id)
    elif cmd == "pause":
        _cmd_pause(chat_id)
    elif cmd.startswith("unpause"):
        parts = text.split()
        minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        _cmd_unpause(chat_id, minutes)
    elif cmd == "positions":
        _cmd_positions(chat_id)
    else:
        _post("sendMessage", {
            "chat_id": chat_id,
            "text": "Commands: /status /pause /unpause [minutes] /positions",
        })


def _cmd_status(chat_id: str) -> None:
    try:
        import requests as _req
        from circuit_breaker import compute_daily_stats
        stats = compute_daily_stats()
        from ingestion.realtime_bars import get_atr
        atr = get_atr()

        try:
            resp = _req.get("http://localhost:2112/api/regime/current", timeout=3)
            regime_data = resp.json() if resp.ok else {}
        except Exception:
            regime_data = {}

        regime = regime_data.get("regime", "UNKNOWN")
        pnl = stats["daily_pnl"]
        daily_target = float(os.getenv("DAILY_TARGET", "10000"))

        text = (
            f"📈 *Status*\n"
            f"Regime: *{regime}*\n"
            f"ATR: `{atr:.4f}`\n"
            f"Daily P&L: ${pnl:,.0f} / ${daily_target:,.0f}\n"
            f"Trades today: {stats['total_trades']} ({stats['winners']}W / {stats['losers']}L)\n"
            f"Consecutive losses: {stats['consecutive_losses']}"
        )
    except Exception as exc:
        text = f"Status error: {exc}"
    _post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})


def _cmd_pause(chat_id: str) -> None:
    try:
        import requests as _req
        resp = _req.post("http://localhost:2112/api/system/pause", timeout=3)
        if resp.ok:
            _post("sendMessage", {"chat_id": chat_id, "text": "⏸ Publisher paused."})
        else:
            _post("sendMessage", {"chat_id": chat_id, "text": f"Pause failed: {resp.status_code}"})
    except Exception as exc:
        _post("sendMessage", {"chat_id": chat_id, "text": f"Pause error: {exc}"})


def _cmd_unpause(chat_id: str, minutes: int) -> None:
    try:
        import requests as _req
        resp = _req.post("http://localhost:2112/api/system/unpause",
                         json={"minutes": minutes}, timeout=3)
        if resp.ok:
            _post("sendMessage", {"chat_id": chat_id, "text": f"▶️ Unpaused for {minutes} min."})
        else:
            _post("sendMessage", {"chat_id": chat_id, "text": f"Unpause failed: {resp.status_code}"})
    except Exception as exc:
        _post("sendMessage", {"chat_id": chat_id, "text": f"Unpause error: {exc}"})


def _cmd_positions(chat_id: str) -> None:
    try:
        from stop_manager import get_open_positions
        from ingestion.realtime_bars import get_latest_close
        positions = get_open_positions()
        if not positions:
            _post("sendMessage", {"chat_id": chat_id, "text": "No open positions."})
            return
        current_price = get_latest_close()
        lines = ["📋 *Open Positions*"]
        for p in positions:
            entry  = p["entry_price"]
            pnl_per = (current_price - entry) if p["direction"] == "LONG" else (entry - current_price)
            pnl_d  = pnl_per * p["quantity"] * 100
            mins   = p["remaining_sec"] // 60
            lines.append(
                f"ID {p['trade_id']} {p['strategy']} {p['direction']}\n"
                f"  Entry ${entry:.2f} | Current ${current_price:.2f}\n"
                f"  P&L: ${pnl_d:+,.2f} | Closes in {mins}m"
            )
        _post("sendMessage", {"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"})
    except Exception as exc:
        _post("sendMessage", {"chat_id": chat_id, "text": f"Positions error: {exc}"})


def _handle_callback_query(query: dict) -> None:
    """Handle inline keyboard button taps."""
    query_id    = query.get("id", "")
    data        = query.get("data", "")
    chat_id     = str(query.get("message", {}).get("chat", {}).get("id", ""))

    # Acknowledge the callback immediately
    _post("answerCallbackQuery", {"callback_query_id": query_id})

    if not data:
        return

    parts = data.split(":", 1)
    if len(parts) != 2:
        return
    action, proposal_id = parts

    if action == "execute":
        _execute_proposal(proposal_id, chat_id)
    elif action == "skip":
        _skip_proposal(proposal_id, chat_id)
    elif action == "adjust":
        _post("sendMessage", {
            "chat_id": chat_id,
            "text": f"🔧 To adjust proposal `{proposal_id[:8]}…`, use the dashboard.",
            "parse_mode": "Markdown",
        })


def _execute_proposal(proposal_id: str, chat_id: str) -> None:
    import requests as _req
    try:
        resp = _req.post(
            f"http://localhost:2112/api/trades/proposed/{proposal_id}/execute",
            timeout=5,
        )
        if resp.ok:
            _post("sendMessage", {"chat_id": chat_id, "text": f"✅ Executing proposal `{proposal_id[:8]}…`", "parse_mode": "Markdown"})
        else:
            _post("sendMessage", {"chat_id": chat_id, "text": f"Execute failed ({resp.status_code}): {resp.text[:200]}"})
    except Exception as exc:
        _post("sendMessage", {"chat_id": chat_id, "text": f"Execute error: {exc}"})


def _skip_proposal(proposal_id: str, chat_id: str) -> None:
    import requests as _req
    try:
        resp = _req.post(
            f"http://localhost:2112/api/trades/proposed/{proposal_id}/skip",
            timeout=5,
        )
        if resp.ok:
            _post("sendMessage", {"chat_id": chat_id, "text": f"❌ Skipped proposal `{proposal_id[:8]}…`", "parse_mode": "Markdown"})
        else:
            _post("sendMessage", {"chat_id": chat_id, "text": f"Skip failed ({resp.status_code})"})
    except Exception as exc:
        _post("sendMessage", {"chat_id": chat_id, "text": f"Skip error: {exc}"})


# ── Polling loop ─────────────────────────────────────────────────────────────

def _poll_loop() -> None:
    global _last_update_id, _bot_running
    logger.info("TelegramBot: poll loop started (chat_id=%s)", TELEGRAM_CHAT_ID or "not set")
    while _bot_running:
        try:
            result = _get("getUpdates", {
                "offset": _last_update_id + 1,
                "timeout": _POLL_INTERVAL_S,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=_POLL_INTERVAL_S + 5)
            if result and result.get("ok"):
                for update in result.get("result", []):
                    _last_update_id = max(_last_update_id, update.get("update_id", 0))
                    if "message" in update:
                        msg = update["message"]
                        text = msg.get("text", "")
                        chat_id = str(msg.get("chat", {}).get("id", ""))
                        if text.startswith("/") and chat_id == TELEGRAM_CHAT_ID:
                            _handle_command(text, chat_id)
                    elif "callback_query" in update:
                        _handle_callback_query(update["callback_query"])
        except Exception as exc:
            logger.debug("TelegramBot: poll error: %s", exc)
            time.sleep(2)


def start() -> None:
    """Start bot polling in a background thread."""
    global _bot_thread, _bot_running
    if not TELEGRAM_BOT_TOKEN:
        logger.info("TelegramBot: TELEGRAM_BOT_TOKEN not set — running in stub mode (no polling)")
        return
    if _bot_thread is not None and _bot_thread.is_alive():
        return
    _bot_running = True
    _bot_thread = threading.Thread(target=_poll_loop, daemon=True, name="telegram-bot-poll")
    _bot_thread.start()
    logger.info("TelegramBot: background poll thread started")


def stop() -> None:
    """Stop the polling thread."""
    global _bot_running
    _bot_running = False
