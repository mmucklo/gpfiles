/**
 * RegimeMonitor — Phase 16 Intraday Cockpit
 *
 * Persistent widget showing current intraday regime + age + next re-eval.
 * On regime shift: flashes + modal popup with strategy recommendation.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import TermLabel from './TermLabel';

interface RegimeData {
  regime: string;
  color: string;
  confidence: number;
  recommended_strategy: string;
  fallback_strategy: string;
  refreshed_at: string;
  next_refresh_at: string;
  error?: string;
}

const REGIME_COLORS: Record<string, string> = {
  green:  '#3fb950',
  grey:   '#6e7681',
  amber:  '#d29922',
  blue:   '#79c0ff',
  red:    '#f85149',
};

const REGIME_BG: Record<string, string> = {
  green: 'rgba(63, 185, 80, 0.1)',
  grey:  'rgba(110, 118, 129, 0.1)',
  amber: 'rgba(210, 153, 34, 0.1)',
  blue:  'rgba(121, 192, 255, 0.1)',
  red:   'rgba(248, 81, 73, 0.1)',
};

interface Props {
  /** If compact, render as a small badge row (for header use) */
  compact?: boolean;
}

const RegimeMonitor: React.FC<Props> = ({ compact = false }) => {
  const [data, setData] = useState<RegimeData | null>(null);
  const [regimeAge, setRegimeAge] = useState('');
  const [nextIn, setNextIn] = useState('');
  const [shifted, setShifted] = useState(false);
  const [shiftModal, setShiftModal] = useState<{ from: string; to: string; recommended: string } | null>(null);
  const timerRef = useRef<number>(0);

  const fetchRegime = useCallback(async () => {
    try {
      const res = await fetch('/api/regime/current');
      if (!res.ok) return;
      const d = await res.json() as RegimeData;
      setData(prev => {
        // Detect regime shift
        if (prev && prev.regime && d.regime && prev.regime !== d.regime) {
          setShifted(true);
          setTimeout(() => setShifted(false), 3000);
          setShiftModal({ from: prev.regime, to: d.regime, recommended: d.recommended_strategy });
        }
        return d;
      });
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchRegime();
    const interval = setInterval(fetchRegime, 2 * 60 * 1000); // poll every 2 min
    return () => clearInterval(interval);
  }, [fetchRegime]);

  // Tick: update age display and next-refresh countdown
  useEffect(() => {
    timerRef.current = window.setInterval(() => {
      if (!data?.refreshed_at) return;

      const ageSec = Math.floor((Date.now() - new Date(data.refreshed_at).getTime()) / 1000);
      const ageMin = Math.floor(ageSec / 60);
      setRegimeAge(ageSec < 60 ? `${ageSec}s` : `${ageMin}m`);

      if (data.next_refresh_at) {
        const remaining = Math.max(0, Math.floor((new Date(data.next_refresh_at).getTime() - Date.now()) / 1000));
        const m = Math.floor(remaining / 60);
        const s = remaining % 60;
        setNextIn(`${m}:${String(s).padStart(2, '0')}`);
      }
    }, 1000);
    return () => clearInterval(timerRef.current);
  }, [data]);

  if (!data) {
    return compact ? (
      <span style={{ fontSize: '0.75rem', color: '#6e7681' }}>Regime: loading…</span>
    ) : null;
  }

  const regime = data.regime ?? 'UNCERTAIN';
  const color  = REGIME_COLORS[data.color ?? 'grey'] ?? '#6e7681';
  const bgCol  = REGIME_BG[data.color ?? 'grey'] ?? 'transparent';

  if (compact) {
    return (
      <>
        <span
          data-testid="regime-monitor-badge"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.35rem',
            padding: '0.25rem 0.7rem',
            borderRadius: '6px',
            fontSize: '0.75rem',
            fontWeight: 700,
            letterSpacing: '0.06em',
            backgroundColor: bgCol,
            color,
            border: `1px solid ${color}40`,
            animation: shifted ? 'regime-shift-flash 3s ease' : 'none',
          }}
          title={`Regime: ${regime} · ${(data.confidence * 100).toFixed(0)}% confidence · Next check in ${nextIn}`}
        >
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
          <TermLabel term="REGIME_CLASSIFIER">{regime}</TermLabel>
          {regimeAge && <span style={{ color: '#6e7681', fontWeight: 400 }}>{regimeAge}</span>}
        </span>

        {/* Regime shift modal */}
        {shiftModal && (
          <div
            style={{
              position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 10000,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onClick={() => setShiftModal(null)}
            role="dialog"
            aria-modal="true"
            aria-label="Regime shift alert"
            data-testid="regime-shift-modal"
          >
            <div
              style={{
                background: '#161b22', border: '2px solid #d29922', borderRadius: '12px',
                padding: '1.5rem 2rem', maxWidth: '420px', width: '90%',
              }}
              onClick={e => e.stopPropagation()}
            >
              <div style={{ fontSize: '1rem', fontWeight: 800, color: '#d29922', marginBottom: '0.75rem' }}>
                ⚡ REGIME SHIFTED
              </div>
              <div style={{ color: '#c9d1d9', marginBottom: '0.75rem' }}>
                <span style={{ color: '#f85149' }}>{shiftModal.from}</span>
                {' → '}
                <span style={{ color: '#3fb950' }}>{shiftModal.to}</span>
              </div>
              <div style={{ fontSize: '0.82rem', color: '#8b949e', marginBottom: '1rem' }}>
                Recommended strategy:{' '}
                <strong style={{ color: '#c9d1d9' }}>
                  <TermLabel term={shiftModal.recommended}>{shiftModal.recommended}</TermLabel>
                </strong>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  style={{
                    flex: 1, background: '#1a7f37', border: '1px solid #3fb950', color: 'white',
                    borderRadius: '6px', padding: '0.5rem', cursor: 'pointer', fontWeight: 700,
                    fontSize: '0.82rem', fontFamily: 'inherit',
                  }}
                  onClick={() => setShiftModal(null)}
                  data-testid="regime-shift-switch-btn"
                >
                  Switch to {shiftModal.recommended}
                </button>
                <button
                  style={{
                    flex: 1, background: '#21262d', border: '1px solid #30363d', color: '#8b949e',
                    borderRadius: '6px', padding: '0.5rem', cursor: 'pointer', fontWeight: 700,
                    fontSize: '0.82rem', fontFamily: 'inherit',
                  }}
                  onClick={() => setShiftModal(null)}
                  data-testid="regime-shift-keep-btn"
                >
                  Keep Current
                </button>
              </div>
            </div>
          </div>
        )}

        <style>{`
          @keyframes regime-shift-flash {
            0%, 20% { background: rgba(210, 153, 34, 0.35); }
            100% { background: ${bgCol}; }
          }
        `}</style>
      </>
    );
  }

  // Full widget (sidebar or panel use)
  return (
    <div
      data-testid="regime-monitor"
      style={{
        background: '#161b22',
        border: `1px solid ${color}40`,
        borderRadius: '8px',
        padding: '0.75rem 1rem',
      }}
    >
      <div style={{ fontSize: '0.68rem', color: '#6e7681', fontWeight: 700, letterSpacing: '0.08em', marginBottom: '0.4rem' }}>
        INTRADAY REGIME
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <span style={{ fontSize: '1.1rem', fontWeight: 900, color }}>
          <TermLabel term="REGIME_CLASSIFIER">{regime}</TermLabel>
        </span>
        <span style={{ fontSize: '0.72rem', color: '#8b949e' }}>
          {(data.confidence * 100).toFixed(0)}% conf
        </span>
        <span style={{ fontSize: '0.7rem', color: '#6e7681', marginLeft: 'auto' }}>
          {regimeAge} old
        </span>
      </div>
      {nextIn && (
        <div style={{ fontSize: '0.7rem', color: '#6e7681', marginTop: '0.25rem' }}>
          Next check in {nextIn}
        </div>
      )}
    </div>
  );
};

export default RegimeMonitor;
