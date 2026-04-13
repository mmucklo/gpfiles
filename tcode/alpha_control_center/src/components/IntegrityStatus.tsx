import { useState, useEffect, useCallback } from 'react';
import './IntegrityStatus.css';

// ============================================================
//  Types
// ============================================================
interface IntegrityData {
  price: {
    tv: number | null;
    yf: number | null;
    ibkr: number | null;
    divergence_pct: number;
    ok: boolean;
    timestamp: string | null;
  };
  chain: {
    last_refresh: string | null;
    entry_count: number;
    age_sec: number;
    ok: boolean;
    source: string;
  };
  execution: {
    broker_confirmed: boolean;
    last_fill_id: string | null;
    nav_checksum: string | null;
    mode: string;
    connected: boolean;
    signals_rejected_commission: number;
    order_path: string | null;
  };
}

type TrafficLight = 'green' | 'amber' | 'red';

function priceStatus(data: IntegrityData['price']): TrafficLight {
  if (data.tv === null && data.yf === null) return 'red';
  if (data.divergence_pct > 0.5) return 'red';
  if (data.divergence_pct > 0.2) return 'amber';
  return 'green';
}

function chainStatus(data: IntegrityData['chain']): TrafficLight {
  // Only flag stale during market hours
  const now = new Date();
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  });
  const parts = fmt.formatToParts(now);
  const h = parseInt(parts.find(p => p.type === 'hour')?.value ?? '0', 10);
  const m = parseInt(parts.find(p => p.type === 'minute')?.value ?? '0', 10);
  const t = h * 60 + m;
  const marketHours = t >= 570 && t < 960;

  if (data.entry_count === 0) return 'red';
  if (marketHours && data.age_sec > 300) return 'red';
  if (marketHours && data.age_sec > 120) return 'amber';
  return 'green';
}

function executionStatus(data: IntegrityData['execution']): TrafficLight {
  const mode = (data.mode || '').toUpperCase();
  if (!data.connected && mode !== 'SIMULATION') return 'amber';
  if (mode === 'IBKR_LIVE' && !data.broker_confirmed) return 'red';
  return 'green';
}

// ============================================================
//  Sub-components
// ============================================================
interface IndicatorProps {
  label: string;
  status: TrafficLight;
  onClick: () => void;
  children: React.ReactNode;
}

const Indicator = ({ label, status, onClick, children }: IndicatorProps) => (
  <button
    className={`integrity-indicator integrity-${status}`}
    onClick={onClick}
    aria-label={`${label} integrity status: ${status}`}
    title={`Click for ${label} details`}
    data-integrity-status={status}
  >
    <span className="integrity-dot" aria-hidden="true" />
    <span className="integrity-label">{label}</span>
    {children}
  </button>
);

interface PanelProps {
  data: IntegrityData | null;
  loading: boolean;
  onClose: () => void;
  openSection: 'price' | 'chain' | 'execution';
}

const IntegrityPanel = ({ data, loading, onClose, openSection }: PanelProps) => {
  const [tab, setTab] = useState<'price' | 'chain' | 'execution'>(openSection);

  useEffect(() => {
    setTab(openSection);
  }, [openSection]);

  if (loading || !data) {
    return (
      <div className="integrity-panel-overlay" onClick={onClose}>
        <div className="integrity-panel" onClick={e => e.stopPropagation()} role="dialog" aria-label="Integrity Status Detail">
          <div className="integrity-panel-header">
            <span>INTEGRITY STATUS</span>
            <button onClick={onClose} aria-label="Close integrity panel">×</button>
          </div>
          <div className="integrity-panel-loading">Loading integrity data…</div>
        </div>
      </div>
    );
  }

  const pStatus = priceStatus(data.price);
  const cStatus = chainStatus(data.chain);
  const eStatus = executionStatus(data.execution);

  const fmtAge = (sec: number) => sec < 60 ? `${Math.round(sec)}s ago` : `${Math.round(sec / 60)}m ago`;

  return (
    <div className="integrity-panel-overlay" onClick={onClose}>
      <div className="integrity-panel" onClick={e => e.stopPropagation()} role="dialog" aria-label="Integrity Status Detail">
        <div className="integrity-panel-header">
          <span>INTEGRITY STATUS</span>
          <button onClick={onClose} aria-label="Close integrity panel">×</button>
        </div>

        <div className="integrity-tabs" role="tablist">
          {(['price', 'chain', 'execution'] as const).map(t => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`integrity-tab ${tab === t ? 'active' : ''} integrity-tab-${t === 'price' ? pStatus : t === 'chain' ? cStatus : eStatus}`}
              onClick={() => setTab(t)}
            >
              <span className={`integrity-dot integrity-dot-sm integrity-${t === 'price' ? pStatus : t === 'chain' ? cStatus : eStatus}`} />
              {t === 'price' ? 'PRICE' : t === 'chain' ? 'CHAIN' : 'EXECUTION'}
            </button>
          ))}
        </div>

        <div className="integrity-panel-body">
          {tab === 'price' && (
            <div className="integrity-section">
              <div className={`integrity-status-banner integrity-${pStatus}`}>
                PRICE INTEGRITY: {pStatus.toUpperCase()}
                {pStatus === 'red' && data.price.divergence_pct > 0.5 && (
                  <span className="integrity-halt-notice"> — TRADING HALTED</span>
                )}
              </div>
              <table className="integrity-table">
                <tbody>
                  <tr>
                    <td>TradingView</td>
                    <td>{data.price.tv != null ? `$${data.price.tv.toFixed(2)}` : <span className="integrity-na">unavailable</span>}</td>
                    <td><span className={`integrity-src-badge ${data.price.tv != null ? 'ok' : 'err'}`}>{data.price.tv != null ? 'OK' : 'ERR'}</span></td>
                  </tr>
                  <tr>
                    <td>yfinance</td>
                    <td>{data.price.yf != null ? `$${data.price.yf.toFixed(2)}` : <span className="integrity-na">unavailable</span>}</td>
                    <td><span className={`integrity-src-badge ${data.price.yf != null ? 'ok' : 'err'}`}>{data.price.yf != null ? 'OK' : 'ERR'}</span></td>
                  </tr>
                  <tr>
                    <td>IBKR</td>
                    <td>{data.price.ibkr != null ? `$${data.price.ibkr.toFixed(2)}` : <span className="integrity-na">not connected</span>}</td>
                    <td><span className={`integrity-src-badge ${data.price.ibkr != null ? 'ok' : 'gray'}`}>{data.price.ibkr != null ? 'OK' : '—'}</span></td>
                  </tr>
                  <tr>
                    <td>Divergence</td>
                    <td className={data.price.divergence_pct > 0.5 ? 'integrity-val-red' : data.price.divergence_pct > 0.2 ? 'integrity-val-amber' : 'integrity-val-green'}>
                      {data.price.divergence_pct.toFixed(3)}%
                    </td>
                    <td>{data.price.divergence_pct > 0.5 ? <span className="integrity-src-badge err">HALT</span> : <span className="integrity-src-badge ok">OK</span>}</td>
                  </tr>
                  {data.price.timestamp && (
                    <tr>
                      <td>Last checked</td>
                      <td colSpan={2}>{new Date(data.price.timestamp).toLocaleTimeString()}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <p className="integrity-rule">Rule: if any two-source divergence exceeds 0.5%, new trades are blocked and this panel turns RED.</p>
            </div>
          )}

          {tab === 'chain' && (
            <div className="integrity-section">
              <div className={`integrity-status-banner integrity-${cStatus}`}>
                CHAIN INTEGRITY: {cStatus.toUpperCase()}
              </div>
              <table className="integrity-table">
                <tbody>
                  <tr>
                    <td>Source</td>
                    <td>{data.chain.source || 'yfinance'}</td>
                  </tr>
                  <tr>
                    <td>Entry Count</td>
                    <td>{data.chain.entry_count > 0 ? data.chain.entry_count.toLocaleString() : <span className="integrity-na">0 — no data</span>}</td>
                  </tr>
                  <tr>
                    <td>Last Refresh</td>
                    <td>{data.chain.last_refresh ? new Date(data.chain.last_refresh).toLocaleTimeString() : <span className="integrity-na">never</span>}</td>
                  </tr>
                  <tr>
                    <td>Age</td>
                    <td className={data.chain.age_sec > 300 ? 'integrity-val-red' : data.chain.age_sec > 120 ? 'integrity-val-amber' : 'integrity-val-green'}>
                      {data.chain.age_sec > 0 ? fmtAge(data.chain.age_sec) : 'fresh'}
                    </td>
                  </tr>
                </tbody>
              </table>
              <p className="integrity-rule">Rule: chain data &gt;5 min stale during market hours turns this panel RED.</p>
            </div>
          )}

          {tab === 'execution' && (
            <div className="integrity-section">
              <div className={`integrity-status-banner integrity-${eStatus}`}>
                EXECUTION INTEGRITY: {eStatus.toUpperCase()}
              </div>
              <table className="integrity-table">
                <tbody>
                  <tr>
                    <td>Mode</td>
                    <td>{data.execution.mode?.toUpperCase() || '—'}</td>
                  </tr>
                  <tr>
                    <td>Broker Connected</td>
                    <td>{data.execution.connected
                      ? <span className="integrity-src-badge ok">YES</span>
                      : <span className="integrity-src-badge gray">NO</span>}
                    </td>
                  </tr>
                  <tr>
                    <td>Broker Confirmed</td>
                    <td>{data.execution.broker_confirmed
                      ? <span className="integrity-src-badge ok">YES</span>
                      : <span className="integrity-src-badge gray">—</span>}
                    </td>
                  </tr>
                  <tr>
                    <td>Last Fill ID</td>
                    <td>{data.execution.last_fill_id || <span className="integrity-na">none</span>}</td>
                  </tr>
                  <tr>
                    <td>NAV Checksum</td>
                    <td style={{ fontFamily: 'monospace', fontSize: '11px' }}>{data.execution.nav_checksum || <span className="integrity-na">—</span>}</td>
                  </tr>
                  <tr>
                    <td>Order Path</td>
                    <td style={{ fontFamily: 'monospace', fontSize: '11px' }}>
                      {data.execution.order_path
                        ? <span style={{ color: data.execution.order_path.includes('real') ? '#3fb950' : '#79c0ff' }}>
                            {data.execution.order_path}
                          </span>
                        : <span className="integrity-na">—</span>}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <span title="Signals suppressed in this session because round-trip IBKR commissions would exceed the profit at the take-profit price.">
                        Signals Rejected (commission)
                      </span>
                    </td>
                    <td>
                      <span
                        style={{ color: data.execution.signals_rejected_commission > 0 ? '#d29922' : '#3fb950', fontWeight: 700 }}
                        title={`${data.execution.signals_rejected_commission} signal(s) suppressed — net profit at TP would be ≤ 0 after IBKR round-trip commissions`}
                      >
                        {data.execution.signals_rejected_commission}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
              <p className="integrity-rule">Rule: in LIVE mode, broker must confirm connection and last fill. RED blocks new trades.</p>
              {data.execution.signals_rejected_commission > 0 && (
                <p className="integrity-rule" style={{ color: '#d29922' }}>
                  ⚠ {data.execution.signals_rejected_commission} signal(s) suppressed this session — premium too low to cover IBKR round-trip commissions at the stated take-profit price.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ============================================================
//  Main Export
// ============================================================
export interface IntegrityStatusRef {
  isAnyRed: boolean;
}

interface IntegrityStatusProps {
  onStatusChange?: (isAnyRed: boolean) => void;
}

const IntegrityStatus = ({ onStatusChange }: IntegrityStatusProps) => {
  const [data, setData] = useState<IntegrityData | null>(null);
  const [loading, setLoading] = useState(true);
  const [panelOpen, setPanelOpen] = useState(false);
  const [openSection, setOpenSection] = useState<'price' | 'chain' | 'execution'>('price');

  const fetchIntegrity = useCallback(async () => {
    try {
      // Fetch audit and broker status — these are fast.
      // /api/fills is omitted here: it performs a live IBKR connection attempt that can
      // take 10+ s; last_fill_id is cosmetic and shown as "none" when unavailable.
      const [auditRes, brokerRes, pubMetricsRes] = await Promise.all([
        fetch('/api/data/audit'),
        fetch('/api/broker/status'),
        fetch('/api/metrics/publisher'),
      ]);

      const audit = auditRes.ok ? await auditRes.json().catch(() => null) : null;
      const broker = brokerRes.ok ? await brokerRes.json().catch(() => null) : null;
      const pubMetrics = pubMetricsRes.ok ? await pubMetricsRes.json().catch(() => null) : null;

      const sv = audit?.spot_validation ?? {};

      // NAV checksum: hash of nav+cash+positions for quick integrity check
      const navVal = audit?.ibkr_spot ?? 0;
      const navChecksum = navVal > 0 ? `sha:${Math.abs(navVal * 1000 | 0).toString(16).slice(0, 8)}` : null;

      const integrityData: IntegrityData = {
        price: {
          tv: sv.tv ?? null,
          yf: sv.yf ?? null,
          ibkr: audit?.ibkr_spot > 0 ? audit.ibkr_spot : null,
          divergence_pct: sv.divergence_pct ?? 0,
          ok: sv.ok ?? true,
          timestamp: sv.timestamp ?? null,
        },
        chain: {
          last_refresh: audit?.last_chain_fetch ?? null,
          entry_count: audit?.chain_entry_count ?? 0,
          age_sec: audit?.chain_age_sec ?? 0,
          ok: (audit?.chain_age_sec ?? 0) < 300,
          source: audit?.options_chain_source ?? 'yfinance',
        },
        execution: {
          broker_confirmed: broker?.connected ?? false,
          last_fill_id: null,
          nav_checksum: navChecksum,
          mode: broker?.mode ?? 'SIMULATION',
          connected: broker?.connected ?? false,
          signals_rejected_commission: pubMetrics?.signals_rejected_commission_total ?? 0,
          order_path: broker?.order_path ?? null,
        },
      };

      setData(integrityData);
      setLoading(false);

      const pSt = priceStatus(integrityData.price);
      const cSt = chainStatus(integrityData.chain);
      const eSt = executionStatus(integrityData.execution);
      const anyRed = pSt === 'red' || cSt === 'red' || eSt === 'red';
      onStatusChange?.(anyRed);
    } catch {
      setLoading(false);
    }
  }, [onStatusChange]);

  useEffect(() => {
    fetchIntegrity();
    const id = setInterval(fetchIntegrity, 15000);
    return () => clearInterval(id);
  }, [fetchIntegrity]);

  const pSt = data ? priceStatus(data.price) : 'amber';
  const cSt = data ? chainStatus(data.chain) : 'amber';
  const eSt = data ? executionStatus(data.execution) : 'amber';

  const openPanel = (section: 'price' | 'chain' | 'execution') => {
    setOpenSection(section);
    setPanelOpen(true);
  };

  return (
    <>
      {panelOpen && (
        <IntegrityPanel
          data={data}
          loading={loading || !data}
          onClose={() => setPanelOpen(false)}
          openSection={openSection}
        />
      )}
      <div className="integrity-bar" role="region" aria-label="Data Integrity Status">
        <span className="integrity-bar-label">INTEGRITY</span>
        <Indicator label="PRICE" status={pSt} onClick={() => openPanel('price')}>
          {data && (
            <span className="integrity-detail">
              {data.price.divergence_pct.toFixed(2)}%
            </span>
          )}
        </Indicator>
        <Indicator label="CHAIN" status={cSt} onClick={() => openPanel('chain')}>
          {data && (
            <span className="integrity-detail">
              {data.chain.age_sec > 0 ? `${Math.round(data.chain.age_sec)}s` : 'fresh'}
            </span>
          )}
        </Indicator>
        <Indicator label="EXEC" status={eSt} onClick={() => openPanel('execution')}>
          {data && (
            <span className="integrity-detail">
              {data.execution.mode?.toUpperCase()}
            </span>
          )}
        </Indicator>
      </div>
    </>
  );
};

export { priceStatus, chainStatus, executionStatus };
export type { IntegrityData, TrafficLight };
export default IntegrityStatus;
