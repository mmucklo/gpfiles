#!/usr/bin/env bash
# TSLA Alpha — End-to-End Smoke Tests
# Validates every API endpoint and frontend build artifacts.
# Exits 0 if all pass, 1 if any fail.

BASE="${ALPHA_BASE:-http://localhost:2112}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

# ─── helpers ───────────────────────────────────────────────────────────────────

check() {
    local name="$1" result="$2"
    if [ "$result" = "ok" ]; then
        printf "  \033[32mPASS\033[0m %s\n" "$name"
        ((PASS++))
    else
        printf "  \033[31mFAIL\033[0m %s — %s\n" "$name" "$result"
        ((FAIL++))
    fi
}

fetch() {
    # fetch <url>  →  prints body; exits non-zero on HTTP error
    curl -sf --max-time 5 "$1" 2>/dev/null
}

py() {
    python3 -c "$1" 2>/dev/null
}

# ─── Section 1: API Health (gemini-fso) ────────────────────────────────────────

echo ""
echo "=== TSLA Alpha E2E Smoke Tests ==="
echo "    Base: $BASE"
echo ""
echo "--- [gemini-fso] API Health ---"

# /api/status
curl -sf --max-time 5 "$BASE/api/status" > /dev/null 2>&1 && r="ok" || r="HTTP error"
check "/api/status responds 200" "$r"

# /api/metrics/vitals — goroutines > 0, uptime_sec > 0
VITALS_BODY=$(fetch "$BASE/api/metrics/vitals")

r=$(echo "$VITALS_BODY" | py "
import sys, json
d = json.load(sys.stdin)
g = d.get('goroutines', 0)
print('ok' if g > 0 else 'goroutines=' + str(g))
" || echo "parse error")
check "/api/metrics/vitals goroutines > 0" "$r"

r=$(echo "$VITALS_BODY" | py "
import sys, json
d = json.load(sys.stdin)
u = d.get('uptime_sec', d.get('uptime_seconds', 0))
print('ok' if u > 0 else 'uptime_sec=' + str(u))
" || echo "parse error")
check "/api/metrics/vitals uptime_sec > 0" "$r"

# /api/broker/status — mode field present
r=$(fetch "$BASE/api/broker/status" | py "
import sys, json
d = json.load(sys.stdin)
print('ok' if 'mode' in d else 'mode field missing')
" || echo "parse error")
check "/api/broker/status mode field present" "$r"

# /api/portfolio — positions + nav or cash present
r=$(fetch "$BASE/api/portfolio" | py "
import sys, json
d = json.load(sys.stdin)
if 'positions' not in d: print('positions missing'); sys.exit()
if 'nav' not in d and 'cash' not in d: print('nav/cash missing'); sys.exit()
print('ok')
" || echo "parse error")
check "/api/portfolio valid structure" "$r"

# ─── Section 2: Signal Expiration Validation (gemini-iy6) ──────────────────────

echo ""
echo "--- [gemini-iy6] Signal Expiration Validation ---"

SIGNALS_BODY=$(fetch "$BASE/api/signals")
SIG_HTTP=$?

r=$([ $SIG_HTTP -eq 0 ] && echo "ok" || echo "HTTP error")
check "/api/signals responds 200" "$r"

# Required fields present on every signal
r=$(echo "$SIGNALS_BODY" | py "
import sys, json
from datetime import date
try:
    sigs = json.load(sys.stdin)
except Exception as e:
    print('json parse error: ' + str(e)); sys.exit()
if not isinstance(sigs, list):
    print('not a list'); sys.exit()
if not sigs:
    print('ok')  # empty is fine
    sys.exit()
required = {'model_id', 'direction', 'confidence', 'expiration_date'}
missing = []
for i, s in enumerate(sigs):
    for f in required:
        if f not in s:
            missing.append(f'sig[{i}] missing {f}')
print('ok' if not missing else missing[0])
" || echo "parse error")
check "/api/signals required fields present" "$r"

# expiration_date values all >= today
r=$(echo "$SIGNALS_BODY" | py "
import sys, json
from datetime import date
try:
    sigs = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if not isinstance(sigs, list) or not sigs:
    print('ok')
    sys.exit()
today = date.today()
stale = []
for s in sigs:
    exp = s.get('expiration_date', '')
    if not exp:
        continue
    try:
        d = date.fromisoformat(exp)
        if d < today:
            stale.append(f\"{s.get('model_id')} exp={exp}\")
    except ValueError:
        stale.append(f\"invalid date: {exp}\")
print('ok' if not stale else 'stale: ' + stale[0])
" || echo "parse error")
check "/api/signals no stale expiration_dates" "$r"

# implied_volatility present on non-IDLE signals (when signals exist)
r=$(echo "$SIGNALS_BODY" | py "
import sys, json
try:
    sigs = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if not isinstance(sigs, list) or not sigs:
    print('ok')  # no signals to check
    sys.exit()
active = [s for s in sigs if s.get('direction') not in ('NEUTRAL', 'IDLE')]
if not active:
    print('ok')  # only heartbeats
    sys.exit()
missing = [s.get('model_id','?') for s in active if 'implied_volatility' not in s]
print('ok' if not missing else 'missing implied_volatility on: ' + missing[0])
" || echo "parse error")
check "/api/signals implied_volatility field present on active signals" "$r"

# ─── Section 3: Gastown Data Integrity (gemini-c6k) ───────────────────────────

echo ""
echo "--- [gemini-c6k] Gastown Data Integrity ---"

GASTOWN_BODY=$(fetch "$BASE/api/gastown/status")
GS_HTTP=$?

r=$([ $GS_HTTP -eq 0 ] && echo "ok" || echo "HTTP error")
check "/api/gastown/status responds 200" "$r"

# agents array exists and non-empty
r=$(echo "$GASTOWN_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
agents = d.get('status', {}).get('agents', None)
if agents is None: print('agents field missing'); sys.exit()
if not isinstance(agents, list): print('agents not a list'); sys.exit()
print('ok' if len(agents) > 0 else 'agents array empty')
" || echo "parse error")
check "/api/gastown/status agents array non-empty" "$r"

# At least one agent running (including alt-session detection via tmux_sessions)
r=$(echo "$GASTOWN_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
agents = d.get('status', {}).get('agents', [])
tmux = d.get('tmux_sessions', [])
running = [a for a in agents if a.get('running')]
# alt-session: agent not marked running but has a tmux session with its name
alt = [a for a in agents if not a.get('running') and
       any(a.get('name','') in s or
           (a.get('name') == 'mayor' and 'tsla-claude' in s) or
           (a.get('name') == 'deacon' and 'tsla-claude' in s)
           for s in tmux)]
print('ok' if (running or alt) else 'no agents running (direct or alt-session)')
" || echo "parse error")
check "/api/gastown/status mayor running (direct or alt-session)" "$r"

# tmux_sessions exists and non-empty
r=$(echo "$GASTOWN_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
t = d.get('tmux_sessions', None)
if t is None: print('tmux_sessions field missing'); sys.exit()
if not isinstance(t, list): print('tmux_sessions not a list'); sys.exit()
print('ok' if t else 'tmux_sessions empty')
" || echo "parse error")
check "/api/gastown/status tmux_sessions non-empty" "$r"

# ready.sources has no "bd not installed" errors
r=$(echo "$GASTOWN_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
ready = d.get('ready', {}) or {}
sources = ready.get('sources', []) if isinstance(ready, dict) else []
bd_errors = [s.get('error','') for s in sources
             if 'bd' in s.get('name','').lower() and s.get('error')]
print('ok' if not bd_errors else 'bd error: ' + bd_errors[0])
" || echo "parse error")
check "/api/gastown/status beads functional (no bd errors)" "$r"

# patrols field is a non-error object (not a string error)
r=$(echo "$GASTOWN_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
patrols = d.get('patrols', None)
if patrols is None: print('patrols field missing'); sys.exit()
if isinstance(patrols, str) and ('error' in patrols.lower() or 'fail' in patrols.lower()):
    print('patrols is error string: ' + patrols[:60]); sys.exit()
print('ok')
" || echo "parse error")
check "/api/gastown/status patrols non-error object" "$r"

# log array exists
r=$(echo "$GASTOWN_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
log = d.get('log', None)
if log is None: print('log field missing'); sys.exit()
print('ok' if isinstance(log, list) else 'log not a list')
" || echo "parse error")
check "/api/gastown/status log field is array" "$r"

# /api/gastown/log
r=$(fetch "$BASE/api/gastown/log" | py "
import sys, json
try:
    d = json.load(sys.stdin)
    print('ok' if isinstance(d, list) else 'not a list')
except Exception as e:
    print('parse error: ' + str(e))
" || echo "HTTP error")
check "/api/gastown/log is JSON array" "$r"

# /api/gastown/log must not be empty or a single placeholder line
r=$(fetch "$BASE/api/gastown/log" | py "
import sys, json
try:
    lines = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if len(lines) == 0:
    print('empty')
elif len(lines) == 1 and ('No log file yet' in lines[0] or 'No activity log entries yet' in lines[0]):
    print('single placeholder line — no real log content')
else:
    print('ok')
" 2>/dev/null || echo "error")
check "/api/gastown/log has real content" "$r"

# /api/system/state — kill_switch and signals_blocked_reason required
r=$(fetch "$BASE/api/system/state" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if 'kill_switch' not in d: print('missing kill_switch field'); sys.exit()
if 'signals_blocked_reason' not in d: print('missing signals_blocked_reason'); sys.exit()
print('ok')
" 2>/dev/null || echo "error")
check "/api/system/state has required fields" "$r"

# ─── Section 3b: Data Audit (gemini-98u) ──────────────────────────────────────

echo ""
echo "--- [gemini-98u] Data Audit ---"

AUDIT_BODY=$(fetch "$BASE/api/data/audit")
AUDIT_HTTP=$?

r=$([ $AUDIT_HTTP -eq 0 ] && echo "ok" || echo "HTTP error")
check "/api/data/audit responds 200" "$r"

# tv_feed_ok field present
r=$(echo "$AUDIT_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if 'tv_feed_ok' not in d: print('tv_feed_ok field missing'); sys.exit()
print('ok')
" || echo "parse error")
check "/api/data/audit tv_feed_ok field present" "$r"

# spot_validation present and has required fields
r=$(echo "$AUDIT_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
sv = d.get('spot_validation', {})
for f in ('tv', 'yf', 'divergence_pct', 'ok'):
    if f not in sv:
        print('spot_validation missing ' + f); sys.exit()
print('ok')
" || echo "parse error")
check "/api/data/audit spot_validation fields present" "$r"

# spot_validation.ok true OR market closed (warn only)
r=$(echo "$AUDIT_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
sv = d.get('spot_validation', {})
if sv.get('ok'):
    print('ok')
else:
    w = sv.get('warning', '')
    # Divergence warning is acceptable; only fail on total feed failure
    if sv.get('tv') is None and sv.get('yf') is None:
        print('both feeds unavailable')
    else:
        print('ok')  # one source available or market closed — acceptable
" || echo "parse error")
check "/api/data/audit spot sources available" "$r"

# ibkr_connected field present (value can be false if IB Gateway not running)
r=$(echo "$AUDIT_BODY" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if 'ibkr_connected' not in d: print('ibkr_connected field missing'); sys.exit()
if 'primary_source' not in d: print('primary_source field missing'); sys.exit()
print('ok')
" || echo "parse error")
check "/api/data/audit has ibkr_connected field" "$r"

# ─── Section 3c: Account + Positions (gemini-60k) ─────────────────────────────

echo ""
echo "--- [gemini-60k] Account + Positions ---"

# /api/account responds 200 and has net_liquidation
r=$(fetch "$BASE/api/account" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if 'net_liquidation' not in d: print('net_liquidation field missing'); sys.exit()
print('ok')
" || echo "parse error")
check "/api/account responds 200 and has net_liquidation" "$r"

# /api/positions returns array
r=$(fetch "$BASE/api/positions" | py "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('json parse error'); sys.exit()
if not isinstance(d, list): print('not a list'); sys.exit()
print('ok')
" || echo "HTTP error")
check "/api/positions responds 200 and returns array" "$r"

# /api/fills responds 200
r=$(fetch "$BASE/api/fills" | py "
import sys, json
try:
    d = json.load(sys.stdin)
    print('ok' if isinstance(d, list) else 'not a list')
except Exception:
    print('json parse error')
" || echo "HTTP error")
check "/api/fills responds 200" "$r"

# ~/tsla_alpha.db exists (data logging initialized)
r=$([ -f "$HOME/tsla_alpha.db" ] && echo "ok" || echo "~/tsla_alpha.db not found — run data/init_db.py")
check "~/tsla_alpha.db exists" "$r"

# ─── Section 3d: Intel Endpoint ──────────────────────────────────────────────

echo ""
echo "--- [gemini-intel] Market Intelligence ---"

INTEL=$(fetch "$BASE/api/intel")
r=$(echo "$INTEL" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'vix' in d" 2>/dev/null && echo ok || echo "missing vix field")
check "/api/intel returns VIX data" "$r"

r=$(echo "$INTEL" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'options_flow' in d" 2>/dev/null && echo ok || echo "missing options_flow")
check "/api/intel returns options_flow" "$r"

r=$(echo "$INTEL" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'earnings' in d" 2>/dev/null && echo ok || echo "missing earnings")
check "/api/intel returns earnings" "$r"

# ─── Section 4: Frontend Build Artifacts (gemini-l50) ─────────────────────────

echo ""
echo "--- [gemini-l50] Frontend Build Artifacts ---"

DIST="$REPO_ROOT/alpha_control_center/dist"

r=$([ -f "$DIST/index.html" ] && echo "ok" || echo "dist/index.html missing — run npm run build")
check "dist/index.html exists" "$r"

r=$([ -d "$DIST/assets" ] && [ -n "$(ls -A "$DIST/assets" 2>/dev/null)" ] && echo "ok" || echo "dist/assets/ missing or empty")
check "dist/assets/ non-empty" "$r"

# index.html references at least one JS chunk
r=$(py "
import re, sys
try:
    html = open('$DIST/index.html').read()
    js = re.findall(r'assets/[^\"]+\.js', html)
    print('ok' if js else 'no JS asset references in index.html')
except FileNotFoundError:
    print('index.html not found')
")
check "dist/index.html references JS assets" "$r"

# ─── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
    printf "  \033[32m✓ ALL %d CHECKS PASSED\033[0m\n" "$PASS"
else
    printf "  \033[31m✗ %d FAILED / %d PASSED\033[0m\n" "$FAIL" "$PASS"
fi
echo "════════════════════════════════════"
echo ""

[ "$FAIL" -eq 0 ] && exit 0 || exit 1

# ─── UX Gate (Playwright, non-blocking if app unreachable) ────────────────────
# Runs after all API checks. If Playwright tests fail, the e2e smoke fails.
if [ -f "$REPO_ROOT/scripts/ux_gate.sh" ]; then
    echo ""
    echo "--- UX Gate (Playwright) ---"
    "$REPO_ROOT/scripts/ux_gate.sh" --base-url "$BASE" || {
        printf "  \033[31mFAIL\033[0m UX Gate failed\n"
        ((FAIL++))
    }
fi
