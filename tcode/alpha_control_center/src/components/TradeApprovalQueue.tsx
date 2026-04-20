/**
 * TradeApprovalQueue — Phase 16 Intraday Cockpit
 *
 * The primary interaction surface during market hours.
 * Signals flow in as proposals; user approves/skips/adjusts.
 *
 * ★ Flash amber on arrival, fade to background over 2s
 * ★ 3-second countdown on Execute for LIVE mode
 * ★ Proposals auto-expire after 60s
 * ★ Updated timestamp, stale indicator
 * ★ All new terms wrapped in TermLabel
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import './TradeApprovalQueue.css';
import TermLabel from './TermLabel';

// ── Types ─────────────────────────────────────────────────────────────────────

interface LegSpec {
  strike: number;
  type: 'CALL' | 'PUT';
  action: 'BUY' | 'SELL';
  quantity: number;
  fill_price: number | null;
}

interface TradeProposal {
  id: string;
  ts_created: string;
  ts_expires: string;
  status: 'pending' | 'executed' | 'skipped' | 'expired' | 'adjusted' | 'execute_failed';
  strategy: string;
  direction: string;
  legs: LegSpec[];
  entry_price: number;
  stop_price: number;
  target_price: number;
  kelly_fraction: number;
  quantity: number;
  confidence: number;
  regime_snapshot: { regime?: string; confidence?: number } | null;
  signals_contributing: string[];
}

interface QueueData {
  proposals: TradeProposal[];
  stats: { pending: number; executed: number; skipped: number; expired: number };
  updated_at: string;
}

type FilterMode = 'all' | 'pending' | 'executed' | 'skipped';

// ── Helpers ───────────────────────────────────────────────────────────────────

const STRATEGY_META: Record<string, { emoji: string; term: string }> = {
  MOMENTUM:    { emoji: '📈', term: 'MOMENTUM' },
  IRON_CONDOR: { emoji: '🦅', term: 'IRON_CONDOR' },
  WAVE_RIDER:  { emoji: '🌊', term: 'WAVE_RIDER' },
  JADE_LIZARD: { emoji: '🦎', term: 'JADE_LIZARD' },
  STRADDLE:    { emoji: '🎯', term: 'STRADDLE' },
  GAMMA_SCALP: { emoji: '🔄', term: 'GAMMA_SCALP' },
};

function getStrategyMeta(key: string) {
  return STRATEGY_META[key] ?? { emoji: '📊', term: 'TRADE_PROPOSAL' };
}

function formatTs(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function ttlPct(created: string, expires: string): number {
  const total = new Date(expires).getTime() - new Date(created).getTime();
  const remaining = new Date(expires).getTime() - Date.now();
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (remaining / total) * 100));
}

function buildContractLabel(legs: LegSpec[]): string {
  if (!legs || legs.length === 0) return 'Unknown';
  if (legs.length === 1) {
    const l = legs[0];
    return `$${l.strike} ${l.type}`;
  }
  if (legs.length === 4) return 'Iron Condor';
  if (legs.length === 3) return 'Jade Lizard';
  if (legs.length === 2) return `${legs[0].type} Spread`;
  return `${legs.length}-Leg Combo`;
}

function formatAge(isoTs: string): string {
  if (!isoTs) return '';
  const diff = Math.floor((Date.now() - new Date(isoTs).getTime()) / 1000);
  if (diff < 5)  return 'just now';
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ── ProposalCard ──────────────────────────────────────────────────────────────

interface CardProps {
  proposal: TradeProposal;
  isLive: boolean;
  onExecute: (id: string, qty: number) => Promise<void>;
  onSkip: (id: string) => Promise<void>;
  onAdjust: (id: string, qty: number) => Promise<void>;
  onToast: (msg: string, type: 'success' | 'error') => void;
}

const ProposalCard: React.FC<CardProps> = ({ proposal: p, isLive, onExecute, onSkip, onAdjust, onToast }) => {
  const [showAdjust, setShowAdjust] = useState(false);
  const [adjustQty, setAdjustQty] = useState(p.quantity);
  const [executing, setExecuting] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(true);
  const [expiryPct, setExpiryPct] = useState(ttlPct(p.ts_created, p.ts_expires));
  const countdownRef = useRef<number>(0);

  // Remove "new" flash class after 2s
  useEffect(() => {
    const t = setTimeout(() => setIsNew(false), 2000);
    return () => clearTimeout(t);
  }, []);

  // Tick expiry bar
  useEffect(() => {
    if (p.status !== 'pending') return;
    const t = setInterval(() => setExpiryPct(ttlPct(p.ts_created, p.ts_expires)), 500);
    return () => clearInterval(t);
  }, [p.ts_created, p.ts_expires, p.status]);

  const handleExecute = async () => {
    if (isLive) {
      // 3-second countdown for LIVE mode
      setCountdown(3);
      countdownRef.current = window.setInterval(() => {
        setCountdown(v => {
          if (v !== null && v <= 1) {
            clearInterval(countdownRef.current);
            setCountdown(null);
            doExecute();
          }
          return v !== null ? v - 1 : null;
        });
      }, 1000);
    } else {
      doExecute();
    }
  };

  const doExecute = async () => {
    setExecuting(true);
    try {
      await onExecute(p.id, p.quantity);
      onToast(`Order submitted — ${p.strategy} ${buildContractLabel(p.legs)}`, 'success');
    } catch (err) {
      onToast(`Execute failed: ${String(err)}`, 'error');
    } finally {
      setExecuting(false);
    }
  };

  const handleAdjustExecute = async () => {
    setExecuting(true);
    try {
      await onAdjust(p.id, adjustQty);
      onToast(`Order submitted (adjusted) — ${p.strategy} ${buildContractLabel(p.legs)}`, 'success');
    } catch (err) {
      onToast(`Execute failed: ${String(err)}`, 'error');
    } finally {
      setExecuting(false);
      setShowAdjust(false);
    }
  };

  const cancelCountdown = () => {
    clearInterval(countdownRef.current);
    setCountdown(null);
  };

  const isPending = p.status === 'pending';
  const isExecuted = p.status === 'executed' || p.status === 'adjusted';
  const isExpired = p.status === 'expired';
  const isFailed = p.status === 'execute_failed';

  const direction = p.direction?.toUpperCase();
  const directionClass = direction === 'BULLISH' ? 'bullish' : direction === 'BEARISH' ? 'bearish' : 'neutral';
  const directionArrow = direction === 'BULLISH' ? '▲' : direction === 'BEARISH' ? '▼' : '●';

  const riskDollar = Math.round((p.entry_price - p.stop_price) * 100 * p.quantity);
  const rewardDollar = Math.round((p.target_price - p.entry_price) * 100 * p.quantity);
  const rrRatio = p.stop_price > 0 && p.entry_price > p.stop_price
    ? ((p.target_price - p.entry_price) / (p.entry_price - p.stop_price)).toFixed(1)
    : '—';
  const notionalRisk = p.entry_price * 100 * p.quantity;
  const meta = getStrategyMeta(p.strategy);
  const confidencePct = Math.round(p.confidence * 100);

  // Confidence ring color
  const confColor = confidencePct >= 75 ? '#3fb950' : confidencePct >= 55 ? '#d29922' : '#f85149';

  return (
    <div
      className={`proposal-card ${p.status} ${isNew && isPending ? 'proposal-card-new' : ''}`}
      data-testid={`proposal-card-${p.id}`}
      role="article"
      aria-label={`Trade proposal: ${p.strategy} ${p.direction} ${buildContractLabel(p.legs)}`}
    >
      {/* Expiry bar */}
      {isPending && (
        <div className="proposal-expiry-bar" style={{ width: `${expiryPct}%` }} />
      )}

      <div className="proposal-card-top">
        {/* Strategy badge */}
        <div className="proposal-strategy-badge">
          <span className="proposal-strategy-icon">{meta.emoji}</span>
          <span className="proposal-strategy-label">
            <TermLabel term={meta.term}>{p.strategy.replace('_', ' ')}</TermLabel>
          </span>
        </div>

        {/* Direction */}
        <div className={`proposal-direction ${directionClass}`}>{directionArrow} {direction}</div>

        {/* Main meta */}
        <div className="proposal-meta">
          <div className="proposal-contract" data-testid="proposal-contract">
            TSLA <span>{buildContractLabel(p.legs)}</span>
            {p.legs?.[0] && ` ${p.legs[0].action}`}
          </div>

          <div className="proposal-price-row">
            <span>Entry: <span className="val">${p.entry_price?.toFixed(2) ?? '—'}</span></span>
            <span className="sl">Stop: ${p.stop_price?.toFixed(2) ?? '—'}</span>
            <span className="tp">Target: ${p.target_price?.toFixed(2) ?? '—'}</span>
          </div>

          {/* Reward:Risk bar */}
          {rewardDollar > 0 && riskDollar > 0 && (
            <div className="proposal-rr-bar">
              <div className="rr-bar-track reward" style={{ width: `${Math.min(80, rewardDollar / (rewardDollar + riskDollar) * 120)}px` }} />
              <div className="rr-bar-track risk" style={{ width: `${Math.min(40, riskDollar / (rewardDollar + riskDollar) * 80)}px` }} />
              <span className="rr-label">{rrRatio}:1 R:R</span>
            </div>
          )}

          <div className="proposal-sizing-row">
            {/* Confidence ring */}
            <div
              className="confidence-ring"
              style={{ background: `conic-gradient(${confColor} ${confidencePct}%, #21262d ${confidencePct}%)` }}
              title={`Confidence: ${confidencePct}%`}
              data-testid="confidence-ring"
            >
              <span style={{ background: '#161b22', borderRadius: '50%', width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {confidencePct}%
              </span>
            </div>

            <span>
              <TermLabel term="KELLY_FRACTION">Kelly</TermLabel>{' '}
              <strong style={{ color: '#c9d1d9' }}>{p.quantity} contracts</strong>
              <span style={{ marginLeft: '0.35rem', color: '#8b949e' }}>(${notionalRisk.toLocaleString()} risk)</span>
            </span>
          </div>
        </div>
      </div>

      {/* Signal source badges */}
      {p.signals_contributing?.length > 0 && (
        <div className="proposal-signals" data-testid="signal-badges">
          {p.signals_contributing.map(s => (
            <span key={s} className="signal-badge">{s}</span>
          ))}
          {p.regime_snapshot?.regime && (
            <span className="signal-badge" style={{ borderColor: '#79c0ff', color: '#79c0ff' }}>
              <TermLabel term="REGIME_CLASSIFIER">{p.regime_snapshot.regime}</TermLabel>
            </span>
          )}
        </div>
      )}

      <div style={{ fontSize: '0.7rem', color: '#6e7681', marginBottom: '0.5rem' }}>
        Proposed {formatTs(p.ts_created)}
        {isPending && expiryPct > 0 && (
          <span style={{ marginLeft: '0.5rem', color: expiryPct < 30 ? '#f85149' : '#d29922' }}>
            · expires in {Math.max(0, Math.round((new Date(p.ts_expires).getTime() - Date.now()) / 1000))}s
          </span>
        )}
        {isExpired && <span style={{ marginLeft: '0.5rem', color: '#f85149' }}>· expired — price moved</span>}
      </div>

      {/* Adjust panel */}
      {showAdjust && isPending && (
        <div className="adjust-panel" data-testid="adjust-panel">
          <div className="adjust-row">
            <span className="adjust-label">Contracts:</span>
            <input
              type="range" className="adjust-slider"
              min={1} max={10} value={adjustQty}
              onChange={e => setAdjustQty(Number(e.target.value))}
              aria-label="Adjust number of contracts"
            />
            <span className="adjust-val">{adjustQty}</span>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
            <button className="btn-execute" style={{ flex: 1 }} onClick={handleAdjustExecute} disabled={executing}>
              {executing ? '…' : 'EXECUTE ADJUSTED'}
            </button>
            <button className="btn-skip" onClick={() => setShowAdjust(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Action buttons */}
      {isPending && !showAdjust && (
        <div className="proposal-actions" data-testid="proposal-actions">
          <button
            className="btn-execute"
            onClick={countdown !== null ? cancelCountdown : handleExecute}
            disabled={executing}
            aria-label={`Execute ${p.strategy} trade for ${p.quantity} contracts`}
            data-testid={`execute-btn-${p.id}`}
          >
            {countdown !== null ? (
              <>
                <div className="btn-execute-countdown">{countdown}</div>
                TAP TO CANCEL
              </>
            ) : executing ? '…' : '⚡ EXECUTE'}
          </button>
          <button className="btn-skip" onClick={() => onSkip(p.id)}
            aria-label="Skip this proposal"
            data-testid={`skip-btn-${p.id}`}>
            SKIP
          </button>
          <button className="btn-adjust" onClick={() => setShowAdjust(true)}
            aria-label="Adjust parameters before executing"
            data-testid={`adjust-btn-${p.id}`}>
            ✏ ADJUST
          </button>
        </div>
      )}

      {/* Status overlay for non-pending */}
      {isExecuted && (
        <div className="proposal-status-overlay executed">✓ EXECUTED</div>
      )}
      {isExpired && (
        <div className="proposal-status-overlay expired">EXPIRED</div>
      )}
      {isFailed && (
        <div className="proposal-status-overlay execute_failed">✗ FAILED</div>
      )}
    </div>
  );
};

// ── TradeApprovalQueue ────────────────────────────────────────────────────────

interface TAQProps {
  brokerMode?: string;
}

const TradeApprovalQueue: React.FC<TAQProps> = ({ brokerMode }) => {
  const [queueData, setQueueData] = useState<QueueData | null>(null);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<FilterMode>('all');
  const [age, setAge] = useState('');
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const toastTimerRef = useRef<number>(0);
  const prevIds = useRef<Set<string>>(new Set());

  const isLive = brokerMode === 'IBKR_LIVE';

  const fetchQueue = useCallback(async () => {
    try {
      const res = await fetch('/api/trades/proposed');
      if (res.ok) {
        const data = await res.json() as QueueData;
        // Flash-on-insert: detect IDs not seen in previous poll
        const incoming = new Set((data.proposals ?? []).map((p: TradeProposal) => p.id));
        const fresh = new Set([...incoming].filter(id => !prevIds.current.has(id)));
        if (fresh.size > 0) {
          setNewIds(fresh);
          setTimeout(() => setNewIds(new Set()), 2000);
        }
        prevIds.current = incoming;
        setQueueData(data);
      }
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, 5000);
    return () => clearInterval(interval);
  }, [fetchQueue]);

  // Age ticker
  useEffect(() => {
    const t = setInterval(() => {
      if (queueData?.updated_at) setAge(formatAge(queueData.updated_at));
    }, 1000);
    return () => clearInterval(t);
  }, [queueData?.updated_at]);

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type });
    clearTimeout(toastTimerRef.current);
    if (type === 'success') {
      toastTimerRef.current = window.setTimeout(() => setToast(null), 5000);
    }
    // error toasts persist until dismissed
  };

  const handleExecute = async (id: string, qty: number) => {
    const resp = await fetch(`/api/trades/proposed/${id}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quantity: qty }),
    });
    const data = await resp.json().catch(() => ({}));
    fetchQueue();
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
  };

  const handleSkip = async (id: string) => {
    await fetch(`/api/trades/proposed/${id}/skip`, { method: 'POST' });
    fetchQueue();
  };

  const handleAdjust = async (id: string, qty: number) => {
    const resp = await fetch(`/api/trades/proposed/${id}/adjust`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quantity: qty }),
    });
    const data = await resp.json().catch(() => ({}));
    fetchQueue();
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
  };

  const proposals = queueData?.proposals ?? [];
  const stats = queueData?.stats ?? { pending: 0, executed: 0, skipped: 0, expired: 0 };
  const stale = queueData?.updated_at ? (Date.now() - new Date(queueData.updated_at).getTime()) / 1000 > 15 : false;

  const filtered = filter === 'all' ? proposals :
    filter === 'executed' ? proposals.filter(p => p.status === 'executed' || p.status === 'adjusted') :
    proposals.filter(p => p.status === filter);

  return (
    <div className="taq-container" data-testid="trade-approval-queue">
      {/* Header */}
      <div className="taq-header">
        <div>
          <h3 style={{ margin: 0, fontSize: '0.9rem', color: '#c9d1d9', fontWeight: 700 }}>
            <TermLabel term="APPROVAL_QUEUE">TRADE APPROVAL QUEUE</TermLabel>
          </h3>
          <div className={`taq-updated ${stale ? 'stale' : ''}`}>
            Updated {age || '…'}{stale ? ' — stale' : ''}
          </div>
        </div>

        <div className="taq-stats">
          <div className="taq-stat">
            <span>Pending:</span>
            <span className="taq-stat-val pending">{stats.pending}</span>
          </div>
          <div className="taq-stat">
            <span>Executed:</span>
            <span className="taq-stat-val executed">{stats.executed}</span>
          </div>
          <div className="taq-stat">
            <span>Skipped:</span>
            <span className="taq-stat-val skipped">{stats.skipped}</span>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="taq-filter-row">
        {(['all', 'pending', 'executed', 'skipped'] as FilterMode[]).map(f => (
          <button
            key={f}
            className={`taq-filter-btn ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}
            aria-label={`Filter: ${f}`}
            data-testid={`filter-${f}`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Cards */}
      {filtered.length === 0 ? (
        <div className="taq-empty" data-testid="taq-empty">
          <div className="taq-empty-icon">📋</div>
          {filter === 'pending'
            ? 'No pending proposals — awaiting next signal'
            : filter === 'executed'
            ? 'No executed trades yet today'
            : 'No proposals yet — engine will populate this queue during market hours'}
        </div>
      ) : (
        filtered.map(p => (
          <div key={p.id} className={newIds.has(p.id) ? 'flash-insert' : ''}>
            <ProposalCard
              proposal={p}
              isLive={isLive}
              onExecute={handleExecute}
              onSkip={handleSkip}
              onAdjust={handleAdjust}
              onToast={showToast}
            />
          </div>
        ))
      )}

      {/* Execute result toast */}
      {toast && (
        <div
          data-testid="execute-toast"
          role="alert"
          style={{
            position: 'fixed', bottom: '20px', right: '20px', zIndex: 9999,
            backgroundColor: toast.type === 'success' ? '#1a7f37' : '#8b1a1a',
            color: '#fff', padding: '0.75rem 1.2rem', borderRadius: '8px',
            fontSize: '13px', fontWeight: 600, boxShadow: '0 2px 8px rgba(0,0,0,0.5)',
            display: 'flex', alignItems: 'center', gap: '0.75rem', maxWidth: '360px',
          }}
        >
          <span style={{ flex: 1 }}>{toast.msg}</span>
          {toast.type === 'error' && (
            <button
              onClick={() => setToast(null)}
              style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '16px', lineHeight: 1 }}
              aria-label="Dismiss"
            >✕</button>
          )}
        </div>
      )}
    </div>
  );
};

export default TradeApprovalQueue;
