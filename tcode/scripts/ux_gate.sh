#!/usr/bin/env bash
# ux_gate.sh — Run targeted UX Playwright tests based on what changed.
# Exit 0 = pass, Exit 1 = fail
# Usage: ./scripts/ux_gate.sh [--base-url URL] [--full]
#
# By default, compares HEAD against the remote tracking branch and runs only
# the specs relevant to the changed files.  Pass --full to force the whole
# suite (used in CI).
#
# Requirements:
#   1. The frontend app to be running (PLAYWRIGHT_BASE_URL or http://localhost:2112)
#   2. Playwright available via local node_modules or npx

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/alpha_control_center"
BASE_URL="${PLAYWRIGHT_BASE_URL:-http://localhost:2112}"
FORCE_FULL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url) BASE_URL="$2"; shift 2 ;;
        --full)     FORCE_FULL=true; shift ;;
        *)          shift ;;
    esac
done

echo ""
echo "=== UX Gate — Playwright Tests ==="
echo "    Base URL: $BASE_URL"
echo "    Test dir: $FRONTEND_DIR/tests"
echo ""

# Resolve playwright binary: prefer local node_modules, fall back to npx
PLAYWRIGHT_BIN=""
if [ -x "$FRONTEND_DIR/node_modules/.bin/playwright" ]; then
    PLAYWRIGHT_BIN="$FRONTEND_DIR/node_modules/.bin/playwright"
elif command -v playwright &>/dev/null; then
    PLAYWRIGHT_BIN="$(command -v playwright)"
elif command -v npx &>/dev/null; then
    PLAYWRIGHT_BIN="npx playwright"
else
    echo "WARN: Playwright not found (no local node_modules/.bin/playwright, no npx). Skipping UX gate."
    exit 0
fi

echo "    Playwright: $PLAYWRIGHT_BIN"
echo ""

# Check app is reachable — skip non-blocking if not running
if ! curl -sf --max-time 5 "$BASE_URL/api/status" > /dev/null 2>&1; then
    echo "WARN: App not reachable at $BASE_URL — skipping Playwright tests (not a gate failure in CI without app)."
    exit 0
fi

# ── All known specs (canonical run order) ─────────────────────────────────────
ALL_SPECS=(
    tests/ux_no_placeholders.spec.ts
    tests/ux_every_hoverable_tooltipped.spec.ts
    tests/ux_tooltip_no_clip.spec.ts
    tests/ux_tooltip_visibility.spec.ts
    tests/ux_integrity_gate.spec.ts
    tests/ux_integrity_contract.spec.ts
    tests/ux_performance.spec.ts
    tests/ux_signal_economics.spec.ts
)

# ── Determine targeted spec list ──────────────────────────────────────────────
# Emits spec basenames (prefixed with "tests/") on stdout, or the sentinel
# FULL_SUITE / SKIP.
#
# Mapping rules:
#   • ux_gate.sh or playwright.config.ts changed → FULL_SUITE
#   • spec file directly modified                → that spec
#   • src/ changed, tooltip keyword              → ux_tooltip_* + ux_every_hoverable_*
#   • src/ changed, signal/economics keyword     → ux_signal_economics
#   • src/ changed, integrity/audit keyword      → ux_integrity_gate
#   • any src/ change                            → + smoke (no_placeholders, every_hoverable, integrity_contract)
#   • no UX-relevant files changed               → SKIP
targeted_specs() {
    local tracking
    tracking="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null \
                || echo 'origin/master')"

    local all_changed
    all_changed="$(git -C "$REPO_ROOT" diff --name-only "${tracking}..HEAD" 2>/dev/null || true)"
    [ -z "$all_changed" ] && { echo FULL_SUITE; return; }

    # Normalise paths to be relative to REPO_ROOT (strips the git-root prefix)
    local git_root pfx
    git_root="$(git -C "$REPO_ROOT" rev-parse --show-toplevel)"
    pfx="${REPO_ROOT#${git_root}}"
    pfx="${pfx#/}"   # strip leading slash; empty if REPO_ROOT == git_root

    local changed
    if [ -n "$pfx" ]; then
        changed="$(echo "$all_changed" | grep "^${pfx}/" | sed "s|^${pfx}/||" || true)"
    else
        changed="$all_changed"
    fi
    [ -z "$changed" ] && { echo SKIP; return; }   # all changes outside REPO_ROOT

    # Gate / config changes require the full suite to validate
    if echo "$changed" | grep -qE '^scripts/ux_gate\.sh$|^alpha_control_center/playwright\.config\.ts$'; then
        echo FULL_SUITE; return
    fi

    local specs=""

    # Spec files directly modified in this push
    local direct
    direct="$(echo "$changed" | grep '^alpha_control_center/tests/ux_' \
              | sed 's|^alpha_control_center/tests/||' || true)"
    specs="$direct"

    # Source file changes → domain-mapped specs + smoke baseline
    local src
    src="$(echo "$changed" | grep '^alpha_control_center/src/' || true)"

    if [ -n "$src" ]; then
        # Smoke: always run these when source changes
        specs="$specs
ux_no_placeholders.spec.ts
ux_every_hoverable_tooltipped.spec.ts
ux_integrity_contract.spec.ts"

        if echo "$src" | grep -qiE 'tooltip'; then
            specs="$specs
ux_tooltip_no_clip.spec.ts
ux_tooltip_visibility.spec.ts"
        fi
        if echo "$src" | grep -qiE 'signal|economics'; then
            specs="$specs
ux_signal_economics.spec.ts"
        fi
        if echo "$src" | grep -qiE 'integrity|audit'; then
            specs="$specs
ux_integrity_gate.spec.ts"
        fi
    fi

    # Deduplicate and emit with tests/ prefix
    local deduped
    deduped="$(echo "$specs" | grep -v '^[[:space:]]*$' | sort -u)"
    [ -z "$deduped" ] && { echo SKIP; return; }
    echo "$deduped" | sed 's|^|tests/|'
}

# ── Build the final SPECS array ───────────────────────────────────────────────
SPECS=()
if [ "$FORCE_FULL" = true ]; then
    SPECS=("${ALL_SPECS[@]}")
    echo "    Mode: full suite (--full)"
else
    mapfile -t SELECTION < <(targeted_specs)
    case "${SELECTION[0]:-}" in
        FULL_SUITE)
            SPECS=("${ALL_SPECS[@]}")
            echo "    Mode: full suite (gate/config changed)"
            ;;
        SKIP)
            echo "    No UX-relevant changes detected — skipping UX gate."
            exit 0
            ;;
        *)
            SPECS=("${SELECTION[@]}")
            echo "    Mode: targeted"
            for s in "${SPECS[@]}"; do echo "      $s"; done
            ;;
    esac
fi
echo ""

# ── Run Playwright ────────────────────────────────────────────────────────────
cd "$FRONTEND_DIR"
if PLAYWRIGHT_BASE_URL="$BASE_URL" \
        $PLAYWRIGHT_BIN test \
        --config=playwright.config.ts \
        "${SPECS[@]}" \
        "${@}"; then
    echo ""
    echo "UX GATE: PASSED"
    exit 0
else
    echo ""
    echo "UX GATE: FAILED — see output above"
    exit 1
fi
