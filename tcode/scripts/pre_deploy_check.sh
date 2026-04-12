#!/usr/bin/env bash
# pre_deploy_check.sh — Run all test layers before deploying.
# Usage: ./scripts/pre_deploy_check.sh
# Exit 0 = all pass, Exit 1 = something failed
set -euo pipefail

FAIL=0
log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "=== Pre-Deploy Check ==="

log "1/4 Go unit tests..."
if (cd execution_engine && go test ./... 2>&1 | tail -5); then
  log "✓ Go tests passed"
else
  log "✗ Go tests FAILED"
  FAIL=1
fi

log "2/4 Python imports..."
if (cd alpha_engine && ./venv/bin/python -c "import publisher, consensus; print('imports ok')" 2>/dev/null); then
  log "✓ Python imports ok"
else
  log "✗ Python imports FAILED"
  FAIL=1
fi

log "3/4 Backtest gate..."
if bash scripts/backtest_gate.sh; then
  log "✓ Backtest gate passed"
else
  EC=$?
  if [ "$EC" -eq 2 ]; then
    log "~ Backtest gate skipped (insufficient data)"
  else
    log "✗ Backtest gate FAILED"
    FAIL=1
  fi
fi

log "4/4 E2E smoke tests..."
if bash scripts/e2e_smoke.sh 2>&1 | tail -5; then
  log "✓ E2E smoke passed"
else
  log "✗ E2E smoke FAILED"
  FAIL=1
fi

if [ "$FAIL" -eq 1 ]; then
  log "=== PRE-DEPLOY: FAILED — do not push ==="
  exit 1
fi
log "=== PRE-DEPLOY: ALL CHECKS PASSED ==="
exit 0
