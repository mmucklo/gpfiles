/**
 * PauseOverlay — Phase 16.1
 *
 * Full-screen semi-transparent overlay that appears when the publisher is paused.
 * User must click ACTIVATE to begin polling. Auto-pauses after the selected duration.
 *
 * State is persisted in localStorage and synced with the backend on load and on change.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import './PauseOverlay.css';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PauseStatus {
  paused: boolean;
  unpause_until: string | null;
  remaining_sec: number;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const DURATION_OPTIONS = [
  { label: '10m', minutes: 10 },
  { label: '30m', minutes: 30 },
  { label: '1h',  minutes: 60 },
  { label: '2h',  minutes: 120 },
];

const LS_KEY = 'tsla_pause_state';

// ── localStorage helpers ──────────────────────────────────────────────────────

function lsRead(): PauseStatus | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const s: PauseStatus = JSON.parse(raw);
    // Check expiry
    if (!s.paused && s.unpause_until) {
      const until = new Date(s.unpause_until).getTime();
      if (Date.now() > until) {
        return { paused: true, unpause_until: null, remaining_sec: 0 };
      }
      s.remaining_sec = Math.max(0, Math.round((until - Date.now()) / 1000));
    }
    return s;
  } catch {
    return null;
  }
}

function lsWrite(s: PauseStatus) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(s));
  } catch { /* ignore */ }
}

// ── Main export ───────────────────────────────────────────────────────────────

interface Props {
  /** Called whenever pause state changes so App can sync header timer */
  onStatusChange?: (s: PauseStatus) => void;
}

export default function PauseOverlay({ onStatusChange }: Props) {
  const [status, setStatus] = useState<PauseStatus>({ paused: true, unpause_until: null, remaining_sec: 0 });
  const [selectedDuration, setSelectedDuration] = useState(10);
  const [lastDataAgo, setLastDataAgo] = useState<string>('—');
  const [activating, setActivating] = useState(false);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Sync backend → local state ──────────────────────────────────────────────
  const syncFromBackend = useCallback(async () => {
    try {
      const r = await fetch('/api/system/pause-status');
      if (!r.ok) return;
      const s: PauseStatus = await r.json();
      // Recalculate remaining from unpause_until (server clock may differ slightly)
      if (!s.paused && s.unpause_until) {
        const until = new Date(s.unpause_until).getTime();
        s.remaining_sec = Math.max(0, Math.round((until - Date.now()) / 1000));
      } else {
        s.remaining_sec = 0;
      }
      setStatus(s);
      lsWrite(s);
      onStatusChange?.(s);
    } catch { /* ignore — overlay stays in current state */ }
  }, [onStatusChange]);

  // ── On mount: restore from localStorage, then sync backend ─────────────────
  useEffect(() => {
    const cached = lsRead();
    if (cached) {
      setStatus(cached);
      onStatusChange?.(cached);
    }
    syncFromBackend();
    // Poll backend every 10s to catch changes from other tabs or server restarts
    pollRef.current = setInterval(syncFromBackend, 10000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Countdown tick ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (tickRef.current) clearInterval(tickRef.current);
    if (!status.paused && status.remaining_sec > 0) {
      tickRef.current = setInterval(() => {
        setStatus(prev => {
          const next = { ...prev, remaining_sec: Math.max(0, prev.remaining_sec - 1) };
          if (next.remaining_sec === 0) {
            // Timer expired — auto-pause
            const paused: PauseStatus = { paused: true, unpause_until: null, remaining_sec: 0 };
            lsWrite(paused);
            onStatusChange?.(paused);
            return paused;
          }
          lsWrite(next);
          return next;
        });
      }, 1000);
    }
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, [status.paused, status.remaining_sec > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Track last data time (when active, record now; when paused, count up) ──
  const lastActiveRef = useRef<number | null>(null);
  useEffect(() => {
    if (!status.paused) {
      lastActiveRef.current = Date.now();
    }
  }, [status.paused]);

  useEffect(() => {
    const agoTimer = setInterval(() => {
      if (lastActiveRef.current === null) {
        setLastDataAgo('never');
        return;
      }
      const sec = Math.round((Date.now() - lastActiveRef.current) / 1000);
      if (sec < 60) setLastDataAgo(`${sec}s ago`);
      else if (sec < 3600) setLastDataAgo(`${Math.floor(sec / 60)}m ago`);
      else setLastDataAgo(`${Math.floor(sec / 3600)}h ago`);
    }, 5000);
    return () => clearInterval(agoTimer);
  }, []);

  // ── Activate handler ────────────────────────────────────────────────────────
  const handleActivate = useCallback(async () => {
    setActivating(true);
    try {
      const r = await fetch('/api/system/unpause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration_min: selectedDuration }),
      });
      if (!r.ok) return;
      const s: PauseStatus = await r.json();
      if (!s.paused && s.unpause_until) {
        const until = new Date(s.unpause_until).getTime();
        s.remaining_sec = Math.max(0, Math.round((until - Date.now()) / 1000));
      }
      setStatus(s);
      lsWrite(s);
      onStatusChange?.(s);
      lastActiveRef.current = Date.now();
      setLastDataAgo('just now');
    } finally {
      setActivating(false);
    }
  }, [selectedDuration, onStatusChange]);

  // ── 30s warning ─────────────────────────────────────────────────────────────
  const warned30Ref = useRef(false);
  useEffect(() => {
    if (!status.paused && status.remaining_sec <= 30 && status.remaining_sec > 0 && !warned30Ref.current) {
      warned30Ref.current = true;
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('TSLA Alpha', { body: 'Polling pauses in 30s — click ACTIVATE to extend.' });
      }
    }
    if (status.paused) {
      warned30Ref.current = false;
    }
  }, [status.paused, status.remaining_sec]);

  // ── Don't render overlay when active ────────────────────────────────────────
  if (!status.paused) return null;

  return (
    <div className="pause-overlay" data-testid="pause-overlay" role="dialog" aria-modal="true" aria-label="Publisher Paused">
      <div className="pause-overlay__backdrop" />
      <div className="pause-overlay__card">
        <div className="pause-overlay__icon">⏸</div>
        <h2 className="pause-overlay__title">PAUSED</h2>
        <p className="pause-overlay__desc">
          Data polling is paused to conserve API rate limits.
        </p>
        {lastActiveRef.current && (
          <p className="pause-overlay__last-data" data-testid="pause-last-data">
            Last data: {lastDataAgo}
          </p>
        )}

        <button
          className="pause-overlay__activate"
          onClick={handleActivate}
          disabled={activating}
          data-testid="pause-activate-btn"
          aria-label={`Activate polling for ${selectedDuration} minutes`}
        >
          {activating ? 'Activating…' : `▶ ACTIVATE   for  ${selectedDuration}m`}
        </button>

        <div className="pause-overlay__chips" role="group" aria-label="Duration options">
          {DURATION_OPTIONS.map(opt => (
            <button
              key={opt.minutes}
              className={`pause-overlay__chip${selectedDuration === opt.minutes ? ' pause-overlay__chip--active' : ''}`}
              onClick={() => setSelectedDuration(opt.minutes)}
              data-testid={`pause-duration-${opt.label}`}
              aria-pressed={selectedDuration === opt.minutes}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Countdown header widget (exported separately for App.tsx header) ──────────

interface CountdownProps {
  status: PauseStatus;
  onPause: () => void;
}

export function PauseCountdown({ status, onPause }: CountdownProps) {
  if (status.paused) return null;

  const min = Math.floor(status.remaining_sec / 60);
  const sec = status.remaining_sec % 60;
  const timeStr = `${min}:${String(sec).padStart(2, '0')}`;
  const isWarning = status.remaining_sec <= 30;

  return (
    <span
      className={`pause-countdown${isWarning ? ' pause-countdown--warning' : ''}`}
      data-testid="pause-countdown"
      aria-live="polite"
      aria-label={`Polling active, ${timeStr} remaining`}
    >
      <span className="pause-countdown__dot" aria-hidden="true" />
      ACTIVE {timeStr} remaining
      <button
        className="pause-countdown__pause-btn"
        onClick={onPause}
        data-testid="pause-header-btn"
        aria-label="Pause polling"
      >
        ⏸ Pause
      </button>
    </span>
  );
}
