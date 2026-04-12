#!/usr/bin/env bash
# Phase 8: Intel Health Check
# Validates all 10 intel sources, API endpoints, and critical processes.
set -euo pipefail

PASS=0
FAIL=0
WARN=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; WARN=$((WARN+1)); }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ALPHA_ENGINE="$REPO_ROOT/alpha_engine"
VENV_PYTHON="$ALPHA_ENGINE/venv/bin/python"

echo "======================================"
echo "  TSLA Alpha Engine — Intel Health Check"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "======================================"
echo ""

# ── Processes ───────────────────────────────────────────────────────────────
echo "--- Processes ---"

if pgrep -f "publisher.py" > /dev/null 2>&1; then
    pass "publisher.py is running (PID: $(pgrep -f publisher.py | head -1))"
else
    fail "publisher.py is NOT running"
fi

if pgrep -f "execution_engine" > /dev/null 2>&1; then
    pass "execution_engine is running (PID: $(pgrep -f execution_engine | head -1))"
else
    warn "execution_engine is NOT running (may be intentional)"
fi

echo ""

# ── Go Engine API Endpoints ──────────────────────────────────────────────────
echo "--- Go Engine API Endpoints ---"

API_BASE="${GEMINI_API:-http://localhost:8080}"

check_endpoint() {
    local name="$1"
    local url="$2"
    local expected_key="${3:-}"
    local response
    response=$(curl -sf --max-time 5 "$url" 2>/dev/null) || { fail "$name unreachable ($url)"; return; }
    if [ -n "$expected_key" ]; then
        if echo "$response" | grep -q "$expected_key"; then
            pass "$name (key: $expected_key present)"
        else
            warn "$name responded but missing key '$expected_key'"
        fi
    else
        pass "$name"
    fi
}

check_endpoint "GET /api/signals"        "$API_BASE/api/signals"        "signals"
check_endpoint "GET /api/positions"      "$API_BASE/api/positions"      ""
check_endpoint "GET /api/system-monitor" "$API_BASE/api/system-monitor" "uptime"
check_endpoint "GET /api/architecture"   "$API_BASE/api/architecture"   ""
check_endpoint "GET /static/index.html"  "$API_BASE/"                   ""

echo ""

# ── Intel Source Keys (via Python) ───────────────────────────────────────────
echo "--- Intel Sources ---"

run_intel_check() {
    local source="$1"
    local key="$2"
    local output
    output=$("$VENV_PYTHON" - <<EOF 2>&1
import sys
sys.path.insert(0, "$ALPHA_ENGINE")
from ingestion.intel import get_intel
d = get_intel()
src = d.get("$source", {})
val = src.get("$key")
if val is None:
    print("MISSING")
elif isinstance(val, str) and val == "":
    print("EMPTY")
else:
    print("OK:" + str(val)[:60])
EOF
    )
    if echo "$output" | grep -q "^OK:"; then
        pass "intel.$source.$key = $(echo "$output" | sed 's/^OK://')"
    elif echo "$output" | grep -q "^MISSING\|^EMPTY"; then
        warn "intel.$source.$key is $output"
    else
        fail "intel.$source.$key — error: $(echo "$output" | tail -1)"
    fi
}

run_intel_check "news"         "sentiment_score"
run_intel_check "vix"          "vix_level"
run_intel_check "spy"          "spy_price"
run_intel_check "earnings"     "next_earnings_date"
run_intel_check "options_flow" "pc_ratio"
run_intel_check "catalyst"     "musk_sentiment"
run_intel_check "institutional" "net_insider_sentiment"
run_intel_check "ev_sector"    "sector_direction"
run_intel_check "macro_regime" "regime"
run_intel_check "premarket"    "futures_bias"

echo ""

# ── Database ─────────────────────────────────────────────────────────────────
echo "--- Database ---"

DB_PATH="/home/builder/tsla_alpha.db"

if [ -f "$DB_PATH" ]; then
    pass "Database exists: $DB_PATH"
    for table in historical_prices macro_snapshots ev_sector_snapshots signals; do
        count=$("$VENV_PYTHON" - <<PYEOF 2>/dev/null
import sqlite3
conn = sqlite3.connect("$DB_PATH")
try:
    row = conn.execute("SELECT COUNT(*) FROM $table").fetchone()
    print(row[0])
except Exception:
    print("ERR")
PYEOF
        )
        if [ "$count" = "ERR" ] || [ -z "$count" ]; then
            warn "Table '$table' missing or query failed"
        elif [ "$count" -eq 0 ]; then
            warn "Table '$table' is empty"
        else
            pass "Table '$table': $count rows"
        fi
    done
else
    fail "Database NOT found: $DB_PATH (run backfill.py)"
fi

echo ""

# ── Python venv ───────────────────────────────────────────────────────────────
echo "--- Python Environment ---"

if [ -x "$VENV_PYTHON" ]; then
    pass "venv python: $VENV_PYTHON"
    for pkg in yfinance numpy pandas; do
        if "$VENV_PYTHON" -c "import $pkg" 2>/dev/null; then
            pass "  import $pkg"
        else
            fail "  import $pkg FAILED"
        fi
    done
else
    fail "venv not found at $VENV_PYTHON"
fi

echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================"
echo "  Results: ${PASS} passed, ${WARN} warnings, ${FAIL} failed"
echo "======================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
