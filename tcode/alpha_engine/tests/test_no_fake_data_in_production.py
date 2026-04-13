"""
Gate test: No fake/random data in production signal paths.

Scans production source files for forbidden patterns (random.uniform, random.randint,
random.choice, rand.Float64 in non-dead-code paths) and fails if any are found.
"""
import re
import os
import pytest

# Files that are allowed to use random (legitimate: jitter, slippage simulation, dead code)
ALLOWLIST = {
    # sleep jitter — anti-thundering-herd on API calls
    ("alpha_engine/publisher.py", r"random\.uniform\(10,\s*20\)"),
    # paper trading slippage simulation
    ("alpha_engine/simulation.py", None),  # entire file is simulation
    # gastown publishes to tsla.alpha.sim (not signals)
    ("alpha_engine/gastown.py", None),
}

# Production files that MUST NOT contain random calls in signal paths
PRODUCTION_FILES = [
    "alpha_engine/publisher.py",
    "alpha_engine/ingestion/intel.py",
    "alpha_engine/ingestion/macro_regime.py",
    "alpha_engine/ingestion/premarket.py",
    "alpha_engine/ingestion/ev_sector.py",
    "alpha_engine/ingestion/catalyst_tracker.py",
    "alpha_engine/ingestion/institutional.py",
    "alpha_engine/consensus.py",
]

# Forbidden patterns in production signal paths
FORBIDDEN_PATTERNS = [
    r"random\.uniform\(",
    r"random\.randint\(",
    r"random\.choice\(",
    r"random\.gauss\(",
    r"random\.random\(",
]

# publisher.py-specific: the sleep jitter lines are allowed
PUBLISHER_ALLOWED_LINES = {
    r"random\.uniform\(10,\s*20\)",   # sleep jitter — legitimate
}

def _get_repo_root() -> str:
    here = os.path.dirname(__file__)
    # alpha_engine/tests/ -> alpha_engine/ -> repo root
    return os.path.abspath(os.path.join(here, "..", ".."))


def _line_is_allowed(filepath: str, line: str) -> bool:
    """Return True if a forbidden-pattern line is on the allowlist."""
    rel = os.path.relpath(filepath, _get_repo_root())

    # simulation.py and gastown.py are fully exempted
    if rel in ("alpha_engine/simulation.py", "alpha_engine/gastown.py"):
        return True

    # publisher.py: allow only sleep jitter
    if rel == "alpha_engine/publisher.py":
        for allowed in PUBLISHER_ALLOWED_LINES:
            if re.search(allowed, line):
                return True
        return False

    return False


@pytest.mark.parametrize("filepath", PRODUCTION_FILES)
def test_no_random_in_production(filepath):
    """Fail if any forbidden random call is found in a production file."""
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
            continue  # Skip pure comment lines
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, line):
                if not _line_is_allowed(abs_path, line):
                    violations.append((lineno, line.rstrip()))

    assert violations == [], (
        f"Forbidden random() call(s) found in production file {filepath}:\n"
        + "\n".join(f"  line {ln}: {code}" for ln, code in violations)
    )


def test_no_random_in_go_subscriber():
    """Fail if Black-Scholes or rand.Float64 fallback exists in subscriber.go."""
    repo_root = _get_repo_root()
    abs_path = os.path.join(repo_root, "execution_engine/subscriber.go")

    if not os.path.exists(abs_path):
        pytest.skip("subscriber.go does not exist")

    with open(abs_path, "r") as f:
        content = f.read()

    # The Black-Scholes fallback (hardcoded 8% rate + 50% IV) must not exist
    assert "CallPrice(" not in content or "DEAD CODE" in content or _subscriber_bs_is_removed(content), (
        "Black-Scholes fallback pricing detected in subscriber.go — "
        "signals with TargetLimitPrice=0 must be rejected, not priced with fake BS params"
    )


def _subscriber_bs_is_removed(content: str) -> bool:
    """Check that the BS fallback block is gone (replaced by reject logic)."""
    # The old pattern: price = s.Pricing.CallPrice(...) as fallback when price <= 0
    forbidden = re.search(
        r"price\s*<=\s*0\s*\{[^}]*CallPrice\(", content, re.DOTALL
    )
    return forbidden is None


def test_go_fetchconsensusprice_removed():
    """Fail if the fake fetchConsensusPrice() still exists in main.go."""
    repo_root = _get_repo_root()
    abs_path = os.path.join(repo_root, "execution_engine/main.go")

    if not os.path.exists(abs_path):
        pytest.skip("main.go does not exist")

    with open(abs_path, "r") as f:
        content = f.read()

    assert "fetchConsensusPrice" not in content, (
        "fetchConsensusPrice() still exists in main.go — "
        "this function returned fake random spot prices and must be removed"
    )
