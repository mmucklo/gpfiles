/**
 * RejectedSignalDetailModal — Phase 14.3
 *
 * Opens on row click in RejectedSignalsPanel. Fetches /api/signals/rejections/:id
 * on mount and renders 5 collapsible sections:
 *   A. Signal Meta
 *   B. Why It Was Rejected
 *   C. Market Context at Rejection
 *   D. Chain Snapshot
 *   E. Actions
 */
import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import TermLabel from './TermLabel';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ChainRow {
  strike: number;
  option_type: string;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  volume: number | null;
  open_interest: number | null;
  bid: number | null;
  ask: number | null;
  is_candidate: boolean | null;
  candidate_filter_killed: string | null;
}

interface StrikeBreakdownRow {
  strike: number;
  option_type: string;
  score: number | null;
  delta: number | null;
  filter_killed: string | null;
  filter_reason: string | null;
}

interface RegimeContext {
  macro_regime?: string;
  correlation_regime?: string;
  [key: string]: unknown;
}

export interface RejectionDetail {
  id: number;
  ts: string;
  model_id: string;
  direction: string | null;
  confidence: number | null;
  ticker: string | null;
  option_type: string | null;
  expiration_date: string | null;
  target_strike_attempted: number | null;
  spot_at_rejection: number | null;
  reason_code: string | null;
  reason_detail: string | null;
  chain_snapshot: ChainRow[] | string | null;
  strike_selector_breakdown: StrikeBreakdownRow[] | string | null;
  archetype: string | null;
  chop_regime_at_rejection: string | null;
  regime_context: RegimeContext | string | null;
  // Phase 14.1 fallback fields
  model: string;
  opt_type: string;
  reason: string;
  expiry: string | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const REASON_COLOR: Record<string, string> = {
  STRIKE_SELECT_FAIL: '#f85149',
  LIQUIDITY_REJECT: '#d29922',
  CHOP_BLOCK: '#79c0ff',
  GREEKS_UNAVAILABLE: '#a371f7',
  SPOT_VARIANCE: '#f0883e',
};

function reasonColor(code: string | null): string {
  if (!code) return '#8b949e';
  const upper = code.toUpperCase();
  for (const [key, color] of Object.entries(REASON_COLOR)) {
    if (upper.includes(key)) return color;
  }
  return '#8b949e';
}

function directionColor(dir: string | null): string {
  if (!dir) return '#8b949e';
  return dir.toUpperCase() === 'BULLISH' ? '#3fb950' : '#f85149';
}

function fmtTs(ts: string): string {
  try {
    return new Date(ts.replace(' ', 'T') + 'Z').toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZoneName: 'short',
    });
  } catch {
    return ts;
  }
}

function fmtNum(n: number | null | undefined, decimals = 2): string {
  if (n == null) return 'not captured';
  return n.toFixed(decimals);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return 'not captured';
  return `${(n * 100).toFixed(1)}%`;
}

// ── Collapsible section wrapper ───────────────────────────────────────────────

interface SectionProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  badge?: React.ReactNode;
}

const Section = ({ title, children, defaultOpen = true, badge }: SectionProps) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ marginBottom: 16, border: '1px solid #30363d', borderRadius: 6 }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 8,
          padding: '8px 12px', background: '#161b22', border: 'none',
          borderRadius: open ? '6px 6px 0 0' : 6, cursor: 'pointer',
          color: '#c9d1d9', fontSize: '0.78rem', fontWeight: 700, textAlign: 'left',
        }}
        aria-expanded={open}
      >
        <span style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', fontSize: '0.65rem' }}>▶</span>
        <span style={{ flex: 1 }}>{title}</span>
        {badge}
      </button>
      {open && (
        <div style={{ padding: '12px 14px', background: '#0d1117', borderRadius: '0 0 6px 6px' }}>
          {children}
        </div>
      )}
    </div>
  );
};

// ── Not-captured placeholder ──────────────────────────────────────────────────

const NotCaptured = ({ msg = 'not captured' }: { msg?: string }) => (
  <span
    style={{ color: '#6e7681', fontStyle: 'italic' }}
    title="This field was not written by the publisher at rejection time. Pre-Phase-14.3 rejections lack this context."
  >
    {msg}
  </span>
);

// ── Section A: Signal Meta ────────────────────────────────────────────────────

const SignalMetaSection = ({ d }: { d: RejectionDetail }) => (
  <Section title="A. Signal Meta">
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 24px', fontSize: '0.78rem', color: '#c9d1d9' }}>
      <div>
        <span style={{ color: '#8b949e' }}>Model: </span>
        {d.model_id ? <TermLabel term={d.model_id.toUpperCase()} badge>{d.model_id}</TermLabel> : <NotCaptured />}
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Direction: </span>
        {d.direction
          ? <span style={{ color: directionColor(d.direction), fontWeight: 700 }}>{d.direction}</span>
          : <NotCaptured />}
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Confidence: </span>
        {d.confidence != null
          ? <span style={{ color: '#e6edf3', fontWeight: 700 }}>{fmtPct(d.confidence)}</span>
          : <NotCaptured />}
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Ticker: </span>
        <span>{d.ticker || 'TSLA'}</span>
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Option type: </span>
        {d.option_type
          ? <span style={{ color: d.option_type === 'CALL' ? '#3fb950' : '#f85149', fontWeight: 700 }}>{d.option_type}</span>
          : <NotCaptured />}
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Expiration: </span>
        {d.expiration_date || d.expiry || <NotCaptured />}
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Archetype: </span>
        {d.archetype
          ? <TermLabel term={d.archetype} badge>{d.archetype}</TermLabel>
          : <NotCaptured />}
      </div>
      <div>
        <span style={{ color: '#8b949e' }}>Rejected at: </span>
        <span style={{ color: '#8b949e' }}>{fmtTs(d.ts)}</span>
      </div>
    </div>
  </Section>
);

// ── Section B: Why It Was Rejected ───────────────────────────────────────────

const RejectionReasonSection = ({ d }: { d: RejectionDetail }) => {
  const rc = d.reason_code || d.reason;
  const rdColor = reasonColor(rc);

  // Parse strike breakdown
  let breakdown: StrikeBreakdownRow[] = [];
  if (d.strike_selector_breakdown) {
    if (Array.isArray(d.strike_selector_breakdown)) {
      breakdown = d.strike_selector_breakdown as StrikeBreakdownRow[];
    } else if (typeof d.strike_selector_breakdown === 'string') {
      try { breakdown = JSON.parse(d.strike_selector_breakdown); } catch { /* leave empty */ }
    }
  }

  const isStrikeFail = rc?.toUpperCase().includes('STRIKE_SELECT_FAIL') || rc?.includes('no_strike_passed');
  const isLiquidity  = rc?.toUpperCase().includes('LIQUIDITY_REJECT');
  const isChopBlock  = rc?.toUpperCase().includes('CHOP_BLOCK');
  const isGreeks     = rc?.toUpperCase().includes('GREEKS_UNAVAILABLE');

  return (
    <Section title="B. Why It Was Rejected">
      <div style={{ fontSize: '0.78rem', color: '#c9d1d9' }}>
        {/* Reason code chip + TermLabel */}
        <div style={{ marginBottom: 10 }}>
          <span
            style={{
              display: 'inline-block', padding: '2px 10px', borderRadius: 12,
              background: `${rdColor}22`, border: `1px solid ${rdColor}77`,
              color: rdColor, fontWeight: 700, fontSize: '0.8rem', marginRight: 8,
            }}
          >
            {rc}
          </span>
          {rc && (
            <TermLabel term={rc.split(':')[0].toUpperCase()} />
          )}
        </div>

        {/* Full reason_detail — verbatim */}
        {d.reason_detail ? (
          <div style={{
            background: '#161b22', border: '1px solid #30363d', borderRadius: 4,
            padding: '8px 12px', fontFamily: 'monospace', fontSize: '0.75rem',
            color: '#e6edf3', lineHeight: 1.5, whiteSpace: 'pre-wrap', marginBottom: 12,
          }}>
            {d.reason_detail}
          </div>
        ) : (
          <div style={{ marginBottom: 12, color: '#6e7681', fontStyle: 'italic' }}>
            No extended reason captured (pre-Phase-14.3 rejection)
          </div>
        )}

        {/* STRIKE_SELECT_FAIL: candidate strike breakdown table */}
        {isStrikeFail && breakdown.length > 0 && (
          <div>
            <div style={{ color: '#8b949e', fontSize: '0.72rem', marginBottom: 6 }}>
              Candidate strike evaluation ({breakdown.length} strikes assessed):
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #30363d', color: '#8b949e' }}>
                  <th style={{ textAlign: 'left', padding: '3px 6px' }}>Strike</th>
                  <th style={{ textAlign: 'left', padding: '3px 6px' }}>Type</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px' }}>Delta</th>
                  <th style={{ textAlign: 'right', padding: '3px 6px' }}>Score</th>
                  <th style={{ textAlign: 'left', padding: '3px 6px' }}>Eliminated by</th>
                  <th style={{ textAlign: 'left', padding: '3px 6px' }}>Detail</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.map((row, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(48,54,61,0.5)', color: '#c9d1d9' }}>
                    <td style={{ padding: '3px 6px', fontFamily: 'monospace' }}>${row.strike}</td>
                    <td style={{ padding: '3px 6px', color: row.option_type === 'CALL' ? '#3fb950' : '#f85149' }}>{row.option_type}</td>
                    <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace' }}>{row.delta != null ? row.delta.toFixed(3) : '—'}</td>
                    <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace' }}>{row.score != null ? row.score.toFixed(3) : '—'}</td>
                    <td style={{ padding: '3px 6px', color: '#f0883e' }}>{row.filter_killed || '—'}</td>
                    <td style={{ padding: '3px 6px', color: '#8b949e', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.filter_reason || ''}>{row.filter_reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* LIQUIDITY_REJECT: floors */}
        {isLiquidity && (
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4, padding: '8px 12px', fontSize: '0.72rem' }}>
            <div style={{ color: '#8b949e', marginBottom: 6 }}>Liquidity thresholds at rejection time:</div>
            <div style={{ color: '#c9d1d9' }}>
              See reason_detail above for actual vs required values.{' '}
              <TermLabel term="LIQUIDITY_REJECT" />
            </div>
          </div>
        )}

        {/* CHOP_BLOCK: show chop regime */}
        {isChopBlock && (
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4, padding: '8px 12px', fontSize: '0.72rem' }}>
            <div style={{ color: '#8b949e', marginBottom: 4 }}>Chop regime at rejection:</div>
            {d.chop_regime_at_rejection
              ? <span style={{ color: '#79c0ff', fontWeight: 700 }}>{d.chop_regime_at_rejection}</span>
              : <NotCaptured />}
            {' '}<TermLabel term="CHOP_BLOCK" />
          </div>
        )}

        {/* GREEKS_UNAVAILABLE */}
        {isGreeks && (
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4, padding: '8px 12px', fontSize: '0.72rem', color: '#c9d1d9' }}>
            <TermLabel term="GREEKS_UNAVAILABLE" /> — greeks could not be computed or retrieved for any candidate strike.
          </div>
        )}
      </div>
    </Section>
  );
};

// ── Section C: Market Context ─────────────────────────────────────────────────

const MarketContextSection = ({ d }: { d: RejectionDetail }) => {
  let regime: RegimeContext = {};
  if (d.regime_context) {
    if (typeof d.regime_context === 'object') {
      regime = d.regime_context as RegimeContext;
    } else if (typeof d.regime_context === 'string') {
      try { regime = JSON.parse(d.regime_context); } catch { /* leave empty */ }
    }
  }

  return (
    <Section title="C. Market Context at Rejection">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 24px', fontSize: '0.78rem', color: '#c9d1d9' }}>
        <div>
          <span style={{ color: '#8b949e' }}>Spot at rejection: </span>
          {d.spot_at_rejection != null
            ? <span style={{ fontFamily: 'monospace', fontWeight: 700 }}>${fmtNum(d.spot_at_rejection, 2)}</span>
            : <NotCaptured />}
        </div>
        <div>
          <span style={{ color: '#8b949e' }}>Chop regime: </span>
          {d.chop_regime_at_rejection
            ? <><span style={{ color: '#79c0ff', fontWeight: 700 }}>{d.chop_regime_at_rejection}</span>{' '}<TermLabel term="CHOP_BLOCK" /></>
            : <NotCaptured />}
        </div>
        <div>
          <span style={{ color: '#8b949e' }}>Macro regime: </span>
          {regime.macro_regime
            ? <><span style={{ fontWeight: 700 }}>{String(regime.macro_regime)}</span>{' '}<TermLabel term="MACRO_REGIME" /></>
            : <NotCaptured />}
        </div>
        <div>
          <span style={{ color: '#8b949e' }}>Correlation regime: </span>
          {regime.correlation_regime
            ? <><span style={{ fontWeight: 700 }}>{String(regime.correlation_regime)}</span>{' '}<TermLabel term="CORRELATION_REGIME" /></>
            : <NotCaptured />}
        </div>
        {d.target_strike_attempted != null && (
          <div>
            <span style={{ color: '#8b949e' }}>Target strike attempted: </span>
            <span style={{ fontFamily: 'monospace' }}>${fmtNum(d.target_strike_attempted, 0)}</span>
          </div>
        )}
      </div>

      {/* Extra regime keys */}
      {Object.keys(regime).filter(k => !['macro_regime', 'correlation_regime'].includes(k)).length > 0 && (
        <div style={{ marginTop: 10, fontSize: '0.72rem', color: '#8b949e' }}>
          <div style={{ marginBottom: 4 }}>Additional regime context:</div>
          <pre style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4, padding: '6px 10px', overflowX: 'auto', fontSize: '0.68rem', color: '#c9d1d9', margin: 0 }}>
            {JSON.stringify(regime, null, 2)}
          </pre>
        </div>
      )}
    </Section>
  );
};

// ── Section D: Chain Snapshot ─────────────────────────────────────────────────

const ChainSnapshotSection = ({ d }: { d: RejectionDetail }) => {
  const [sortBy, setSortBy] = useState<'oi' | 'volume' | 'delta'>('oi');
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  let rows: ChainRow[] = [];
  if (d.chain_snapshot) {
    if (Array.isArray(d.chain_snapshot)) {
      rows = d.chain_snapshot as ChainRow[];
    } else if (typeof d.chain_snapshot === 'string') {
      try { rows = JSON.parse(d.chain_snapshot); } catch { /* leave empty */ }
    }
  }

  const sorted = [...rows].sort((a, b) => {
    const va = sortBy === 'oi' ? (a.open_interest ?? 0) : sortBy === 'volume' ? (a.volume ?? 0) : (a.delta ?? 0);
    const vb = sortBy === 'oi' ? (b.open_interest ?? 0) : sortBy === 'volume' ? (b.volume ?? 0) : (b.delta ?? 0);
    return (va - vb) * sortDir;
  });

  const toggleSort = (col: 'oi' | 'volume' | 'delta') => {
    if (sortBy === col) setSortDir(d => d === 1 ? -1 : 1);
    else { setSortBy(col); setSortDir(-1); }
  };

  const sortLabel = (col: string) => sortBy === col ? (sortDir === -1 ? ' ▼' : ' ▲') : '';

  return (
    <Section
      title="D. Chain Snapshot"
      badge={rows.length > 0 ? <span style={{ fontSize: '0.7rem', color: '#8b949e', marginLeft: 'auto' }}>{rows.length} rows (point-in-time, read-only)</span> : undefined}
    >
      {rows.length === 0 ? (
        d.chain_snapshot == null ? (
          <div style={{ color: '#6e7681', fontStyle: 'italic', fontSize: '0.78rem' }}>
            Chain snapshot not captured for this rejection (pre-Phase-14.3).
          </div>
        ) : (
          <div style={{ color: '#6e7681', fontStyle: 'italic', fontSize: '0.78rem' }}>
            Chain snapshot is empty.
          </div>
        )
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.70rem', minWidth: 700 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #30363d', color: '#8b949e' }}>
                <th style={{ textAlign: 'left', padding: '3px 6px' }}>Strike</th>
                <th style={{ textAlign: 'left', padding: '3px 6px' }}>Type</th>
                <th style={{ textAlign: 'right', padding: '3px 6px', cursor: 'pointer' }} onClick={() => toggleSort('delta')}>Delta{sortLabel('delta')}</th>
                <th style={{ textAlign: 'right', padding: '3px 6px' }}>Gamma</th>
                <th style={{ textAlign: 'right', padding: '3px 6px' }}>Theta</th>
                <th style={{ textAlign: 'right', padding: '3px 6px' }}>Vega</th>
                <th style={{ textAlign: 'right', padding: '3px 6px', cursor: 'pointer' }} onClick={() => toggleSort('volume')}>Volume{sortLabel('volume')}</th>
                <th style={{ textAlign: 'right', padding: '3px 6px', cursor: 'pointer' }} onClick={() => toggleSort('oi')}>OI{sortLabel('oi')}</th>
                <th style={{ textAlign: 'right', padding: '3px 6px' }}>Bid</th>
                <th style={{ textAlign: 'right', padding: '3px 6px' }}>Ask</th>
                <th style={{ textAlign: 'left', padding: '3px 6px' }}>Candidate?</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr
                  key={i}
                  style={{
                    borderBottom: '1px solid rgba(48,54,61,0.4)',
                    background: row.is_candidate ? 'rgba(210,153,34,0.05)' : undefined,
                  }}
                >
                  <td style={{ padding: '3px 6px', fontFamily: 'monospace', color: '#c9d1d9' }}>${row.strike}</td>
                  <td style={{ padding: '3px 6px', color: row.option_type === 'CALL' ? '#3fb950' : '#f85149' }}>{row.option_type}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#c9d1d9' }}>{row.delta != null ? row.delta.toFixed(3) : '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#8b949e' }}>{row.gamma != null ? row.gamma.toFixed(4) : '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#8b949e' }}>{row.theta != null ? row.theta.toFixed(4) : '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#8b949e' }}>{row.vega != null ? row.vega.toFixed(4) : '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#c9d1d9' }}>{row.volume ?? '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#c9d1d9' }}>{row.open_interest ?? '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#c9d1d9' }}>{row.bid != null ? `$${row.bid.toFixed(2)}` : '—'}</td>
                  <td style={{ padding: '3px 6px', textAlign: 'right', fontFamily: 'monospace', color: '#c9d1d9' }}>{row.ask != null ? `$${row.ask.toFixed(2)}` : '—'}</td>
                  <td style={{ padding: '3px 6px', fontSize: '0.68rem' }}>
                    {row.is_candidate === null || row.is_candidate === undefined
                      ? <span style={{ color: '#6e7681' }}>—</span>
                      : row.is_candidate
                        ? <span style={{ color: '#d29922' }}>Yes — <span style={{ color: '#f0883e' }}>{row.candidate_filter_killed || 'passed'}</span></span>
                        : <span style={{ color: '#8b949e' }}>No</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
};

// ── Section E: Actions ────────────────────────────────────────────────────────

interface ActionsProps {
  d: RejectionDetail;
}

const ActionsSection = ({ d }: ActionsProps) => {
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState('');
  const [tag, setTag] = useState<'rejection_analysis' | 'false_negative' | 'expected'>('rejection_analysis');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const signalId = `rejection_${d.id}_${d.model_id || d.model}`;

  const submitComment = async (action: 'REJECTION_COMMENT' | 'MARK_EXPECTED' | 'MARK_FALSE_NEG') => {
    setSaving(true);
    setSaveError(null);
    const body = {
      signal_id: signalId,
      signal_snapshot: JSON.stringify({ rejection_id: d.id, ts: d.ts, reason_code: d.reason_code || d.reason }),
      user_comment: comment || (action === 'MARK_EXPECTED' ? 'Marked as expected rejection' : action === 'MARK_FALSE_NEG' ? 'Marked as false-negative rejection' : comment),
      action: 'REJECTION_COMMENT',
      tag,
      reviewer: 'user',
    };
    try {
      const r = await fetch('/api/signals/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: r.statusText }));
        setSaveError((err as { error: string }).error || r.statusText);
      } else {
        setSaved(action === 'MARK_EXPECTED' ? 'Marked as expected' : action === 'MARK_FALSE_NEG' ? 'Marked as false-negative' : 'Comment saved');
        setCommentOpen(false);
        setComment('');
      }
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const copyDiagnostic = () => {
    const text = JSON.stringify(d, null, 2);
    navigator.clipboard.writeText(text).then(() => {
      setSaved('Copied to clipboard');
      setTimeout(() => setSaved(null), 2000);
    });
  };

  return (
    <Section title="E. Actions">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: '0.75rem' }}>
        <button
          onClick={() => setCommentOpen(v => !v)}
          style={{
            padding: '5px 12px', borderRadius: 4, cursor: 'pointer',
            background: 'rgba(63,185,80,0.1)', border: '1px solid rgba(63,185,80,0.4)',
            color: '#3fb950', fontWeight: 600,
          }}
        >
          {commentOpen ? 'Cancel comment' : 'Comment on this rejection'}
        </button>
        <button
          onClick={() => { setTag('expected'); setComment('Marked as expected rejection'); submitComment('MARK_EXPECTED'); }}
          disabled={saving}
          style={{
            padding: '5px 12px', borderRadius: 4, cursor: 'pointer',
            background: 'rgba(121,192,255,0.1)', border: '1px solid rgba(121,192,255,0.4)',
            color: '#79c0ff', fontWeight: 600,
          }}
        >
          Mark as expected
        </button>
        <button
          onClick={() => { setTag('false_negative'); setComment('Marked as false-negative rejection'); submitComment('MARK_FALSE_NEG'); }}
          disabled={saving}
          style={{
            padding: '5px 12px', borderRadius: 4, cursor: 'pointer',
            background: 'rgba(248,81,73,0.1)', border: '1px solid rgba(248,81,73,0.4)',
            color: '#f85149', fontWeight: 600,
          }}
        >
          Mark as false-negative <TermLabel term="FALSE_NEGATIVE" />
        </button>
        <button
          onClick={copyDiagnostic}
          style={{
            padding: '5px 12px', borderRadius: 4, cursor: 'pointer',
            background: 'rgba(139,148,158,0.1)', border: '1px solid rgba(139,148,158,0.4)',
            color: '#8b949e', fontWeight: 600,
          }}
        >
          Copy diagnostic JSON
        </button>
      </div>

      {commentOpen && (
        <div style={{ marginTop: 12 }}>
          <div style={{ marginBottom: 6, fontSize: '0.72rem', color: '#8b949e' }}>
            Tag:{' '}
            <select
              value={tag}
              onChange={e => setTag(e.target.value as typeof tag)}
              style={{ background: '#0d1117', border: '1px solid #30363d', color: '#c9d1d9', borderRadius: 3, padding: '2px 6px', fontSize: '0.72rem' }}
            >
              <option value="rejection_analysis">rejection_analysis</option>
              <option value="false_negative">false_negative</option>
              <option value="expected">expected</option>
            </select>
          </div>
          <textarea
            value={comment}
            onChange={e => setComment(e.target.value)}
            placeholder="Your annotation (verbatim — never trimmed)"
            rows={3}
            style={{
              width: '100%', background: '#161b22', border: '1px solid #30363d',
              color: '#c9d1d9', borderRadius: 4, padding: '6px 8px',
              fontSize: '0.75rem', fontFamily: 'inherit', resize: 'vertical',
              boxSizing: 'border-box',
            }}
          />
          <div style={{ marginTop: 6, display: 'flex', gap: 8 }}>
            <button
              onClick={() => submitComment('REJECTION_COMMENT')}
              disabled={saving || !comment.trim()}
              style={{
                padding: '4px 12px', borderRadius: 4, cursor: saving || !comment.trim() ? 'not-allowed' : 'pointer',
                background: 'rgba(63,185,80,0.15)', border: '1px solid rgba(63,185,80,0.4)',
                color: '#3fb950', fontWeight: 600, fontSize: '0.75rem', opacity: saving || !comment.trim() ? 0.5 : 1,
              }}
            >
              {saving ? 'Saving…' : 'Save comment'}
            </button>
          </div>
        </div>
      )}

      {saved && (
        <div style={{ marginTop: 8, color: '#3fb950', fontSize: '0.75rem' }}>{saved}</div>
      )}
      {saveError && (
        <div style={{ marginTop: 8, color: '#f85149', fontSize: '0.75rem' }}>Error: {saveError}</div>
      )}
    </Section>
  );
};

// ── Main modal ────────────────────────────────────────────────────────────────

interface Props {
  rejectionId: number;
  onClose: () => void;
}

const RejectedSignalDetailModal = ({ rejectionId, onClose }: Props) => {
  const [data, setData] = useState<RejectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/signals/rejections/${rejectionId}`);
      if (!r.ok) {
        setError(`HTTP ${r.status}: ${r.statusText}`);
        return;
      }
      const d = await r.json() as RejectionDetail;
      setData(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [rejectionId]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.8)',
        zIndex: 10100, display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        paddingTop: 40, overflowY: 'auto',
      }}
      role="dialog"
      aria-modal="true"
      aria-label={`Rejection detail #${rejectionId}`}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#0d1117', border: '1px solid #30363d', borderRadius: 8,
          padding: '20px 24px', width: '90vw', maxWidth: 860,
          marginBottom: 40,
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <span style={{ color: '#d29922', fontWeight: 700, fontSize: '0.95rem' }}>
              Rejection Drill-Down
            </span>
            <span style={{ color: '#6e7681', fontSize: '0.78rem', marginLeft: 10 }}>
              #{rejectionId}
            </span>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: '#8b949e', cursor: 'pointer', fontSize: '1.2rem', lineHeight: 1 }}
            aria-label="Close"
          >✕</button>
        </div>

        {/* Loading / error / content */}
        {loading && (
          <div style={{ color: '#8b949e', fontSize: '0.85rem', padding: '24px 0', textAlign: 'center' }}>
            Loading rejection detail…
          </div>
        )}
        {!loading && error && (
          <div style={{ color: '#f85149', fontSize: '0.85rem', padding: '16px 0' }}>
            Failed to load: {error}
          </div>
        )}
        {!loading && !error && data && (
          <>
            <SignalMetaSection d={data} />
            <RejectionReasonSection d={data} />
            <MarketContextSection d={data} />
            <ChainSnapshotSection d={data} />
            <ActionsSection d={data} />
          </>
        )}
      </div>
    </div>,
    document.body,
  );
};

export default RejectedSignalDetailModal;
