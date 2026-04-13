"""
Gate test: No execution stubs in production Go files.

Scans Go source files for the known stub patterns that were present in the
old ibkr_client.go before Phase 4:
  - 'Handshake Successful' print with no real TCP dial
  - 'REAL ORDER EXECUTED' print with no real broker API call
  - 'Connected = true' set without a real net.Dial / ib.connect()

Any match in a non-test .go file causes the test to fail.
"""
import os
import re
import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Forbidden stub patterns (case-sensitive literal strings / regex)
STUB_PATTERNS = [
    r"Handshake Successful",
    r"REAL ORDER EXECUTED",
    r'Connected\s*=\s*true',        # setting Connected without a real socket
]

# Go files that must never contain these patterns.
# Excludes test files (*_test.go) and vendored code.
def _production_go_files():
    engine_dir = os.path.join(_REPO_ROOT, "execution_engine")
    files = []
    for fname in os.listdir(engine_dir):
        if fname.endswith(".go") and not fname.endswith("_test.go"):
            files.append(os.path.join(engine_dir, fname))
    return files


@pytest.mark.parametrize("go_file", _production_go_files())
def test_no_stub_patterns_in_go_file(go_file):
    """Fail if any known execution stub pattern is found in a production Go file."""
    with open(go_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    violations = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue  # skip pure comment lines
        for pattern in STUB_PATTERNS:
            if re.search(pattern, line):
                violations.append((lineno, pattern, line.rstrip()))

    rel = os.path.relpath(go_file, _REPO_ROOT)
    assert violations == [], (
        f"Execution stub pattern(s) found in {rel}:\n"
        + "\n".join(
            f"  line {ln}: [{pat}] → {code}"
            for ln, pat, code in violations
        )
        + "\n\nThese stubs must be removed. "
        "Real IBKR orders go through ingestion/ibkr_order.py subprocess."
    )
