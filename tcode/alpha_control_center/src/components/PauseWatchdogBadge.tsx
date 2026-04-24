/**
 * PauseWatchdogBadge — Phase 21
 *
 * Shows the pause leak watchdog status as a small badge in the UI.
 * Polls /api/pause/watchdog-status every 10 seconds.
 *
 * States:
 *   ok=true  → green "Silent" badge (no leaks detected while paused)
 *   ok=false → red "LEAK DETECTED" badge with leak count
 *   daemon not running → grey "Watchdog off" badge
 */
import { useState, useEffect } from 'react';

interface WatchdogStatus {
  ok: boolean;
  paused: boolean;
  leak_count: number;
  leaks: Array<{ ts: number; fn: string; module: string }>;
  last_checked: number;
}

const POLL_MS = 10_000;

export default function PauseWatchdogBadge() {
  const [status, setStatus] = useState<WatchdogStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    const poll = async () => {
      try {
        const r = await fetch('/api/pause/watchdog-status');
        if (r.ok) {
          setStatus(await r.json());
          setError(false);
        } else {
          setError(true);
        }
      } catch {
        setError(true);
      }
      if (!cancelled) timer = setTimeout(poll, POLL_MS);
    };

    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  const baseStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    padding: '2px 8px',
    borderRadius: '10px',
    fontSize: '11px',
    fontWeight: 700,
    fontFamily: 'monospace',
    cursor: 'default',
    userSelect: 'none',
  };

  if (error || status === null) {
    return (
      <span
        style={{ ...baseStyle, background: '#30363d', color: '#8b949e', border: '1px solid #30363d' }}
        title="Pause watchdog daemon not running — start pause_leak_detector.py"
      >
        ◉ Watchdog off
      </span>
    );
  }

  if (!status.paused) {
    // System is unpaused — watchdog is monitoring but not actively checking for leaks
    return (
      <span
        style={{ ...baseStyle, background: '#0d1117', color: '#3fb950', border: '1px solid #238636' }}
        title="System is active (unpaused). Watchdog monitors during pause windows."
      >
        ◉ Active
      </span>
    );
  }

  if (status.ok) {
    return (
      <span
        style={{ ...baseStyle, background: '#0f2c1a', color: '#3fb950', border: '1px solid #238636' }}
        title="Paused & silent — no network leaks detected"
      >
        ✓ Silent
      </span>
    );
  }

  // Leak detected
  const leakFns = status.leaks.slice(-3).map(l => `${l.module}.${l.fn}`).join(', ');
  return (
    <span
      style={{ ...baseStyle, background: '#6e1919', color: '#f85149', border: '2px solid #f85149', animation: 'pulse 1s infinite' }}
      title={`LEAK: ${status.leak_count} blocked call(s) slipped through while paused.\nFunctions: ${leakFns}`}
    >
      ⚠ LEAK ({status.leak_count})
    </span>
  );
}
