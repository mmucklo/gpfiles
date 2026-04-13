"""
Gate test: No random slippage in production fill paths.

Verifies that:
1. fill_model.go does NOT import "math/rand" — eliminating random slippage from
   the SIMULATION fill path (fills are now deterministic: mid ± fixed half-spread).
2. The production fill path in Go produces identical results for identical inputs
   (verified by asserting absence of rand.* calls in the source).

In IBKR_PAPER / IBKR_LIVE modes, fills come directly from IBKR — the local
FillModel is only used in SIMULATION mode, and must be deterministic there.
"""
import os
import re
import pytest


def _get_repo_root() -> str:
    here = os.path.dirname(__file__)
    return os.path.abspath(os.path.join(here, "..", ".."))


def test_fill_model_no_math_rand_import():
    """fill_model.go must not import math/rand.

    After FIX 2 the random slippage component was removed.  The only allowed
    fill calculation is: mid ± (spread/2) where spread is a fixed deterministic
    fraction of mid.
    """
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "execution_engine", "fill_model.go")

    if not os.path.exists(path):
        pytest.skip("fill_model.go does not exist")

    with open(path) as fh:
        content = fh.read()

    assert '"math/rand"' not in content, (
        'fill_model.go imports "math/rand".\n'
        "Random slippage must be removed from the SIMULATION fill path. "
        "Fills must be deterministic: mid ± (fixed_spread / 2)."
    )


def test_fill_model_no_rand_calls():
    """fill_model.go must not call rand.Float64() or rand.Intn()."""
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "execution_engine", "fill_model.go")

    if not os.path.exists(path):
        pytest.skip("fill_model.go does not exist")

    with open(path) as fh:
        content = fh.read()

    forbidden_patterns = [
        r"rand\.Float64\(",
        r"rand\.Float32\(",
        r"rand\.Intn\(",
        r"rand\.Int63\(",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, content), (
            f"fill_model.go contains a random call matching '{pattern}'.\n"
            "All random number generation must be removed from the fill path."
        )


def test_main_go_no_rand_seed():
    """main.go must not call rand.Seed() — a signal that rand is no longer used."""
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "execution_engine", "main.go")

    if not os.path.exists(path):
        pytest.skip("main.go does not exist")

    with open(path) as fh:
        content = fh.read()

    assert "rand.Seed(" not in content, (
        "main.go still calls rand.Seed() — math/rand is no longer needed and "
        "should be removed entirely from the production execution path."
    )


def test_fill_model_determinism():
    """Verify the fill model formula is deterministic by inspecting source.

    Checks that CalculateFillPrice uses only fixed arithmetic (no rand.*) so the
    same midPrice always produces the same fill price.
    """
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "execution_engine", "fill_model.go")

    if not os.path.exists(path):
        pytest.skip("fill_model.go does not exist")

    with open(path) as fh:
        content = fh.read()

    # The deterministic formula must contain a fixed spread calculation.
    assert "spread" in content and "midPrice" in content, (
        "fill_model.go does not appear to contain the expected deterministic "
        "spread calculation.  Ensure CalculateFillPrice uses mid ± (spread/2) "
        "with no random component."
    )
    # rand must not appear as a code identifier (not in comments or variable names).
    # Strip comment lines before checking to avoid false positives from doc comments.
    code_lines = [l for l in content.splitlines() if not l.strip().startswith("//")]
    code_only = "\n".join(code_lines)
    assert "rand." not in code_only, (
        "fill_model.go contains 'rand.' in non-comment code.  Remove all rand usage."
    )
