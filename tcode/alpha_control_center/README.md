# TSLA Alpha Control Center — Frontend

React + TypeScript + Vite dashboard for autonomous TSLA options trading.

## UX Audit Features (2026-04-11)

### 1. Discoverability
- Every interactive element has an `aria-label`.
- **Help Panel** — click the `?` (HelpCircle) icon in the header to open "What's on this screen?" listing every panel with one-line descriptions and interactive hints.
- Hidden actions are advertised via visible tooltips and `data-tooltip` attributes.

### 2. Tooltip Correctness
- The `Tooltip` component uses `fixed` positioning with above/below flip based on `getBoundingClientRect()` — no clipping at viewport edges.
- All tooltips carry `data-tooltip` attributes for Playwright test discovery.
- Playwright test: `tests/ux_tooltip_visibility.spec.ts` — iterates every `[data-tooltip]` element and asserts `getBoundingClientRect()` is fully inside the viewport after hover.

### 3. Fluidity
- `SkeletonLoader` components (`SkeletonCard`, `SkeletonTable`, `SkeletonLine`) displayed for Intel and Scorecard panels while data loads.
- Skeleton CSS uses a smooth shimmer animation — no layout shift.
- Playwright test: `tests/ux_performance.spec.ts` — records Long Tasks API entries during a 30-second interactive session, asserts no frame >100ms (max 3 heavy tasks allowed for initial network fetches).
- All polled intervals are debounced via `useCallback` + `useRef`.

### 4. Drill-Downs
- **NAV** — clicking the NET LIQ pill opens a full breakdown: cash + position market values + unrealized + realized P&L, with source API path and timestamp.
- **Signals** — clicking any signal card opens the `SignalModal` with full provenance: timestamp, model ID, confidence source, spot sources (TV/YF), options chain, data feed audit.
- **Positions** — clicking any position card opens `PositionModal`: qty, avg cost, current price, market value, unrealized P&L, strike, expiry, Greeks (delta, IV), catalyst.
- **Trades** — clicking any trade row opens `FillDrilldownModal`: entry/exit price, qty, cost basis, signal provenance, options snapshot at entry.

### 5. Fraud-Protection "Integrity Status" Panel
Three independent indicators visible in the header at all times:

| Indicator | Green | Amber | Red |
|-----------|-------|-------|-----|
| **PRICE** | <0.2% divergence between TV and YF | 0.2–0.5% | >0.5% — trading halted |
| **CHAIN** | Options chain <2min stale | 2–5min stale during market hours | >5min stale during market hours |
| **EXEC**  | Broker connected, mode ok | Disconnected in paper mode | Live mode, broker not confirmed |

- Click any indicator to open a detailed breakdown with exact values, source, computation rule, and timestamp.
- When any indicator is RED, a prominent alert banner appears in the dashboard and the "NEW TRADE" button is disabled and `aria-disabled="true"`.
- Playwright test: `tests/ux_integrity_gate.spec.ts` — mocks audit API to force RED, asserts new-trade button is disabled.

### 6. Pre-Commit Bake-In
- `scripts/ux_gate.sh` — runs all Playwright UX tests
- Wired into `scripts/e2e_smoke.sh` (appended at end)
- Wired into `scripts/backtest_gate.sh` (runs after Sharpe/drawdown checks)
- Git pre-push hook at `.git/hooks/pre-push` — blocks push if UX gate fails

## Running Tests

```bash
# From alpha_control_center/
PLAYWRIGHT_BASE_URL=http://localhost:2112 \
  ./node_modules/.bin/playwright test --config=playwright.config.ts

# Or via the gate script (from repo root):
./scripts/ux_gate.sh --base-url http://localhost:2112
```

## Dev

```bash
npm run dev      # start Vite dev server (port 5173)
npm run build    # production build → dist/
npm run lint     # ESLint
```

## Architecture

See `../README.md` and the Notion project hub for full architecture docs.
