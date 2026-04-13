#!/usr/bin/env bash
# integrity_gate.sh — All integrity checks that must pass before deployment.
#
# Tests included:
#   1. No fake/random data in production signal paths (test_no_fake_data_in_production.py)
#   2. Signal independence (test_signal_independence.py)
#   3. No execution stubs in production Go files (test_stub_execution_grep.py) [Phase 4]
#   4. Round-trip broker reflection (test_ibkr_order_roundtrip.py) [Phase 4, integration]
#
# Usage:
#   ./scripts/integrity_gate.sh
#
# Environment:
#   IBKR_GATEWAY_RUNNING=1  — enable the round-trip broker test (requires live IB Gateway)
#
# Exit: 0 = all required tests passed, 1 = failure

set -euo pipefail

FAIL=0
SKIP_ROUND_TRIP=0
log() { echo "[$(date '+%H:%M:%S')] $*"; }

cd "$(dirname "$0")/.."

log "=== INTEGRITY GATE ==="

# ── 1. Fake-data grep test ────────────────────────────────────────────────────
log "1/4 Fake-data check (no random/fake data in production signal paths)..."
if (cd alpha_engine && ./venv/bin/python -m pytest tests/test_no_fake_data_in_production.py -q 2>&1 | tail -5); then
  log "✓ Fake-data check passed"
else
  log "✗ Fake-data check FAILED"
  FAIL=1
fi

# ── 2. Signal independence test ───────────────────────────────────────────────
log "2/4 Signal independence check..."
if (cd alpha_engine && ./venv/bin/python -m pytest tests/test_signal_independence.py -q 2>&1 | tail -5); then
  log "✓ Signal independence check passed"
else
  log "✗ Signal independence check FAILED"
  FAIL=1
fi

# ── 3. Stub execution grep test ───────────────────────────────────────────────
log "3/4 Execution stub grep (no Handshake Successful / REAL ORDER EXECUTED in Go files)..."
if (cd alpha_engine && ./venv/bin/python -m pytest tests/test_stub_execution_grep.py -q 2>&1 | tail -5); then
  log "✓ Execution stub grep passed"
else
  log "✗ Execution stub grep FAILED"
  FAIL=1
fi

# ── 4. Round-trip broker reflection (integration, requires IB Gateway) ────────
log "4/4 Round-trip broker reflection test..."
if [ "${IBKR_GATEWAY_RUNNING:-0}" = "1" ]; then
  if (cd alpha_engine && IBKR_GATEWAY_RUNNING=1 EXECUTION_MODE=IBKR_PAPER \
      ./venv/bin/python -m pytest tests/test_ibkr_order_roundtrip.py -q 2>&1 | tail -10); then
    log "✓ Round-trip broker reflection passed"
  else
    log "✗ Round-trip broker reflection FAILED"
    FAIL=1
  fi
else
  log "~ Round-trip broker reflection SKIPPED (set IBKR_GATEWAY_RUNNING=1 to enable)"
  SKIP_ROUND_TRIP=1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [ "$FAIL" -eq 1 ]; then
  log "=== INTEGRITY GATE: FAILED — do not push ==="
  exit 1
fi

if [ "$SKIP_ROUND_TRIP" -eq 1 ]; then
  log "=== INTEGRITY GATE: PASSED (round-trip test skipped — IB Gateway not running) ==="
else
  log "=== INTEGRITY GATE: ALL CHECKS PASSED ==="
fi
exit 0
