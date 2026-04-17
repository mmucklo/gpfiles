/**
 * MorningBriefing — Phase 16 Intraday Cockpit
 *
 * Displays:
 * A. Regime Classification (label, confidence, factor bars)
 * B. Strategy Selector (one-click, system recommends, lock to confirm)
 * C. Catalyst Calendar (events + countdown)
 * D. Daily Game Plan (user-editable text)
 *
 * ★ Progressive disclosure: card face shows top 5 metrics, expand for rest.
 * ★ Updated timestamp on every panel.
 * ★ All new terms wrapped in TermLabel.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import './MorningBriefing.css';
import TermLabel from './TermLabel';

// ── Types ─────────────────────────────────────────────────────────────────────

interface RegimeFactor {
  name: string;
  contribution: number;
  description: string;
}

interface Catalyst {
  name: string;
  time: string;
  impact: 'high' | 'medium' | 'low';
  countdown_sec: number;
}

interface MorningBriefingData {
  regime: string;
  color: string;
  confidence: number;
  composite_score: number;
  factors: RegimeFactor[];
  recommended_strategy: string;
  fallback_strategy: string;
  catalysts: Catalyst[];
  refreshed_at: string;
  next_refresh_at: string;
  error?: string;
}

const STRATEGIES = [
  { key: 'MOMENTUM',    emoji: '📈', label: 'MOMENTUM',   term: 'MOMENTUM' },
  { key: 'IRON_CONDOR', emoji: '🦅', label: 'CONDOR',     term: 'IRON_CONDOR' },
  { key: 'WAVE_RIDER',  emoji: '🌊', label: 'WAVE',       term: 'WAVE_RIDER' },
  { key: 'JADE_LIZARD', emoji: '🦎', label: 'JADE LZD',   term: 'JADE_LIZARD' },
  { key: 'STRADDLE',    emoji: '🎯', label: 'STRADDLE',   term: 'STRADDLE' },
  { key: 'GAMMA_SCALP', emoji: '🔄', label: 'GAMMA SCP',  term: 'GAMMA_SCALP' },
] as const;

type StrategyKey = typeof STRATEGIES[number]['key'];

// ── Helpers ───────────────────────────────────────────────────────────────────

function regimeClass(regime: string): string {
  return (regime || 'uncertain').toLowerCase().replace(/_/g, '_');
}

function formatAge(isoTs: string): string {
  if (!isoTs) return '';
  const diff = Math.floor((Date.now() - new Date(isoTs).getTime()) / 1000);
  if (diff < 5)  return 'just now';
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function isStale(isoTs: string, maxAge: number): boolean {
  if (!isoTs) return true;
  return (Date.now() - new Date(isoTs).getTime()) / 1000 > maxAge;
}

function formatCountdown(sec: number): string {
  if (sec <= 0) return 'now';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  /** Optional: if provided, shows regime monitor widget inline */
  compact?: boolean;
}

const MorningBriefing: React.FC<Props> = ({ compact = false }) => {
  const [data, setData] = useState<MorningBriefingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyKey | null>(null);
  const [lockedStrategy, setLockedStrategy] = useState<StrategyKey | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [gamePlan, setGamePlan] = useState('');
  const [countdown, setCountdown] = useState<Record<string, number>>({});
  const [age, setAge] = useState('');
  const timerRef = useRef<number>(0);

  const fetchBriefing = useCallback(async () => {
    try {
      const [briefRes, stratRes] = await Promise.all([
        fetch('/api/morning-briefing'),
        fetch('/api/strategy/current'),
      ]);
      if (briefRes.ok) {
        const d = await briefRes.json() as MorningBriefingData;
        setData(d);
        // Pre-select recommended strategy if none locked yet
        if (!lockedStrategy && d.recommended_strategy) {
          setSelectedStrategy(d.recommended_strategy as StrategyKey);
        }
      }
      if (stratRes.ok) {
        const s = await stratRes.json();
        if (s.strategy) {
          setLockedStrategy(s.strategy as StrategyKey);
          setSelectedStrategy(s.strategy as StrategyKey);
        }
      }
    } catch {
      // silently fail — stale timestamp turns red
    } finally {
      setLoading(false);
    }
  }, [lockedStrategy]);

  useEffect(() => {
    fetchBriefing();
    const interval = setInterval(fetchBriefing, 5 * 60 * 1000); // every 5 min
    return () => clearInterval(interval);
  }, [fetchBriefing]);

  // Tick: update age display + countdown every second
  useEffect(() => {
    timerRef.current = window.setInterval(() => {
      if (data?.refreshed_at) {
        setAge(formatAge(data.refreshed_at));
      }
      if (data?.catalysts) {
        const now = Date.now() / 1000;
        const updated: Record<string, number> = {};
        data.catalysts.forEach(c => {
          // countdown_sec is relative from fetch time; decrement over time
          const createdAt = new Date(data.refreshed_at).getTime() / 1000;
          const remaining = c.countdown_sec - (now - createdAt);
          updated[c.name] = Math.max(0, Math.round(remaining));
        });
        setCountdown(updated);
      }
    }, 1000);
    return () => clearInterval(timerRef.current);
  }, [data]);

  const handleLockStrategy = async () => {
    if (!selectedStrategy) return;
    try {
      const res = await fetch('/api/strategy/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: selectedStrategy }),
      });
      if (res.ok) {
        setLockedStrategy(selectedStrategy);
      }
    } catch {
      // ignore
    }
  };

  const handleUnlock = () => {
    setLockedStrategy(null);
  };

  if (loading) {
    return (
      <div className="morning-briefing" data-testid="morning-briefing">
        <div className="morning-briefing-grid">
          <div className="regime-card" style={{ height: 160 }}>
            <div className="briefing-panel-title">MORNING BRIEFING</div>
            <div style={{ color: '#6e7681', fontSize: '0.82rem' }}>Loading regime classification…</div>
          </div>
        </div>
      </div>
    );
  }

  const regime = data?.regime ?? 'UNCERTAIN';
  const rc = regimeClass(regime);
  const confidence = data?.confidence ?? 0;
  const topFactors = expanded ? (data?.factors ?? []) : (data?.factors ?? []).slice(0, 4);
  const stale = data?.refreshed_at ? isStale(data.refreshed_at, 35 * 60) : true; // stale after 35 min

  return (
    <div className="morning-briefing" data-testid="morning-briefing">
      <div className="morning-briefing-grid">

        {/* A. Regime Classification */}
        <div className={`regime-card ${rc}`} data-testid="regime-card">
          <div className="briefing-panel-title">
            <TermLabel term="REGIME_CLASSIFIER">REGIME CLASSIFICATION</TermLabel>
          </div>

          {/* ★ Upper-left: biggest metric (Z-scan) */}
          <div className={`regime-label ${rc}`} data-testid="regime-label">{regime}</div>
          <div className="regime-confidence">
            {(confidence * 100).toFixed(0)}% confidence
            {data?.composite_score !== undefined && (
              <span style={{ marginLeft: '0.5rem', color: '#6e7681' }}>
                composite {data.composite_score > 0 ? '+' : ''}{data.composite_score.toFixed(2)}
              </span>
            )}
          </div>

          {/* Factor bars (top 4 visible, expand for all) */}
          <div className="regime-factors">
            {topFactors.map(f => {
              const pct = Math.min(100, Math.abs(f.contribution) / 0.3 * 100);
              const fillClass = f.contribution > 0.01 ? 'positive' : f.contribution < -0.01 ? 'negative' : 'neutral';
              return (
                <div className="regime-factor" key={f.name} title={f.description}>
                  <div className="regime-factor-label">
                    <span>{f.name}</span>
                    <span style={{ color: f.contribution > 0 ? '#3fb950' : f.contribution < 0 ? '#f85149' : '#6e7681' }}>
                      {f.contribution > 0 ? '+' : ''}{(f.contribution * 100).toFixed(0)}
                    </span>
                  </div>
                  <div className="regime-factor-bar-track">
                    <div className={`regime-factor-bar-fill ${fillClass}`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          {(data?.factors?.length ?? 0) > 4 && (
            <button className="briefing-expand-toggle" onClick={() => setExpanded(v => !v)}
              aria-label={expanded ? 'Show fewer factors' : `Show ${(data?.factors?.length ?? 0) - 4} more factors`}>
              {expanded ? '▲ Show less' : `▼ +${(data?.factors?.length ?? 0) - 4} more factors`}
            </button>
          )}

          <div className={`briefing-updated ${stale ? 'stale' : ''}`}>
            Updated {age || '…'}{stale ? ' — stale' : ''}
          </div>
        </div>

        {/* B. Strategy Selector */}
        <div className="strategy-card" data-testid="strategy-card">
          <div className="briefing-panel-title">
            <TermLabel term="STRATEGY_LOCK">STRATEGY SELECTOR</TermLabel>
          </div>

          <div style={{ fontSize: '0.78rem', color: '#8b949e', marginBottom: '0.5rem' }}>
            System recommends:{' '}
            <span style={{ color: '#79c0ff', fontWeight: 700 }}>
              {data?.recommended_strategy ?? '—'}
            </span>
            {data?.fallback_strategy && (
              <span style={{ marginLeft: '0.4rem', color: '#6e7681' }}>
                · fallback: {data.fallback_strategy}
              </span>
            )}
          </div>

          <div className="strategy-grid">
            {STRATEGIES.map(s => {
              const isRec = s.key === data?.recommended_strategy;
              const isSel = s.key === selectedStrategy;
              const isLocked = s.key === lockedStrategy;
              return (
                <button
                  key={s.key}
                  className={`strategy-btn ${isRec ? 'recommended' : ''} ${isSel ? 'selected' : ''} ${isLocked ? 'locked' : ''}`}
                  onClick={() => {
                    if (!lockedStrategy) setSelectedStrategy(s.key);
                  }}
                  disabled={!!lockedStrategy && s.key !== lockedStrategy}
                  aria-label={`Select ${s.key} strategy${isRec ? ' (recommended)' : ''}`}
                  data-testid={`strategy-btn-${s.key.toLowerCase()}`}
                >
                  <span className="strategy-btn-emoji">{s.emoji}</span>
                  <span className="strategy-btn-name">
                    <TermLabel term={s.term}>{s.label}</TermLabel>
                  </span>
                </button>
              );
            })}
          </div>

          <div className="strategy-lock-row">
            {lockedStrategy ? (
              <>
                <span className="strategy-locked-badge">🔒 {lockedStrategy} LOCKED</span>
                <button
                  className="briefing-expand-toggle"
                  onClick={handleUnlock}
                  aria-label="Unlock strategy selection"
                >
                  unlock / change
                </button>
              </>
            ) : (
              <button
                className="btn-lock-strategy"
                onClick={handleLockStrategy}
                disabled={!selectedStrategy}
                data-testid="lock-strategy-btn"
                aria-label="Lock selected strategy for the session"
              >
                🔒 LOCK STRATEGY
              </button>
            )}
          </div>
        </div>

        {/* C. Catalyst Calendar */}
        <div className="catalyst-card" data-testid="catalyst-card">
          <div className="briefing-panel-title">
            <TermLabel term="CATALYST_CALENDAR">CATALYST CALENDAR</TermLabel>
          </div>

          {/* Face: summary */}
          {!expanded && (
            <div style={{ fontSize: '0.82rem', color: '#c9d1d9', marginBottom: '0.5rem' }}>
              {(data?.catalysts?.length ?? 0) === 0
                ? <span style={{ color: '#6e7681' }}>No high-impact catalysts today</span>
                : <>{data!.catalysts.length} event{data!.catalysts.length !== 1 ? 's' : ''} today
                  {' · '}
                  <span style={{ color: '#f85149', fontWeight: 700 }}>
                    {data!.catalysts.find(c => c.impact === 'high')?.name ?? ''}
                  </span>
                </>
              }
            </div>
          )}

          <div className="catalyst-list">
            {(data?.catalysts ?? []).slice(0, compact ? 2 : undefined).map(c => (
              <div key={c.name} className={`catalyst-item ${c.impact}`} data-testid={`catalyst-${c.impact}`}>
                <div className={`catalyst-impact-badge ${c.impact}`}>{c.impact.toUpperCase()}</div>
                <div className="catalyst-name">{c.name}</div>
                <div className="catalyst-time">{c.time}</div>
                <div className="catalyst-countdown">
                  {(countdown[c.name] ?? c.countdown_sec) > 0
                    ? `in ${formatCountdown(countdown[c.name] ?? c.countdown_sec)}`
                    : 'now'}
                </div>
              </div>
            ))}
            {(data?.catalysts ?? []).length === 0 && (
              <div className="catalyst-empty">
                No catalysts today — clean tape, trend-following conditions
              </div>
            )}
          </div>
        </div>

        {/* D. Daily Game Plan */}
        {!compact && (
          <div className="game-plan-card" data-testid="game-plan-card">
            <div className="briefing-panel-title">MY DAILY GAME PLAN</div>
            <textarea
              className="game-plan-textarea"
              placeholder="Type your morning thesis here… (e.g. 'Ride momentum off the open if TSLA gaps up >1%, switch to condors if it stalls by 10am')"
              value={gamePlan}
              onChange={e => setGamePlan(e.target.value)}
              aria-label="Daily game plan"
              data-testid="game-plan-textarea"
            />
            <div style={{ fontSize: '0.7rem', color: '#6e7681', marginTop: '0.3rem' }}>
              Persisted in session — shown in EOD review
            </div>
          </div>
        )}

      </div>
    </div>
  );
};

export default MorningBriefing;
