/**
 * Phase 17 — Position Manager
 *
 * Shows open managed positions with:
 *   - ATR stop (red), trailing stop (amber), target (green) levels
 *   - Mini time countdown until time stop
 *   - Manual close button
 *   - Circuit breaker banner (hard stop / soft pause / target reached)
 *   - Exit toast notifications (winner green, loser red)
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import './PositionManager.css';

// ── Types ──────────────────────────────────────────────────────────────────

interface ManagedPosition {
  trade_id: number;
  entry_price: number;
  entry_time: string;
  quantity: number;
  direction: 'LONG' | 'SHORT';
  strategy: string;
  initial_stop: number;
  current_stop: number;
  target: number | null;
  trailing_engaged: boolean;
  time_stop_at: string;
  remaining_sec: number;
}

interface Bar {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap: number;
}

interface Indicators {
  atr: number;
  volume_ratio: number;
  vwap: number;
  bar_range_vs_atr: number;
  bar_count: number;
}

interface CircuitBreakerState {
  status: 'active' | 'soft_pause' | 'hard_stop' | 'target_reached';
  daily_pnl: number;
  consecutive_losses: number;
  remaining_pause_sec: number;
  resume_at?: string;
  winners?: number;
  losers?: number;
}

interface ExitToast {
  id: string;
  trade_id: number;
  pnl: number;
  stop_type: string;
  fading: boolean;
}

const API_BASE = '';

// ── Helpers ────────────────────────────────────────────────────────────────

function fmtCountdown(sec: number): { label: string; urgency: '' | 'urgent' | 'critical' } {
  if (sec <= 0) return { label: '00:00', urgency: 'critical' };
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  const label = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  const urgency = sec <= 60 ? 'critical' : sec <= 180 ? 'urgent' : '';
  return { label, urgency };
}

function fmtPnl(pnl: number): string {
  return pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`;
}

// ── Stop visualization bar ─────────────────────────────────────────────────

function StopViz({
  entry, initialStop, currentStop, target, currentPrice,
}: {
  entry: number; initialStop: number; currentStop: number;
  target: number | null; currentPrice: number;
}) {
  const allPrices = [initialStop, currentStop, entry, currentPrice, target ?? currentPrice].filter(Boolean);
  const min = Math.min(...allPrices) * 0.998;
  const max = Math.max(...allPrices) * 1.002;
  const range = max - min || 1;

  const pct = (price: number) => Math.max(0, Math.min(100, ((price - min) / range) * 100));

  return (
    <div className="stop-viz">
      <div className="stop-viz-track">
        {initialStop > 0 && (
          <div
            className="stop-indicator initial-stop"
            style={{ left: `${pct(initialStop)}%` }}
            title={`Initial stop: $${initialStop.toFixed(2)}`}
          />
        )}
        {currentStop > 0 && currentStop !== initialStop && (
          <div
            className="stop-indicator trailing-stop"
            style={{ left: `${pct(currentStop)}%` }}
            title={`Trailing stop: $${currentStop.toFixed(2)}`}
          />
        )}
        {target != null && (
          <div
            className="stop-indicator target"
            style={{ left: `${pct(target)}%` }}
            title={`Target: $${target.toFixed(2)}`}
          />
        )}
        <div
          className="stop-indicator current-price"
          style={{ left: `${pct(currentPrice)}%` }}
          title={`Current: $${currentPrice.toFixed(2)}`}
        />
      </div>
      <div className="stop-line-label">
        <span style={{ color: '#e53935' }}>SL ${initialStop > 0 ? initialStop.toFixed(2) : 'N/A'}</span>
        {currentStop !== initialStop && currentStop > 0 && (
          <span style={{ color: '#ff9800' }}>Trail ${currentStop.toFixed(2)}</span>
        )}
        {target != null && (
          <span style={{ color: '#26a17b' }}>TP ${target.toFixed(2)}</span>
        )}
      </div>
    </div>
  );
}

// ── Circuit Breaker Banner ─────────────────────────────────────────────────

function CircuitBreakerBanner({ state }: { state: CircuitBreakerState | null }) {
  if (!state || state.status === 'active') return null;

  if (state.status === 'hard_stop') {
    return (
      <div className="circuit-breaker-banner hard-stop" data-testid="circuit-breaker-banner">
        🛑 CIRCUIT BREAKER: Daily loss limit hit (${Math.abs(state.daily_pnl).toFixed(0)} lost).
        Trading paused for the day.
      </div>
    );
  }
  if (state.status === 'soft_pause') {
    const mins = Math.ceil(state.remaining_pause_sec / 60);
    return (
      <div className="circuit-breaker-banner soft-pause" data-testid="circuit-breaker-banner">
        ⚠️ {state.consecutive_losses} consecutive losses — cooling off for {mins} more min.
      </div>
    );
  }
  if (state.status === 'target_reached') {
    return (
      <div className="circuit-breaker-banner target-reached" data-testid="circuit-breaker-banner">
        🎯 Daily target reached! ${state.daily_pnl.toFixed(0)} profit today.
      </div>
    );
  }
  return null;
}

// ── Exit Toasts ────────────────────────────────────────────────────────────

function ExitToasts({ toasts }: { toasts: ExitToast[] }) {
  if (toasts.length === 0) return null;
  return (
    <div className="exit-toast-container" data-testid="exit-toast-container">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`exit-toast ${t.pnl >= 0 ? 'winner' : 'loser'} ${t.fading ? 'fading' : ''}`}
        >
          {t.pnl >= 0 ? '✅' : '❌'} Trade #{t.trade_id} closed —&nbsp;
          {fmtPnl(t.pnl)} ({t.stop_type})
        </div>
      ))}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

export default function PositionManager() {
  const [positions, setPositions] = useState<ManagedPosition[]>([]);
  const [bars, setBars] = useState<Bar[]>([]);
  const [indicators, setIndicators] = useState<Indicators | null>(null);
  const [cbState, setCbState] = useState<CircuitBreakerState | null>(null);
  const [toasts, setToasts] = useState<ExitToast[]>([]);
  const [closing, setClosing] = useState<Set<number>>(new Set());

  // Countdown tick — force re-render every second for live countdowns
  const [, setTick] = useState(0);
  useEffect(() => {
    const interval = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  const prevPositionIds = useRef<Set<number>>(new Set());

  const fetchPositions = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/positions/managed`);
      if (!resp.ok) return;
      const data = await resp.json();
      const newPositions: ManagedPosition[] = data.positions ?? [];
      setBars(data.bars ?? []);
      setIndicators(data.indicators ?? null);

      // Detect closed positions → emit exit toasts
      const newIds = new Set(newPositions.map(p => p.trade_id));
      for (const oldId of prevPositionIds.current) {
        if (!newIds.has(oldId)) {
          // Position closed — we don't have P&L here, show a toast without it
          addToast(oldId, 0, 'CLOSED');
        }
      }
      prevPositionIds.current = newIds;
      setPositions(newPositions);
    } catch {
      // Silent — position manager is non-critical
    }
  }, []);

  const fetchCircuitBreaker = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/circuit-breaker`);
      if (!resp.ok) return;
      const data = await resp.json();
      setCbState(data);
    } catch {
      // Silent
    }
  }, []);

  useEffect(() => {
    fetchPositions();
    fetchCircuitBreaker();
    const posTimer = setInterval(fetchPositions, 15_000);    // 15s
    const cbTimer  = setInterval(fetchCircuitBreaker, 30_000); // 30s
    return () => { clearInterval(posTimer); clearInterval(cbTimer); };
  }, [fetchPositions, fetchCircuitBreaker]);

  function addToast(trade_id: number, pnl: number, stop_type: string) {
    const id = `${trade_id}-${Date.now()}`;
    setToasts(prev => [...prev, { id, trade_id, pnl, stop_type, fading: false }]);
    setTimeout(() => {
      setToasts(prev => prev.map(t => t.id === id ? { ...t, fading: true } : t));
      setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 400);
    }, 5000);
  }

  async function handleClose(trade_id: number) {
    setClosing(prev => new Set([...prev, trade_id]));
    try {
      const resp = await fetch(`${API_BASE}/api/positions/managed/${trade_id}/close`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exit_price: 0 }),  // 0 = use latest bar close
      });
      if (resp.ok) {
        addToast(trade_id, 0, 'MANUAL');
        await fetchPositions();
      }
    } finally {
      setClosing(prev => {
        const n = new Set(prev);
        n.delete(trade_id);
        return n;
      });
    }
  }

  // Compute countdown client-side from time_stop_at
  function remainingSec(pos: ManagedPosition): number {
    const stopAt = new Date(pos.time_stop_at).getTime();
    return Math.max(0, Math.floor((stopAt - Date.now()) / 1000));
  }

  // Latest bar close for unrealized P&L display
  const currentPrice = bars.length > 0 ? bars[bars.length - 1].close : 0;

  return (
    <>
      <div className="position-manager" data-testid="position-manager">
        <h3>
          Open Positions
          {indicators && (
            <span style={{ float: 'right', fontWeight: 400, color: '#888', fontSize: 11 }}>
              ATR {indicators.atr.toFixed(3)} · Vol×{indicators.volume_ratio.toFixed(1)}
            </span>
          )}
        </h3>

        <CircuitBreakerBanner state={cbState} />

        {positions.length === 0 ? (
          <div className="empty-state">No open positions</div>
        ) : (
          positions.map(pos => {
            const remaining = remainingSec(pos);
            const { label: countdownLabel, urgency } = fmtCountdown(remaining);
            const pnlPerUnit = currentPrice > 0
              ? (pos.direction === 'LONG' ? currentPrice - pos.entry_price : pos.entry_price - currentPrice)
              : 0;
            const pnlDollar = pnlPerUnit * pos.quantity * 100;
            const isPositive = pnlDollar >= 0;

            return (
              <div
                key={pos.trade_id}
                className={`position-card ${isPositive ? 'pnl-positive' : 'pnl-negative'}`}
                data-testid={`position-card-${pos.trade_id}`}
              >
                <div className="position-header">
                  <span className="position-title">
                    #{pos.trade_id} {pos.strategy} {pos.direction}
                    {pos.trailing_engaged && (
                      <span style={{ color: '#ff9800', marginLeft: 6, fontSize: 10 }}>TRAILING</span>
                    )}
                  </span>
                  <span className={`position-pnl ${isPositive ? 'green' : 'red'}`}>
                    {currentPrice > 0 ? fmtPnl(pnlDollar) : '—'}
                  </span>
                </div>

                <div className="position-meta">
                  <span>Entry: ${pos.entry_price.toFixed(2)}</span>
                  {currentPrice > 0 && <span>Now: ${currentPrice.toFixed(2)}</span>}
                  <span>Qty: {pos.quantity}</span>
                </div>

                {currentPrice > 0 && (
                  <StopViz
                    entry={pos.entry_price}
                    initialStop={pos.initial_stop}
                    currentStop={pos.current_stop}
                    target={pos.target}
                    currentPrice={currentPrice}
                  />
                )}

                <div className="time-stop-countdown" data-testid="time-stop-countdown">
                  <span>⏱ Closes in</span>
                  <span className={urgency || undefined}>{countdownLabel}</span>
                </div>

                <button
                  className="close-now-btn"
                  data-testid={`close-btn-${pos.trade_id}`}
                  disabled={closing.has(pos.trade_id)}
                  onClick={() => handleClose(pos.trade_id)}
                  aria-label={`Close position ${pos.trade_id} at market`}
                >
                  {closing.has(pos.trade_id) ? 'Closing…' : 'Close Now'}
                </button>
              </div>
            );
          })
        )}
      </div>

      <ExitToasts toasts={toasts} />
    </>
  );
}
