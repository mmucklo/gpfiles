"""
Gate test: gastown isolation from the live publisher.

Verifies that publisher.py never subscribes to 'tsla.alpha.sim' (the gastown
simulation channel) and that gastown.py only publishes to that channel, never
to the live 'tsla.alpha.signals' subject.

This is the FIX 1 isolation assertion mandated by Phase 2 stabilisation.
"""
import os
import pytest


def _get_repo_root() -> str:
    here = os.path.dirname(__file__)
    return os.path.abspath(os.path.join(here, "..", ".."))


def test_publisher_never_subscribes_to_sim_channel():
    """publisher.py must not read from tsla.alpha.sim — the gastown sim channel.

    Subscribing to sim data would create a feedback loop: gastown P&L (driven
    by paper outcomes) → publisher → live signals → execution.
    """
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "alpha_engine", "publisher.py")

    if not os.path.exists(path):
        pytest.skip("publisher.py does not exist")

    with open(path) as fh:
        content = fh.read()

    assert "tsla.alpha.sim" not in content, (
        "publisher.py references 'tsla.alpha.sim'.\n"
        "The publisher must never subscribe to the gastown simulation channel "
        "to prevent feedback loops between paper P&L and live signal generation."
    )


def test_gastown_never_publishes_to_live_channel():
    """gastown.py must only publish to tsla.alpha.sim, never to tsla.alpha.signals.

    Publishing to the live channel would inject fake sim signals into the real
    execution engine.
    """
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "alpha_engine", "gastown.py")

    if not os.path.exists(path):
        pytest.skip("gastown.py does not exist")

    with open(path) as fh:
        content = fh.read()

    assert "tsla.alpha.signals" not in content, (
        "gastown.py references 'tsla.alpha.signals'.\n"
        "gastown must only publish to 'tsla.alpha.sim' (simulation) — "
        "never to the live signal channel consumed by the Go execution engine."
    )


def test_publisher_imports_no_gastown_or_refinery():
    """publisher.py must not import gastown or refinery modules."""
    repo_root = _get_repo_root()
    path = os.path.join(repo_root, "alpha_engine", "publisher.py")

    if not os.path.exists(path):
        pytest.skip("publisher.py does not exist")

    with open(path) as fh:
        content = fh.read()

    for forbidden in ("import gastown", "from gastown", "import refinery", "from refinery"):
        assert forbidden not in content, (
            f"publisher.py contains '{forbidden}'.\n"
            "The publisher must be isolated from the gastown/refinery simulation loop."
        )
