/**
 * SystemHealthPanel — Phase 13.6
 *
 * Displays a grid of LED indicators for every long-running platform component.
 * Each row shows: LED · component name (TermLabel) · last heartbeat age · expected cadence.
 * Clicking a row opens a drill-down popover with sparkline + restart button.
 *
 * Also exports <SystemHealthBadge /> for the dashboard header.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import TermLabel from './TermLabel';
import './SystemHealthPanel.css';

// ── Types ─────────────────────────────────────────────────────────────────────

export type HeartbeatStatus = 'ok' | 'degraded' | 'error';

export interface ComponentHealth {
  status: HeartbeatStatus;
  last_ts: string | null;
  age_sec: number | null;
  expected_max_age_sec: number;
  pid: number | null;
  uptime_sec: number | null;
  detail: string | null;
}

export interface HeartbeatsPayload {
  ts: string;
  components: Record<string, ComponentHealth>;
}

interface SparklineRow {
  ts: string;
  status: string;
  detail: string | null;
  pid: number | null;
  uptime_sec: number | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtAge(ageSec: number | null): string {
  if (ageSec === null || ageSec === undefined) return 'never';
  if (ageSec < 60) return `${Math.round(ageSec)}s ago`;
  if (ageSec < 3600) return `${Math.round(ageSec / 60)}m ago`;
  return `${Math.round(ageSec / 3600)}h ago`;
}

function fmtUptime(uptimeSec: number | null): string {
  if (!uptimeSec) return '—';
  const h = Math.floor(uptimeSec / 3600);
  const m = Math.floor((uptimeSec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

const LED_COLOR: Record<HeartbeatStatus, string> = {
  ok: '#3fb950',
  degraded: '#d29922',
  error: '#f85149',
};

// Maps component key → glossary term key
const COMPONENT_TERM: Record<string, string> = {
  publisher: 'PUBLISHER',
  intel_refresh: 'INTEL_REFRESH',
  options_chain_api: 'OPTIONS_CHAIN_API',
  premarket: 'PREMARKET',
  congress_trades: 'CONGRESS_TRADES',
  correlation_regime: 'CORRELATION_REGIME',
  macro_regime: 'MACRO_REGIME',
  engine_subscriber: 'ENGINE_SUBSCRIBER',
  engine_ibkr_status: 'IBKR_GATEWAY',
};

// ── Sparkline ─────────────────────────────────────────────────────────────────

const StatusDot = ({ status }: { status: string }) => {
  const color = status === 'ok' ? '#3fb950' : status === 'degraded' ? '#d29922' : '#f85149';
  return (
    <span
      style={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        backgroundColor: color,
        flexShrink: 0,
      }}
      title={status}
      aria-label={`status: ${status}`}
    />
  );
};

const Sparkline = ({ rows }: { rows: SparklineRow[] }) => {
  if (!rows.length) return <div className="sph-sparkline-empty">No heartbeat history</div>;
  return (
    <div className="sph-sparkline" aria-label="Last 10 heartbeats">
      {[...rows].reverse().map((r, i) => (
        <div key={i} className="sph-spark-row" title={r.detail ?? r.status}>
          <StatusDot status={r.status} />
          <span className="sph-spark-ts">{r.ts.slice(11, 19)}</span>
          {r.detail && (
            <span className="sph-spark-detail">{r.detail.slice(0, 48)}</span>
          )}
        </div>
      ))}
    </div>
  );
};

// ── Restart Button + 3s countdown modal ───────────────────────────────────────

interface RestartModalProps {
  component: string;
  onClose: () => void;
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

const RestartModal = ({ component, onClose, onSuccess, onError }: RestartModalProps) => {
  const [countdown, setCountdown] = useState(3);
  const [restarting, setRestarting] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    timerRef.current = setInterval(() => {
      setCountdown(n => {
        if (n <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return n - 1;
      });
    }, 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  const handleConfirm = async () => {
    if (countdown > 0 || restarting) return;
    setRestarting(true);
    try {
      const res = await fetch(`/api/system/heartbeats/${component}/restart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (data.ok) {
        onSuccess(data.msg ?? `Restarted ${component}`);
      } else {
        onError(data.error ?? 'restart failed');
      }
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : 'network error');
    }
    setRestarting(false);
    onClose();
  };

  return createPortal(
    <div className="sph-modal-overlay" onClick={onClose} role="dialog" aria-modal="true"
      aria-label={`Confirm restart ${component}`}>
      <div className="sph-modal-card" onClick={e => e.stopPropagation()}>
        <div className="sph-modal-title">Restart {component}?</div>
        <p className="sph-modal-body">
          This will run <code>systemctl --user restart</code> for the associated service.
          The component will be offline for a few seconds.
        </p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button className="sph-btn-cancel" onClick={onClose}>Cancel</button>
          <button
            className="sph-btn-confirm"
            onClick={handleConfirm}
            disabled={countdown > 0 || restarting}
            data-testid="restart-confirm-btn"
          >
            {restarting ? 'Restarting…' : countdown > 0 ? `Restart (${countdown})` : 'Restart'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};

// ── DrillDown popover ─────────────────────────────────────────────────────────

const RESTARTABLE = new Set(['publisher', 'engine_subscriber', 'engine_ibkr_status']);

interface DrillDownProps {
  component: string;
  health: ComponentHealth;
  onClose: () => void;
}

const DrillDown = ({ component, health, onClose }: DrillDownProps) => {
  const [sparkline, setSparkline] = useState<SparklineRow[]>([]);
  const [sparkLoading, setSparkLoading] = useState(true);
  const [restartModal, setRestartModal] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  useEffect(() => {
    setSparkLoading(true);
    fetch(`/api/system/heartbeats/${component}/sparkline`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { setSparkline(data); setSparkLoading(false); })
      .catch(() => setSparkLoading(false));
  }, [component]);

  const ledColor = LED_COLOR[health.status];

  return createPortal(
    <div className="sph-drill-overlay" onClick={onClose} role="dialog" aria-modal="true"
      aria-label={`${component} heartbeat detail`}>
      <div className="sph-drill-card" onClick={e => e.stopPropagation()}>
        <div className="sph-drill-header">
          <span
            className={`sph-led sph-led-${health.status}${health.status === 'error' ? ' sph-pulse' : ''}`}
            style={{ backgroundColor: ledColor }}
            aria-label={`status: ${health.status}`}
          />
          <span className="sph-drill-title">
            <TermLabel term={COMPONENT_TERM[component] ?? component.toUpperCase()} />
          </span>
          <button className="sph-drill-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <table className="sph-drill-table">
          <tbody>
            <tr>
              <td>Status</td>
              <td><span className={`sph-status-badge sph-status-${health.status}`}>{health.status.toUpperCase()}</span></td>
            </tr>
            <tr>
              <td>Last heartbeat</td>
              <td>{health.last_ts ?? <span className="sph-na">never</span>}</td>
            </tr>
            <tr>
              <td>Age</td>
              <td>{fmtAge(health.age_sec)}</td>
            </tr>
            <tr>
              <td>Expected cadence</td>
              <td>every {health.expected_max_age_sec}s</td>
            </tr>
            {health.pid != null && (
              <tr>
                <td>PID</td>
                <td>{health.pid}</td>
              </tr>
            )}
            {health.uptime_sec != null && (
              <tr>
                <td>Uptime</td>
                <td>{fmtUptime(health.uptime_sec)}</td>
              </tr>
            )}
            {health.detail && (
              <tr>
                <td>Last detail</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{health.detail}</td>
              </tr>
            )}
          </tbody>
        </table>

        <div className="sph-sparkline-label">Last 10 heartbeats</div>
        {sparkLoading
          ? <div className="sph-sparkline-empty">Loading…</div>
          : <Sparkline rows={sparkline} />}

        {RESTARTABLE.has(component) && (
          <div style={{ marginTop: 12 }}>
            <button
              className="sph-btn-restart"
              onClick={() => setRestartModal(true)}
              data-testid="restart-service-btn"
            >
              Restart this service
            </button>
          </div>
        )}

        {toast && (
          <div className={`sph-toast ${toast.ok ? 'sph-toast-ok' : 'sph-toast-err'}`}
            role="alert">{toast.msg}</div>
        )}
      </div>

      {restartModal && (
        <RestartModal
          component={component}
          onClose={() => setRestartModal(false)}
          onSuccess={msg => { setToast({ msg, ok: true }); setTimeout(() => setToast(null), 4000); }}
          onError={msg => { setToast({ msg, ok: false }); setTimeout(() => setToast(null), 6000); }}
        />
      )}
    </div>,
    document.body,
  );
};

// ── SystemHealthPanel ─────────────────────────────────────────────────────────

const COMPONENT_ORDER = [
  'publisher',
  'intel_refresh',
  'options_chain_api',
  'premarket',
  'congress_trades',
  'correlation_regime',
  'macro_regime',
  'engine_subscriber',
  'engine_ibkr_status',
];

export interface SystemHealthPanelProps {
  onHealthChange?: (summary: { total: number; ok: number; degraded: number; error: number }) => void;
}

const SystemHealthPanel = ({ onHealthChange }: SystemHealthPanelProps) => {
  const [data, setData] = useState<HeartbeatsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [drillComponent, setDrillComponent] = useState<string | null>(null);

  const fetchHeartbeats = useCallback(async () => {
    try {
      const res = await fetch('/api/system/heartbeats');
      if (res.ok) {
        const payload: HeartbeatsPayload = await res.json();
        setData(payload);
        setLoading(false);

        // Notify parent of aggregate health
        if (onHealthChange) {
          const comps = Object.values(payload.components);
          const total = comps.length;
          const ok = comps.filter(c => c.status === 'ok').length;
          const degraded = comps.filter(c => c.status === 'degraded').length;
          const error = comps.filter(c => c.status === 'error').length;
          onHealthChange({ total, ok, degraded, error });
        }
      }
    } catch {
      setLoading(false);
    }
  }, [onHealthChange]);

  useEffect(() => {
    fetchHeartbeats();
    const id = setInterval(fetchHeartbeats, 15000);
    return () => clearInterval(id);
  }, [fetchHeartbeats]);

  const components = data?.components ?? {};

  return (
    <div className="sph-panel" role="region" aria-label="System Health">
      <div className="sph-panel-header">
        <span className="sph-panel-title">
          SYSTEM HEALTH
        </span>
        {loading && <span className="sph-loading">loading…</span>}
      </div>

      <div className="sph-grid" role="list">
        {COMPONENT_ORDER.map(comp => {
          const health = components[comp];
          if (!health) return null;
          const ledColor = LED_COLOR[health.status];
          const termKey = COMPONENT_TERM[comp] ?? comp.toUpperCase();

          return (
            <button
              key={comp}
              role="listitem"
              className={`sph-row sph-row-${health.status}`}
              onClick={() => setDrillComponent(comp)}
              aria-label={`${comp}: ${health.status}${health.age_sec != null ? `, ${fmtAge(health.age_sec)}` : ''}`}
              data-testid={`sph-row-${comp}`}
            >
              <span
                className={`sph-led sph-led-${health.status}${health.status === 'error' ? ' sph-pulse' : ''}`}
                style={{ backgroundColor: ledColor }}
                aria-hidden="true"
              />
              <span className="sph-comp-name">
                <TermLabel term={termKey} />
              </span>
              <span className="sph-age">
                {health.age_sec != null ? fmtAge(health.age_sec) : 'never'}
              </span>
              <span className="sph-cadence">
                every {health.expected_max_age_sec}s
              </span>
            </button>
          );
        })}
      </div>

      {drillComponent && components[drillComponent] && (
        <DrillDown
          component={drillComponent}
          health={components[drillComponent]}
          onClose={() => setDrillComponent(null)}
        />
      )}
    </div>
  );
};

// ── SystemHealthBadge — always-visible header badge ───────────────────────────

export interface HealthSummary {
  total: number;
  ok: number;
  degraded: number;
  error: number;
}

export interface SystemHealthBadgeProps {
  summary: HealthSummary | null;
  onClick?: () => void;
  ariaExpanded?: boolean;
}

export const SystemHealthBadge = ({ summary, onClick, ariaExpanded }: SystemHealthBadgeProps) => {
  if (!summary) return null;

  const { total, ok, degraded, error } = summary;
  const allOk = error === 0 && degraded === 0;
  const anyError = error > 0;

  let bg = '#1a7f37';
  let border = '#3fb950';
  let label = `SYS ${ok}/${total} ok`;
  if (anyError) {
    bg = '#6e1919';
    border = '#f85149';
    label = `SYS ⚠ ${error} error${error > 1 ? 's' : ''}`;
  } else if (degraded > 0) {
    bg = '#9e6a03';
    border = '#d29922';
    label = `SYS ${degraded} degraded`;
  }

  return (
    <button
      className={`sph-badge${anyError ? ' sph-badge-pulse' : ''}`}
      style={{ background: bg, border: `1px solid ${border}` }}
      onClick={onClick}
      aria-label={`System health: ${label}`}
      aria-expanded={ariaExpanded}
      aria-haspopup="dialog"
      data-testid="system-health-badge"
      title="System Health — click for per-component detail"
    >
      {label}
    </button>
  );
};

export default SystemHealthPanel;
