"""
test_pause_guard_coverage.py — Phase 21: AST-based contract test.

Asserts that every ingestion module that makes external HTTP/socket calls
has at least one @_pause_guard (or @pause_guard) decorator applied to a
public entry-point function.

Uses Python's ast module to parse source without executing it, so no
network calls or environment setup required.
"""
from __future__ import annotations

import ast
import os
import textwrap
from pathlib import Path

import pytest

INGESTION_DIR = Path(__file__).parent.parent / "ingestion"

# Map: filename → expected decorated function names (at least one must be decorated).
# A module passes if ANY function in its required set is decorated.
REQUIRED_GUARDS: dict[str, list[str]] = {
    "catalyst_tracker.py": ["get_catalyst_intel"],
    "ev_sector.py":        ["get_ev_sector_intel"],
    "institutional.py":    ["get_institutional_intel"],
    "macro_regime.py":     ["get_macro_regime"],
    "intel.py":            ["get_intel"],
    "tradier_chain.py":    ["get_expirations", "get_chain", "get_quotes"],
    "premarket.py":        ["_fetch_premarket"],
    "congress_trades.py":  ["_fetch_senate_ptrs", "_fetch_house_ptrs"],
}


def _collect_decorated_functions(source: str, decorator_names: set[str]) -> set[str]:
    """Return names of functions decorated with any of decorator_names."""
    tree = ast.parse(source)
    decorated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # @_pause_guard  or  @pause_guard
            if isinstance(dec, ast.Name) and dec.id in decorator_names:
                decorated.add(node.name)
            # @module._pause_guard
            elif isinstance(dec, ast.Attribute) and dec.attr in decorator_names:
                decorated.add(node.name)
    return decorated


GUARD_NAMES = {"_pause_guard", "pause_guard"}


@pytest.mark.parametrize("filename,required_fns", REQUIRED_GUARDS.items())
def test_module_has_pause_guard(filename: str, required_fns: list[str]) -> None:
    """At least one function in required_fns must be decorated with @_pause_guard."""
    path = INGESTION_DIR / filename
    assert path.exists(), f"Expected ingestion module {filename} not found at {path}"

    source = path.read_text(encoding="utf-8")
    decorated = _collect_decorated_functions(source, GUARD_NAMES)

    decorated_required = [fn for fn in required_fns if fn in decorated]
    assert decorated_required, (
        f"{filename}: none of {required_fns} is decorated with @_pause_guard or @pause_guard.\n"
        f"Functions decorated in this file: {sorted(decorated) or '(none)'}\n"
        f"Add @_pause_guard to at least one of {required_fns} to satisfy the Phase 21 "
        f"pause-silence contract."
    )


def test_pause_guard_module_exists() -> None:
    """pause_guard.py must exist at alpha_engine root."""
    pg = Path(__file__).parent.parent / "pause_guard.py"
    assert pg.exists(), (
        "pause_guard.py not found at alpha_engine/. "
        "Create it with is_paused(), @pause_guard, and @pause_guard_async."
    )


def test_pause_guard_has_required_api() -> None:
    """pause_guard.py must export is_paused, pause_guard, pause_guard_async."""
    pg = Path(__file__).parent.parent / "pause_guard.py"
    source = pg.read_text(encoding="utf-8")
    tree = ast.parse(source)

    top_level_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level_names.add(node.name)

    required = {"is_paused", "pause_guard", "pause_guard_async", "_record_blocked_call"}
    missing = required - top_level_names
    assert not missing, (
        f"pause_guard.py is missing required symbols: {missing}. "
        f"Found: {sorted(top_level_names)}"
    )


def test_socket_guard_module_exists() -> None:
    """socket_guard.py must exist at alpha_engine root."""
    sg = Path(__file__).parent.parent / "socket_guard.py"
    assert sg.exists(), (
        "socket_guard.py not found at alpha_engine/. "
        "Create it with install_socket_guard() and a localhost whitelist."
    )


def test_socket_guard_has_required_api() -> None:
    """socket_guard.py must export install_socket_guard and uninstall_socket_guard."""
    sg = Path(__file__).parent.parent / "socket_guard.py"
    source = sg.read_text(encoding="utf-8")
    tree = ast.parse(source)

    top_level_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level_names.add(node.name)

    required = {"install_socket_guard", "uninstall_socket_guard"}
    missing = required - top_level_names
    assert not missing, (
        f"socket_guard.py is missing required symbols: {missing}. "
        f"Found: {sorted(top_level_names)}"
    )
