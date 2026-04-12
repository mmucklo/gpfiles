#!/usr/bin/env bash
# backtest_gate.sh — Block deploy if new strategy degrades Sharpe ratio vs baseline.
# Usage: ./scripts/backtest_gate.sh [--min-sharpe 0.5] [--max-drawdown 0.15]
# Exit 0 = pass, Exit 1 = fail (degraded), Exit 2 = insufficient data
set -euo pipefail

MIN_SHARPE="${1:-0.5}"
MAX_DRAWDOWN="${2:-0.15}"
DB="$HOME/tsla_alpha.db"

if [ ! -f "$DB" ]; then
  echo "WARN: No SQLite DB at $DB — insufficient data, skipping gate (exit 2)"
  exit 2
fi

# Count fills to determine if we have enough data
FILL_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM fills;" 2>/dev/null || echo "0")
if [ "$FILL_COUNT" -lt 5 ]; then
  echo "WARN: Only $FILL_COUNT fills in DB — insufficient data for backtest gate (exit 2)"
  exit 2
fi

echo "Running replay engine backtest..."
RESULT=$(cd ~/src/gemini && alpha_engine/venv/bin/python alpha_engine/replay.py --strategy current 2>/dev/null || echo '{"sharpe": 0, "max_drawdown": 1}')

SHARPE=$(echo "$RESULT" | alpha_engine/venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print(d.get('sharpe', 0))" 2>/dev/null || echo "0")
DRAWDOWN=$(echo "$RESULT" | alpha_engine/venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print(abs(d.get('max_drawdown', 1)))" 2>/dev/null || echo "1")

echo "Backtest results: Sharpe=$SHARPE MaxDrawdown=$DRAWDOWN"
echo "Thresholds: MinSharpe=$MIN_SHARPE MaxDrawdown=$MAX_DRAWDOWN"

FAIL=0
if alpha_engine/venv/bin/python -c "exit(0 if float('$SHARPE') >= float('$MIN_SHARPE') else 1)" 2>/dev/null; then
  echo "✓ Sharpe $SHARPE >= $MIN_SHARPE"
else
  echo "✗ Sharpe $SHARPE < $MIN_SHARPE — GATE FAIL"
  FAIL=1
fi

if alpha_engine/venv/bin/python -c "exit(0 if float('$DRAWDOWN') <= float('$MAX_DRAWDOWN') else 1)" 2>/dev/null; then
  echo "✓ MaxDrawdown $DRAWDOWN <= $MAX_DRAWDOWN"
else
  echo "✗ MaxDrawdown $DRAWDOWN > $MAX_DRAWDOWN — GATE FAIL"
  FAIL=1
fi

if [ "$FAIL" -eq 1 ]; then
  echo "BACKTEST GATE: FAILED — deploy blocked"
  exit 1
fi
echo "BACKTEST GATE: PASSED"
exit 0
