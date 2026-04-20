/**
 * StatusBar — Phase 18 UI Overhaul (Zone A)
 *
 * Fixed 64px bar that never scrolls. Three sections:
 *  Left:   Daily P&L (large) + progress bar toward target + unrealized
 *  Center: Position count · Regime badge · Strategy selector
 *  Right:  Mode badge · SYS health · Pause countdown · Catalyst alert
 *
 * Color law: #00C853 = profit ONLY, #FF1744 = loss ONLY.
 * Amber/orange for warnings, blue for info. NEVER red for system errors.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import './StatusBar.css';
import TermLabel from './TermLabel';
import type { HealthSummary as SysHealthSummary } from './SystemHealthPanel';

// ── Types ─────────────────────────────────────────────────────────────────────

interface PnLData {
  total_pnl: number;
  daily_target: number;
  target_pct: number;
  loss_used_pct: number;
  circuit_broken: boolean;
  updated_at: string;
}

interface RegimeData {
  regime: string;
  color: string;
  confidence: number;
  recommended_strategy: string;
}

interface Catalyst {
  name: string;
  time: string;
  impact: 'high' | 'medium' | 'low';
  countdown_sec: number;
}

interface MorningBriefingData {
  catalysts: Catalyst[];
  refreshed_at: string;
}

// Re-export the shared type from SystemHealthPanel
export type { SysHealthSummary as HealthSummary };

export interface PauseStatus {
  paused: boolean;
  unpause_until: string | null;
  remaining_sec: number;
}

interface BrokerStatus {
  mode: string;
  connected: boolean;
}

interface Props {
  healthSummary: SysHealthSummary | null;
  pauseStatus: PauseStatus;
  brokerStatus: BrokerStatus | null;
  onHealthClick: () => void;
  onPause: () => void;
  /** Fires when user clicks the position-count badge (parent scrolls to table) */
  onPositionCountClick?: () => void;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const REGIME_COLORS: Record<string, string> = {
  green: '#00C853',
  grey:  '#8b949e',
  amber: '#FFB300',
  blue:  '#58a6ff',
  red:   '#FF1744',
};

const STRATEGIES = [
  { key: 'MOMENTUM',    emoji: '📈', label: 'MOMENTUM' },
  { key: 'IRON_CONDOR', emoji: '🦅', label: 'CONDOR' },
  { key: 'WAVE_RIDER',  emoji: '🌊', label: 'WAVE' },
  { key: 'JADE_LIZARD', emoji: '🦎', label: 'JADE LZD' },
  { key: 'STRADDLE',    emoji: '🎯', label: 'STRADDLE' },
  { key: 'GAMMA_SCALP', emoji: '🔄', label: 'GAMMA SCP' },
] as const;
type StrategyKey = typeof STRATEGIES[number]['key'];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatCountdown(sec: number): string {
  if (sec <= 0) return '0:00';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatPnL(val: number): string {
  const abs = Math.abs(val);
  const sign = val >= 0 ? '+' : '-';
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(1)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

// ── StatusBar ─────────────────────────────────────────────────────────────────

const StatusBar: React.FC<Props> = ({
  healthSummary,
  pauseStatus,
  brokerStatus,
  onHealthClick,
  onPause,
  onPositionCountClick,
}) => {
  const today = new Date().toISOString().slice(0, 10);

  const [pnlData, setPnlData]       = useState<PnLData | null>(null);
  const [positionCount, setPositionCount] = useState(0);
  const [regime, setRegime]         = useState<RegimeData | null>(null);
  const [strategy, setStrategy]     = useState<StrategyKey | null>(null);
  const [catalysts, setCatalysts]   = useState<Catalyst[]>([]);
  const [regimeFetched, setRegimeFetched] = useState(false);
  const [showStratDrop, setShowStratDrop] = useState(false);
  const [showCatalystPop, setShowCatalystPop] = useState(false);
  const [tick, setTick] = useState(0);
  const stratDropRef = useRef<HTMLDivElement>(null);
  const catalystPopRef = useRef<HTMLDivElement>(null);
  const briefingFetchedAt = useRef<number>(0);

  // ── Data fetchers ──────────────────────────────────────────────────────────

  const fetchPnL = useCallback(async () => {
    try {
      const r = await fetch(`/api/trades/pnl?date=${today}`);
      if (r.ok) setPnlData(await r.json());
    } catch { /* silent */ }
  }, [today]);

  const fetchPositions = useCallback(async () => {
    try {
      const r = await fetch('/api/positions/managed');
      if (r.ok) {
        const d = await r.json();
        setPositionCount((d.positions ?? []).length);
      }
    } catch { /* silent */ }
  }, []);

  const fetchRegime = useCallback(async () => {
    try {
      const r = await fetch('/api/regime/current');
      if (r.ok) {
        const d = await r.json();
        setRegime(d);
        setRegimeFetched(true);
      }
    } catch { /* silent */ }
  }, []);

  const fetchStrategy = useCallback(async () => {
    try {
      const r = await fetch('/api/strategy/current');
      if (r.ok) {
        const d = await r.json();
        if (d.strategy) setStrategy(d.strategy as StrategyKey);
      }
    } catch { /* silent */ }
  }, []);

  const fetchCatalysts = useCallback(async () => {
    try {
      const r = await fetch('/api/morning-briefing');
      if (r.ok) {
        const d = await r.json() as MorningBriefingData;
        setCatalysts(d.catalysts ?? []);
        briefingFetchedAt.current = d.refreshed_at
          ? new Date(d.refreshed_at).getTime()
          : Date.now();
      }
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchPnL(); fetchPositions(); fetchRegime(); fetchStrategy(); fetchCatalysts();
    const pnlTimer  = setInterval(fetchPnL, 10_000);
    const posTimer  = setInterval(fetchPositions, 15_000);
    const regTimer  = setInterval(fetchRegime, 2 * 60_000);
    const stratTimer = setInterval(fetchStrategy, 60_000);
    const catTimer  = setInterval(fetchCatalysts, 5 * 60_000);
    return () => {
      clearInterval(pnlTimer); clearInterval(posTimer); clearInterval(regTimer);
      clearInterval(stratTimer); clearInterval(catTimer);
    };
  }, [fetchPnL, fetchPositions, fetchRegime, fetchStrategy, fetchCatalysts]);

  // 1-second tick for countdown display
  useEffect(() => {
    const t = setInterval(() => setTick(v => v + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // Find nearest catalyst within 2 hours
  const nearCatalyst = (() => {
    if (!catalysts.length) return null;
    const now = Date.now() / 1000;
    const fetchedAt = briefingFetchedAt.current / 1000;
    for (const c of catalysts) {
      const remaining = c.countdown_sec - (now - fetchedAt);
      if (remaining > 0 && remaining <= 7200) return { ...c, remaining: Math.floor(remaining) };
    }
    return null;
  })();

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (stratDropRef.current && !stratDropRef.current.contains(e.target as Node)) {
        setShowStratDrop(false);
      }
      if (catalystPopRef.current && !catalystPopRef.current.contains(e.target as Node)) {
        setShowCatalystPop(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // ── Strategy select ────────────────────────────────────────────────────────

  const handleSelectStrategy = async (key: StrategyKey) => {
    try {
      const r = await fetch('/api/strategy/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: key }),
      });
      if (r.ok) setStrategy(key);
    } catch { /* silent */ }
    setShowStratDrop(false);
  };

  // ── Derived values ─────────────────────────────────────────────────────────

  const totalPnL    = pnlData?.total_pnl ?? 0;
  const dailyTarget = pnlData?.daily_target ?? 10000;
  const targetPct   = Math.min(100, Math.max(0, pnlData?.target_pct ?? 0));
  const circuit     = pnlData?.circuit_broken ?? false;
  const pnlPositive = totalPnL > 0;
  const pnlNegative = totalPnL < 0;

  const regimeColor = regime ? (REGIME_COLORS[regime.color ?? 'grey'] ?? '#8b949e') : '#8b949e';
  const regimeBg    = regime ? `${regimeColor}1a` : 'transparent';

  const mode = brokerStatus?.mode ?? 'LOADING';
  const isLive = mode === 'IBKR_LIVE';
  const isSim  = mode === 'SIMULATION';
  const disconnected = brokerStatus && !brokerStatus.connected && !isSim;

  const healthOk = healthSummary ? (healthSummary.error === 0 && healthSummary.degraded === 0) : true;
  const healthLabel = healthSummary ? `${healthSummary.ok}/${healthSummary.total}` : '…/…';

  const pausedRemaining = pauseStatus?.remaining_sec ?? 0;
  const isPaused = pauseStatus?.paused && pausedRemaining > 0;

  // Track ticks to refresh countdowns
  void tick; // used only to trigger re-render

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="status-bar" role="banner" aria-label="Status bar" data-testid="status-bar">

      {/* ── LEFT: P&L ───────────────────────────────────────────────── */}
      <div className="sb-section sb-left">
        <div className="sb-pnl-block" data-testid="sb-pnl-block">
          {circuit && (
            <span className="sb-circuit-badge" data-testid="sb-circuit-badge" aria-label="Circuit breaker triggered">
              🚨 CB
            </span>
          )}
          <span
            className={`sb-pnl-amount ${pnlPositive ? 'profit' : pnlNegative ? 'loss' : 'zero'}`}
            data-testid="sb-pnl-amount"
            aria-label={`Daily P&L: ${formatPnL(totalPnL)}`}
          >
            {formatPnL(totalPnL)}
          </span>
        </div>
        <div className="sb-target-row">
          <div className="sb-target-track" aria-label={`${targetPct.toFixed(0)}% of $${(dailyTarget/1000).toFixed(0)}k target`} data-testid="sb-target-bar">
            <div
              className={`sb-target-fill ${targetPct >= 100 ? 'complete' : targetPct >= 50 ? 'halfway' : ''}`}
              style={{ width: `${pnlNegative ? 0 : targetPct}%` }}
            />
          </div>
          <span className="sb-target-label">
            {targetPct.toFixed(0)}% of ${(dailyTarget / 1000).toFixed(0)}k
          </span>
        </div>
      </div>

      {/* ── CENTER: Positions + Regime + Strategy ────────────────────── */}
      <div className="sb-section sb-center">
        {/* Position count */}
        <button
          className="sb-chip sb-pos-count"
          data-testid="sb-position-count"
          onClick={onPositionCountClick}
          aria-label={`${positionCount} open positions — click to scroll to positions table`}
          title="Click to jump to positions table"
        >
          📊 {positionCount} pos
        </button>

        {/* Regime badge */}
        {regimeFetched && regime && (
          <span
            className="sb-chip sb-regime-badge"
            data-testid="regime-badge"
            style={{ color: regimeColor, background: regimeBg, borderColor: `${regimeColor}40` }}
            title={`Regime: ${regime.regime} · ${(regime.confidence * 100).toFixed(0)}% confidence`}
            aria-label={`Market regime: ${regime.regime}, ${(regime.confidence * 100).toFixed(0)}% confidence`}
          >
            <span className="sb-regime-dot" style={{ background: regimeColor }} />
            <TermLabel term="REGIME_CLASSIFIER">{regime.regime}</TermLabel>
            <span className="sb-regime-conf">{(regime.confidence * 100).toFixed(0)}%</span>
          </span>
        )}

        {/* Strategy selector */}
        <div className="sb-strategy-wrapper" ref={stratDropRef}>
          <button
            className="sb-chip sb-strategy-chip"
            data-testid="strategy-selector"
            onClick={() => setShowStratDrop(v => !v)}
            aria-label={`Current strategy: ${strategy ?? 'none'} — click to change`}
            aria-haspopup="listbox"
            aria-expanded={showStratDrop}
          >
            {STRATEGIES.find(s => s.key === strategy)?.emoji ?? '📊'}{' '}
            {strategy?.replace('_', ' ') ?? 'SELECT'}
            <span className="sb-dropdown-arrow">▾</span>
          </button>

          {showStratDrop && (
            <div className="sb-strategy-dropdown" role="listbox" aria-label="Strategy options" data-testid="strategy-dropdown">
              {STRATEGIES.map(s => (
                <button
                  key={s.key}
                  className={`sb-strat-opt ${strategy === s.key ? 'active' : ''}`}
                  role="option"
                  aria-selected={strategy === s.key}
                  data-testid="strategy-option"
                  onClick={() => handleSelectStrategy(s.key)}
                >
                  {s.emoji} {s.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── RIGHT: Mode + Health + Pause + Catalyst ──────────────────── */}
      <div className="sb-section sb-right">
        {/* Mode badge */}
        <span
          className={`sb-chip sb-mode-badge ${isLive ? 'live' : isSim ? 'sim' : 'paper'}`}
          data-testid="sb-mode-badge"
          aria-label={`Execution mode: ${mode}${disconnected ? ' — IBKR disconnected' : ''}`}
        >
          {isLive ? '⚠ LIVE' : isSim ? 'SIM' : 'PAPER'}
          {disconnected && <span className="sb-disconnected-dot" aria-hidden="true">⚠</span>}
        </span>

        {/* SYS health badge */}
        <button
          className={`sb-chip sb-health-badge ${healthOk ? 'ok' : 'degraded'}`}
          data-testid="sb-health-badge"
          onClick={onHealthClick}
          aria-label={`System health: ${healthLabel}`}
          aria-expanded={false}
          title="Click for per-component health detail"
        >
          SYS {healthLabel}
        </button>

        {/* Pause / Active status */}
        {isPaused ? (
          <span
            className="sb-chip sb-pause-badge"
            data-testid="sb-pause-badge"
            aria-label={`System paused — ${formatCountdown(pausedRemaining)} remaining`}
          >
            ⏸ {formatCountdown(pausedRemaining)}
          </span>
        ) : (
          <button
            className="sb-chip sb-active-badge"
            data-testid="sb-active-badge"
            onClick={onPause}
            aria-label="System active — click to pause"
          >
            ⏱ ACTIVE
          </button>
        )}

        {/* Catalyst alert — only shown when event within 2h */}
        {nearCatalyst && (
          <div className="sb-catalyst-wrapper" ref={catalystPopRef}>
            <button
              className="sb-chip sb-catalyst-badge pulsing"
              data-testid="sb-catalyst-badge"
              onClick={() => setShowCatalystPop(v => !v)}
              aria-label={`Catalyst alert: ${nearCatalyst.name} in ${formatCountdown(nearCatalyst.remaining)}`}
            >
              ⚡ {nearCatalyst.name.length > 12 ? nearCatalyst.name.slice(0, 12) + '…' : nearCatalyst.name}{' '}
              in {formatCountdown(nearCatalyst.remaining)}
            </button>

            {showCatalystPop && (
              <div className="sb-catalyst-popover" role="dialog" aria-label="Catalyst detail">
                <div className="sb-cat-pop-title">⚡ {nearCatalyst.name}</div>
                <div className="sb-cat-pop-row">
                  <span>Scheduled:</span><span>{nearCatalyst.time}</span>
                </div>
                <div className="sb-cat-pop-row">
                  <span>Impact:</span>
                  <span className={`sb-cat-impact ${nearCatalyst.impact}`}>
                    {nearCatalyst.impact.toUpperCase()}
                  </span>
                </div>
                <div className="sb-cat-pop-row">
                  <span>Countdown:</span>
                  <span style={{ color: '#FFB300', fontWeight: 700 }}>
                    {formatCountdown(nearCatalyst.remaining)}
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default StatusBar;
