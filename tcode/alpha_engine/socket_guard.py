"""
socket_guard.py — Phase 21: socket-level network blocking during pause.

When installed, monkeypatches socket.create_connection so that any attempt
to open an outbound TCP connection is blocked unless the destination is on
the whitelist (NATS localhost:4222 or IBKR gateway localhost:4002).

Usage (call once at process startup)::
    from socket_guard import install_socket_guard
    install_socket_guard()

After installation, all subsequent calls to socket.create_connection that
target non-whitelisted hosts will raise PausedNetworkError when is_paused()
returns True.  Whitelisted connections (NATS, IBKR gateway) are always
allowed regardless of pause state, so the heartbeat loop and live-order
management continue to function.

Whitelist entries are (host_lower, port) tuples.  Both "localhost" and
"127.0.0.1" forms are accepted.
"""
from __future__ import annotations

import logging
import socket as _socket_module
from typing import Any

logger = logging.getLogger(__name__)

# ── Whitelist: connections always allowed during pause ─────────────────────────
# localhost:4222 — NATS server (heartbeat, signal emission)
# localhost:4002 — IBKR paper gateway (order management / position queries)
_ALWAYS_ALLOWED: frozenset[tuple[str, int]] = frozenset({
    ("localhost", 4222),
    ("127.0.0.1", 4222),
    ("localhost", 4002),
    ("127.0.0.1", 4002),
})

_original_create_connection = _socket_module.create_connection
_guard_installed: bool = False


class PausedNetworkError(ConnectionRefusedError):
    """Raised when a non-whitelisted connection is attempted while paused."""


def _is_whitelisted(address: tuple[str, int]) -> bool:
    host, port = address[0].lower(), address[1]
    return (host, port) in _ALWAYS_ALLOWED


def _guarded_create_connection(
    address: tuple[str, int],
    timeout: Any = _socket_module._GLOBAL_DEFAULT_TIMEOUT,
    source_address: Any = None,
    *,
    all_errors: bool = False,
) -> _socket_module.socket:
    """Replacement for socket.create_connection that blocks non-whitelisted connections during pause."""
    from pause_guard import is_paused

    if is_paused() and not _is_whitelisted(address):
        host, port = address
        logger.warning(
            "[SOCKET-GUARD] blocked outbound TCP %s:%s (system is paused)",
            host, port,
        )
        raise PausedNetworkError(
            f"[socket_guard] connection to {host}:{port} blocked — system is paused. "
            f"Whitelisted: NATS localhost:4222, IBKR localhost:4002."
        )

    # Pass through to the original implementation.
    # Handle the all_errors kwarg which was added in Python 3.11.
    try:
        return _original_create_connection(
            address, timeout, source_address, all_errors=all_errors
        )
    except TypeError:
        return _original_create_connection(address, timeout, source_address)


def install_socket_guard() -> None:
    """Install the socket-level network guard (idempotent)."""
    global _guard_installed
    if _guard_installed:
        return
    _socket_module.create_connection = _guarded_create_connection  # type: ignore[assignment]
    _guard_installed = True
    logger.info(
        "[SOCKET-GUARD] installed — non-whitelisted connections blocked when paused "
        "(whitelist: NATS localhost:4222, IBKR localhost:4002)"
    )


def uninstall_socket_guard() -> None:
    """Remove the socket guard and restore the original function (for tests)."""
    global _guard_installed
    _socket_module.create_connection = _original_create_connection  # type: ignore[assignment]
    _guard_installed = False
