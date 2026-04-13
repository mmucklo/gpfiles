"""
Gate test: Signal models do NOT read from fills, portfolio, closed_trades, or simulation P&L.

Verifies that no feedback loop exists between paper trading outcomes and live signal generation.
"""
import ast
import os
import pytest

# Signal model files — these must not import or read from execution/fill data
SIGNAL_MODEL_FILES = [
    "alpha_engine/publisher.py",
    "alpha_engine/consensus.py",
    "alpha_engine/ingestion/intel.py",
    "alpha_engine/ingestion/macro_regime.py",
    "alpha_engine/ingestion/premarket.py",
    "alpha_engine/ingestion/ev_sector.py",
    "alpha_engine/ingestion/catalyst_tracker.py",
    "alpha_engine/ingestion/institutional.py",
]

# Symbols/strings that would indicate a signal model is reading from execution outputs
FORBIDDEN_IMPORTS = [
    "fills",
    "closed_trades",
    "simulation",
    "PaperPortfolio",
    "IBKRExecutor",
    "gastown",
    "refinery",
]

# String patterns forbidden in signal model source (reading fill/portfolio state)
FORBIDDEN_STRINGS = [
    "closed_trades",
    "fills_audit",
    "Portfolio.NAV",
    "Portfolio.Cash",
    "unrealized_pnl",
    "realized_pnl",
    "sim_state",
    "GlobalSimState",
    "UpdateSimState",
    "tsla.alpha.sim",       # gastown sim channel — signals must not subscribe
]


def _get_repo_root() -> str:
    here = os.path.dirname(__file__)
    return os.path.abspath(os.path.join(here, "..", ".."))


def _get_imports(source: str) -> list[str]:
    """Extract all imported module names from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


@pytest.mark.parametrize("filepath", SIGNAL_MODEL_FILES)
def test_no_fill_imports(filepath):
    """Signal model must not import from fills/portfolio/simulation modules."""
    repo_root = _get_repo_root()
    abs_path = os.path.join(repo_root, filepath)

    if not os.path.exists(abs_path):
        pytest.skip(f"{filepath} does not exist")

    with open(abs_path, "r") as f:
        source = f.read()

    imports = _get_imports(source)
    bad_imports = [
        imp for imp in imports
        if any(forbidden in imp for forbidden in FORBIDDEN_IMPORTS)
    ]

    assert bad_imports == [], (
        f"Signal model {filepath} imports execution/fill modules: {bad_imports}\n"
        "Signal models must be isolated from paper trading outcomes to prevent feedback loops."
    )


@pytest.mark.parametrize("filepath", SIGNAL_MODEL_FILES)
def test_no_fill_string_references(filepath):
    """Signal model source must not reference fill/portfolio state strings."""
    repo_root = _get_repo_root()
    abs_path = os.path.join(repo_root, filepath)

    if not os.path.exists(abs_path):
        pytest.skip(f"{filepath} does not exist")

    with open(abs_path, "r") as f:
        lines = f.readlines()

    violations = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for forbidden in FORBIDDEN_STRINGS:
            if forbidden in line:
                violations.append((lineno, forbidden, line.rstrip()))

    assert violations == [], (
        f"Signal model {filepath} references execution/fill state:\n"
        + "\n".join(
            f"  line {ln}: [{pattern}] {code}"
            for ln, pattern, code in violations
        )
        + "\nSignal models must not read fill/portfolio/sim data (no feedback loops)."
    )


def test_publisher_does_not_subscribe_to_sim_channel():
    """publisher.py must not subscribe to tsla.alpha.sim (the gastown simulation channel)."""
    repo_root = _get_repo_root()
    abs_path = os.path.join(repo_root, "alpha_engine/publisher.py")

    if not os.path.exists(abs_path):
        pytest.skip("publisher.py does not exist")

    with open(abs_path, "r") as f:
        content = f.read()

    assert "tsla.alpha.sim" not in content, (
        "publisher.py references tsla.alpha.sim — "
        "the publisher must not read from the gastown simulation channel"
    )


def test_gastown_publishes_only_to_sim():
    """gastown.py must only publish to tsla.alpha.sim, never to tsla.alpha.signals."""
    repo_root = _get_repo_root()
    abs_path = os.path.join(repo_root, "alpha_engine/gastown.py")

    if not os.path.exists(abs_path):
        pytest.skip("gastown.py does not exist")

    with open(abs_path, "r") as f:
        content = f.read()

    assert "tsla.alpha.signals" not in content, (
        "gastown.py publishes to tsla.alpha.signals — "
        "gastown must only publish to tsla.alpha.sim (simulation channel)"
    )
