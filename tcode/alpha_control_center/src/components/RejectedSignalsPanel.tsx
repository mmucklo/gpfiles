/**
 * RejectedSignalsPanel — Phase 14.3
 *
 * Full list view for signal rejections: filters, table, pagination, drill-down.
 * Opens when the header badge is clicked.
 *
 * Endpoints used:
 *   GET /api/signals/rejections?hours=24&limit=50&offset=0&model=&reason=&archetype=
 *   GET /api/signals/rejections/summary?hours=24
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import TermLabel from './TermLabel';
import RejectedSignalDetailModal from './RejectedSignalDetailModal';

// ── Types ─────────────────────────────────────────────────────────────────────

interface RejectionListItem {
  id: number;
  ts: string;
  model_id: string;
  direction: string | null;
  confidence: number | null;
  reason_code: string | null;
  reason_detail: string | null;
  spot_at_rejection: number | null;
  option_type: string | null;
  expiration_date: string | null;
  archetype: string | null;
  chop_regime_at_rejection: string | null;
  // Phase 14.1 fallback
  model: string;
  opt_type: string;
  reason: string;
  expiry: string | null;
}

interface RejectionListResponse {
  total_count: number;
  items: RejectionListItem[];
  has_more: boolean;
}

interface SummaryResponse {
  total: number;
  by_reason: Record<string, number>;
  by_model: Record<string, number>;
  by_archetype: Record<string, number>;
  since: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

const REASON_COLOR: Record<string, string> = {
  STRIKE_SELECT_FAIL: '#f85149',
  LIQUIDITY_REJECT:   '#d29922',
  CHOP_BLOCK:         '#79c0ff',
  GREEKS_UNAVAILABLE: '#a371f7',
  SPOT_VARIANCE:      '#f0883e',
};

function reasonColor(rc: string | null): string {
  if (!rc) return '#8b949e';
  const u = rc.toUpperCase();
  for (const [k, c] of Object.entries(REASON_COLOR)) {
    if (u.includes(k)) return c;
  }
  return '#8b949e';
}

function reasonTerm(rc: string | null): string {
  if (!rc) return '';
  const u = rc.toUpperCase();
  for (const k of Object.keys(REASON_COLOR)) {
    if (u.includes(k)) return k;
  }
  return '';
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(ts: string): string {
  try {
    return new Date(ts.replace(' ', 'T') + 'Z').toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return ts.slice(11, 19) || ts;
  }
}

function contractLabel(item: RejectionListItem): string {
  const ot = item.option_type || item.opt_type || '?';
  const exp = item.expiration_date || item.expiry || '';
  const expShort = exp ? exp.slice(5) : '';          // "04-24"
  return `${ot} ${expShort}`;
}

// ── Confidence bar ────────────────────────────────────────────────────────────

const ConfBar = ({ value }: { value: number | null }) => {
  if (value == null) return <span style={{ color: '#6e7681' }}>—</span>;
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? '#3fb950' : pct >= 50 ? '#d29922' : '#f85149';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 70 }}>
      <div style={{ flex: 1, height: 6, background: '#21262d', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
      </div>
      <span style={{ color, fontSize: '0.7rem', fontFamily: 'monospace', minWidth: 28 }}>{pct}%</span>
    </div>
  );
};

// ── Summary mini-bar ──────────────────────────────────────────────────────────

const SummaryBar = ({ summary }: { summary: SummaryResponse | null }) => {
  if (!summary || summary.total === 0) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', fontSize: '0.72rem', color: '#8b949e', marginBottom: 12 }}>
      {Object.entries(summary.by_reason).map(([rc, cnt]) => (
        <span key={rc}>
          <span style={{ color: reasonColor(rc), fontWeight: 700 }}>{cnt}</span>
          {' '}
          <TermLabel term={reasonTerm(rc) || rc} badge>{rc}</TermLabel>
        </span>
      ))}
    </div>
  );
};

// ── Filter pills ──────────────────────────────────────────────────────────────

interface FiltersProps {
  model: string;
  reason: string;
  archetype: string;
  hours: number;
  onModel: (v: string) => void;
  onReason: (v: string) => void;
  onArchetype: (v: string) => void;
  onHours: (v: number) => void;
  summary: SummaryResponse | null;
}

const Filters = ({ model, reason, archetype, hours, onModel, onReason, onArchetype, onHours, summary }: FiltersProps) => {
  const reasons = summary ? Object.keys(summary.by_reason) : [];
  const models  = summary ? Object.keys(summary.by_model) : [];
  const archs   = summary ? Object.keys(summary.by_archetype) : [];

  const pillStyle = (active: boolean, color = '#d29922'): React.CSSProperties => ({
    padding: '2px 10px', borderRadius: 12, fontSize: '0.7rem', cursor: 'pointer',
    background: active ? `${color}22` : 'transparent',
    border: `1px solid ${active ? color : '#30363d'}`,
    color: active ? color : '#8b949e',
    fontWeight: active ? 700 : 400,
  });

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10, alignItems: 'center' }}>
      {/* Hours */}
      {[1, 4, 24, 72].map(h => (
        <button key={h} style={pillStyle(hours === h)} onClick={() => onHours(h)}>
          {h < 24 ? `${h}h` : `${h / 24}d`}
        </button>
      ))}

      <span style={{ width: 1, height: 16, background: '#30363d', display: 'inline-block', margin: '0 4px' }} />

      {/* Reason filter */}
      {reasons.map(rc => (
        <button
          key={rc}
          style={pillStyle(reason === rc, reasonColor(rc))}
          onClick={() => onReason(reason === rc ? '' : rc)}
        >
          {rc}
        </button>
      ))}

      {/* Model filter */}
      {models.map(m => (
        <button
          key={m}
          style={pillStyle(model === m, '#3fb950')}
          onClick={() => onModel(model === m ? '' : m)}
        >
          {m}
        </button>
      ))}

      {/* Archetype filter */}
      {archs.map(a => (
        <button
          key={a}
          style={pillStyle(archetype === a, '#a371f7')}
          onClick={() => onArchetype(archetype === a ? '' : a)}
        >
          {a}
        </button>
      ))}

      {/* Clear */}
      {(model || reason || archetype) && (
        <button
          style={{ ...pillStyle(false), color: '#f85149', borderColor: '#f8514933' }}
          onClick={() => { onModel(''); onReason(''); onArchetype(''); }}
        >
          ✕ clear
        </button>
      )}
    </div>
  );
};

// ── Main panel ────────────────────────────────────────────────────────────────

interface Props {
  onClose: () => void;
  initialRejectionId?: number;  // pre-open a specific row drill-down
}

const RejectedSignalsPanel = ({ onClose, initialRejectionId }: Props) => {
  const [hours, setHours]         = useState(24);
  const [modelFilter, setModel]   = useState('');
  const [reasonFilter, setReason] = useState('');
  const [archFilter, setArch]     = useState('');
  const [offset, setOffset]       = useState(0);

  const [data, setData]           = useState<RejectionListResponse | null>(null);
  const [summary, setSummary]     = useState<SummaryResponse | null>(null);
  const [loading, setLoading]     = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError]         = useState<string | null>(null);

  const [drillId, setDrillId]     = useState<number | null>(initialRejectionId ?? null);

  const abortRef = useRef<AbortController | null>(null);

  const fetchList = useCallback(async (reset = true) => {
    if (abortRef.current) abortRef.current.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    if (reset) { setLoading(true); setOffset(0); }
    else        setLoadingMore(true);
    setError(null);

    const off = reset ? 0 : offset;
    const params = new URLSearchParams({
      hours: String(hours), limit: String(PAGE_SIZE), offset: String(off),
    });
    if (modelFilter)  params.set('model', modelFilter);
    if (reasonFilter) params.set('reason', reasonFilter);
    if (archFilter)   params.set('archetype', archFilter);

    try {
      const [listRes, sumRes] = await Promise.all([
        fetch(`/api/signals/rejections?${params}`, { signal: ctrl.signal }),
        reset ? fetch(`/api/signals/rejections/summary?hours=${hours}`, { signal: ctrl.signal }) : Promise.resolve(null),
      ]);

      if (!listRes.ok) { setError(`HTTP ${listRes.status}`); return; }
      const listData = await listRes.json() as RejectionListResponse;

      if (reset) {
        setData(listData);
        if (sumRes?.ok) setSummary(await sumRes.json() as SummaryResponse);
      } else {
        setData(prev => prev
          ? { ...listData, items: [...prev.items, ...listData.items] }
          : listData
        );
        setOffset(off + PAGE_SIZE);
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError(String(e));
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [hours, modelFilter, reasonFilter, archFilter, offset]);

  // Refetch when filters change
  useEffect(() => { fetchList(true); }, [hours, modelFilter, reasonFilter, archFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  // Close on Escape
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' && !drillId) onClose(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [onClose, drillId]);

  const items = data?.items ?? [];

  return createPortal(
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
          zIndex: 10000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
          paddingTop: 32, overflowY: 'auto',
        }}
        role="dialog"
        aria-modal="true"
        aria-label="Rejected Signals Panel"
      >
        <div
          onClick={e => e.stopPropagation()}
          style={{
            background: '#0d1117', border: '1px solid #30363d', borderRadius: 8,
            padding: '20px 24px', width: '92vw', maxWidth: 1100, marginBottom: 40,
            minHeight: 300,
          }}
        >
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <div>
              <span style={{ color: '#d29922', fontWeight: 700, fontSize: '0.95rem' }}>
                Rejected Signals
              </span>
              {summary != null && (
                <span style={{ color: '#8b949e', fontSize: '0.78rem', marginLeft: 10 }}>
                  {summary.total} in last {hours}h
                </span>
              )}
            </div>
            <button
              onClick={onClose}
              style={{ background: 'none', border: 'none', color: '#8b949e', cursor: 'pointer', fontSize: '1.2rem', lineHeight: 1 }}
              aria-label="Close"
            >✕</button>
          </div>

          {/* Summary breakdown */}
          <SummaryBar summary={summary} />

          {/* Filters */}
          <Filters
            model={modelFilter} reason={reasonFilter} archetype={archFilter} hours={hours}
            onModel={setModel} onReason={setReason} onArchetype={setArch} onHours={setHours}
            summary={summary}
          />

          {/* Content */}
          {loading && (
            <div style={{ color: '#8b949e', fontSize: '0.85rem', padding: '32px 0', textAlign: 'center' }}>
              Loading rejections…
            </div>
          )}
          {!loading && error && (
            <div style={{ color: '#f85149', fontSize: '0.85rem', padding: '16px 0' }}>
              Failed to load: {error}
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div style={{
              textAlign: 'center', padding: '40px 0', color: '#6e7681',
              fontSize: '0.85rem',
            }}>
              <div style={{ fontSize: '2rem', marginBottom: 10 }}>🕐</div>
              <div>No rejections in the selected window</div>
              {(modelFilter || reasonFilter || archFilter) && (
                <div style={{ marginTop: 6, fontSize: '0.75rem' }}>Try clearing the active filters</div>
              )}
            </div>
          )}
          {!loading && !error && items.length > 0 && (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem', color: '#c9d1d9' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #30363d', color: '#8b949e', position: 'sticky', top: 0, background: '#0d1117' }}>
                      <th style={{ textAlign: 'left', padding: '5px 8px', whiteSpace: 'nowrap' }}>Time</th>
                      <th style={{ textAlign: 'left', padding: '5px 8px' }}>Model</th>
                      <th style={{ textAlign: 'left', padding: '5px 8px' }}>Direction</th>
                      <th style={{ textAlign: 'left', padding: '5px 8px', minWidth: 80 }}>Confidence</th>
                      <th style={{ textAlign: 'left', padding: '5px 8px' }}>Contract</th>
                      <th style={{ textAlign: 'left', padding: '5px 8px' }}>Reason</th>
                      <th style={{ textAlign: 'left', padding: '5px 8px', maxWidth: 220 }}>Detail (first 80 chars)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(item => {
                      const rc = item.reason_code || item.reason;
                      const rcTerm = reasonTerm(rc);
                      return (
                        <tr
                          key={item.id}
                          onClick={() => setDrillId(item.id)}
                          style={{
                            borderBottom: '1px solid rgba(48,54,61,0.5)',
                            cursor: 'pointer',
                            transition: 'background 0.1s',
                          }}
                          onMouseEnter={e => (e.currentTarget.style.background = '#161b22')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                          role="button"
                          tabIndex={0}
                          aria-label={`Rejection #${item.id} — ${item.model_id || item.model} ${rc}`}
                          onKeyDown={e => { if (e.key === 'Enter') setDrillId(item.id); }}
                        >
                          <td style={{ padding: '5px 8px', color: '#8b949e', whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: '0.7rem' }}>
                            {fmtTime(item.ts)}
                          </td>
                          <td style={{ padding: '5px 8px' }}>
                            <TermLabel term={(item.model_id || item.model).toUpperCase()} badge>
                              {item.model_id || item.model}
                            </TermLabel>
                          </td>
                          <td style={{ padding: '5px 8px' }}>
                            {item.direction
                              ? <span style={{ color: item.direction === 'BULLISH' ? '#3fb950' : '#f85149', fontWeight: 700, fontSize: '0.7rem' }}>{item.direction}</span>
                              : <span style={{ color: '#6e7681' }}>—</span>
                            }
                          </td>
                          <td style={{ padding: '5px 8px' }}>
                            <ConfBar value={item.confidence} />
                          </td>
                          <td style={{ padding: '5px 8px', fontFamily: 'monospace', fontSize: '0.7rem', color: '#8b949e' }}>
                            {contractLabel(item)}
                          </td>
                          <td style={{ padding: '5px 8px' }}>
                            <span style={{
                              display: 'inline-block', padding: '1px 8px', borderRadius: 10,
                              background: `${reasonColor(rc)}22`, border: `1px solid ${reasonColor(rc)}66`,
                              color: reasonColor(rc), fontWeight: 700, fontSize: '0.68rem', whiteSpace: 'nowrap',
                            }}>
                              {rcTerm ? <TermLabel term={rcTerm}>{rc}</TermLabel> : rc}
                            </span>
                          </td>
                          <td style={{
                            padding: '5px 8px', color: '#8b949e', maxWidth: 220,
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                            fontSize: '0.68rem',
                          }}
                            title={item.reason_detail || item.reason}
                          >
                            {item.reason_detail || item.reason}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Load more */}
              {data?.has_more && (
                <div style={{ textAlign: 'center', marginTop: 14 }}>
                  <button
                    onClick={() => fetchList(false)}
                    disabled={loadingMore}
                    style={{
                      padding: '6px 20px', borderRadius: 4, cursor: loadingMore ? 'not-allowed' : 'pointer',
                      background: 'rgba(139,148,158,0.1)', border: '1px solid #30363d',
                      color: '#c9d1d9', fontSize: '0.78rem',
                      opacity: loadingMore ? 0.6 : 1,
                    }}
                  >
                    {loadingMore ? 'Loading…' : `Load more (${data.total_count - items.length} remaining)`}
                  </button>
                </div>
              )}

              <div style={{ marginTop: 8, textAlign: 'right', fontSize: '0.7rem', color: '#6e7681' }}>
                Showing {items.length} of {data?.total_count ?? 0}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Drill-down modal */}
      {drillId != null && (
        <RejectedSignalDetailModal
          rejectionId={drillId}
          onClose={() => setDrillId(null)}
        />
      )}
    </>,
    document.body,
  );
};

export default RejectedSignalsPanel;
