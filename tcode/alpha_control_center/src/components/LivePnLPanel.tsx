/**
 * LivePnLPanel — Phase 16 Intraday Cockpit
 *
 * ★ Headline: huge P&L number + motivating progress bar toward $10k
 * ★ Waterfall chart (recharts BarChart) — story of the day
 * ★ Strategy breakdown donut + table
 * ★ Guardrail bars (daily loss limit, consecutive losses, circuit breaker)
 * ★ All new terms wrapped in TermLabel
 * ★ Updated timestamp on every panel, turns red if stale
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import './LivePnLPanel.css';
import TermLabel from './TermLabel';

// Recharts components — stored in a ref and loaded asynchronously on mount.
// This avoids top-level await (not supported in ES2020 targets) and prevents
// Rollup from erroring when the package isn't installed.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RechartsLib = Record<string, any>;

// ── Types ─────────────────────────────────────────────────────────────────────

interface WaterfallBar {
  time: string;
  strategy: string;
  pnl: number;
  cumul: number;
}

interface StrategyBreakdown {
  strategy: string;
  trades: number;
  winners: number;
  losers: number;
  net_pnl: number;
  win_rate: number;
}

interface PnLData {
  date: string;
  total_pnl: number;
  daily_target: number;
  target_pct: number;
  daily_loss_limit: number;
  loss_used_pct: number;
  circuit_broken: boolean;
  winners: number;
  losers: number;
  waterfall: WaterfallBar[];
  updated_at: string;
}

interface BreakdownData {
  date: string;
  strategies: StrategyBreakdown[];
}

// ── Colors ────────────────────────────────────────────────────────────────────

const STRATEGY_COLORS: Record<string, string> = {
  MOMENTUM:    '#3fb950',
  IRON_CONDOR: '#79c0ff',
  WAVE_RIDER:  '#d29922',
  JADE_LIZARD: '#bc8cff',
  STRADDLE:    '#ffa657',
  GAMMA_SCALP: '#39d353',
};

function stratColor(s: string) {
  return STRATEGY_COLORS[s] ?? '#58a6ff';
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatPnL(val: number): string {
  const abs = Math.abs(val);
  const sign = val >= 0 ? '+' : '-';
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(1)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

function formatAge(isoTs: string): string {
  if (!isoTs) return '';
  const diff = Math.floor((Date.now() - new Date(isoTs).getTime()) / 1000);
  if (diff < 5)  return 'just now';
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function barColor(pnl: number): string {
  return pnl >= 0 ? '#3fb950' : '#f85149';
}

function guardrailClass(pct: number): string {
  if (pct >= 90) return 'flashing';
  if (pct >= 75) return 'danger';
  if (pct >= 50) return 'warn';
  return '';
}

// ── Custom Waterfall Tooltip ──────────────────────────────────────────────────

const WaterfallTooltip = ({ active, payload }: { active?: boolean; payload?: Array<{ payload: WaterfallBar }> }) => {
  if (!active || !payload?.length) return null;
  const bar = payload[0].payload;
  const ts = new Date(bar.time).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  return (
    <div className="pnl-tooltip">
      <div className="pnl-tooltip-label">{ts} · {bar.strategy}</div>
      <div className={`pnl-tooltip-val ${bar.pnl >= 0 ? 'pos' : 'neg'}`}>
        P&L: {formatPnL(bar.pnl)}
      </div>
      <div style={{ fontSize: '0.72rem', color: '#8b949e', marginTop: '0.2rem' }}>
        Cumulative: {formatPnL(bar.cumul)}
      </div>
    </div>
  );
};

// ── Fallback chart (no recharts) ──────────────────────────────────────────────

const FallbackBars: React.FC<{ bars: WaterfallBar[] }> = ({ bars }) => {
  if (bars.length === 0) {
    return <div style={{ color: '#6e7681', fontSize: '0.82rem', textAlign: 'center', padding: '2rem' }}>
      Awaiting first trade — waterfall will appear after execution
    </div>;
  }
  const maxAbs = Math.max(...bars.map(b => Math.abs(b.pnl)), 1);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 180, padding: '0 4px' }}>
      {bars.map((b, i) => {
        const h = Math.max(4, (Math.abs(b.pnl) / maxAbs) * 160);
        return (
          <div key={i} title={`${b.strategy}: ${formatPnL(b.pnl)}`}
            style={{ flex: 1, height: h, background: barColor(b.pnl), borderRadius: 2, minWidth: 4 }} />
        );
      })}
    </div>
  );
};

// ── LivePnLPanel ──────────────────────────────────────────────────────────────

const LivePnLPanel: React.FC = () => {
  const [pnlData, setPnlData] = useState<PnLData | null>(null);
  const [breakdown, setBreakdown] = useState<BreakdownData | null>(null);
  const [age, setAge] = useState('');
  const rcRef = useRef<RechartsLib | null>(null);
  const [rcReady, setRcReady] = useState(false);

  // Async-load recharts after mount so no top-level await / Rollup resolution
  useEffect(() => {
    let cancelled = false;
    import(/* @vite-ignore */ 'recharts').then(rc => {
      if (!cancelled) {
        rcRef.current = rc;
        setRcReady(true);
      }
    }).catch(() => { /* recharts unavailable — use fallback */ });
    return () => { cancelled = true; };
  }, []);

  const today = new Date().toISOString().slice(0, 10);

  const fetchAll = useCallback(async () => {
    try {
      const [pnlRes, bdRes] = await Promise.all([
        fetch(`/api/trades/pnl?date=${today}`),
        fetch(`/api/trades/pnl/strategy-breakdown?date=${today}`),
      ]);
      if (pnlRes.ok) setPnlData(await pnlRes.json());
      if (bdRes.ok) setBreakdown(await bdRes.json());
    } catch {
      // silent
    }
  }, [today]);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 10000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  useEffect(() => {
    const t = setInterval(() => {
      if (pnlData?.updated_at) setAge(formatAge(pnlData.updated_at));
    }, 1000);
    return () => clearInterval(t);
  }, [pnlData?.updated_at]);

  const totalPnL    = pnlData?.total_pnl ?? 0;
  const targetPct   = Math.min(100, Math.max(0, pnlData?.target_pct ?? 0));
  const dailyTarget = pnlData?.daily_target ?? 10000;
  const lossUsedPct = Math.min(100, Math.max(0, pnlData?.loss_used_pct ?? 0));
  const circuit     = pnlData?.circuit_broken ?? false;
  const winners     = pnlData?.winners ?? 0;
  const losers      = pnlData?.losers ?? 0;
  const waterfall   = pnlData?.waterfall ?? [];
  const stale       = pnlData?.updated_at ? (Date.now() - new Date(pnlData.updated_at).getTime()) / 1000 > 30 : false;

  const pnlClass = totalPnL > 0 ? 'positive' : totalPnL < 0 ? 'negative' : 'zero';

  // Build donut data
  const bdStrategies = breakdown?.strategies ?? [];
  const donutData = bdStrategies
    .filter(s => s.net_pnl !== 0)
    .map(s => ({ name: s.strategy, value: Math.abs(s.net_pnl) }));

  // Waterfall chart data with color per bar
  const waterfallWithColor = waterfall.map(b => ({
    ...b,
    fill: barColor(b.pnl),
    timeLabel: new Date(b.time).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
  }));

  return (
    <div className="pnl-panel" data-testid="live-pnl-panel">

      {/* Circuit breaker banner — shown prominently when tripped */}
      {circuit && (
        <div className="circuit-breaker-banner" role="alert" data-testid="circuit-breaker-banner">
          <span className="cb-icon">🚨</span>
          <div>
            <div className="cb-text">
              <TermLabel term="CIRCUIT_BREAKER">CIRCUIT BREAKER</TermLabel>
              {' '}— NO MORE TRADES TODAY
            </div>
            <div className="cb-sub">Daily loss limit reached. Resume tomorrow at market open.</div>
          </div>
        </div>
      )}

      {/* A. Headline */}
      <div className="pnl-headline" data-testid="pnl-headline">
        <div className="pnl-panel-title">
          LIVE P&L —{' '}
          <TermLabel term="DAILY_PNL_TARGET">TARGET ${dailyTarget.toLocaleString()}</TermLabel>
        </div>
        <div className={`pnl-amount ${pnlClass}`} data-testid="pnl-amount">
          {totalPnL >= 0 ? '+' : ''}{totalPnL.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
        </div>

        <div style={{ display: 'flex', gap: '1.5rem', fontSize: '0.78rem', color: '#8b949e', marginBottom: '0.75rem' }}>
          <span><span style={{ color: '#3fb950', fontWeight: 700 }}>W:{winners}</span></span>
          <span><span style={{ color: '#f85149', fontWeight: 700 }}>L:{losers}</span></span>
          {(winners + losers) > 0 && (
            <span>Win rate: <span style={{ color: '#c9d1d9', fontWeight: 700 }}>
              {Math.round(winners / (winners + losers) * 100)}%
            </span></span>
          )}
        </div>

        {/* ★ Motivating progress bar toward target */}
        <div className="pnl-target-bar-wrapper">
          <div className="pnl-target-label">
            <span>Progress toward daily target</span>
            <span className="target-val">
              {targetPct.toFixed(0)}% of ${(dailyTarget / 1000).toFixed(0)}k
            </span>
          </div>
          <div className="pnl-target-track">
            <div
              className={`pnl-target-fill ${lossUsedPct > 75 ? 'danger' : lossUsedPct > 50 ? 'warning' : ''}`}
              style={{ width: `${totalPnL < 0 ? 0 : targetPct}%` }}
              data-testid="pnl-target-bar"
            />
          </div>
        </div>

        <div className={`pnl-updated ${stale ? 'stale' : ''}`}>
          Updated {age || '…'}{stale ? ' — stale' : ''}
        </div>
      </div>

      {/* B. Waterfall chart */}
      <div className="pnl-waterfall" data-testid="pnl-waterfall">
        <div className="pnl-panel-title">
          <TermLabel term="WATERFALL_CHART">P&L WATERFALL — TODAY</TermLabel>
        </div>

        <div className="pnl-chart-container">
          {rcReady && rcRef.current && waterfallWithColor.length > 0 ? (() => {
            const RC = rcRef.current!;
            return (
              <RC.ResponsiveContainer width="100%" height="100%">
                <RC.BarChart data={waterfallWithColor} barSize={waterfallWithColor.length > 20 ? 4 : 16}
                  margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <RC.XAxis dataKey="timeLabel" tick={{ fill: '#6e7681', fontSize: 10 }}
                    axisLine={false} tickLine={false} />
                  <RC.YAxis tick={{ fill: '#6e7681', fontSize: 10 }}
                    axisLine={false} tickLine={false}
                    tickFormatter={(v: number) => `$${v >= 0 ? '' : '-'}${Math.abs(v).toLocaleString()}`} />
                  <RC.Tooltip content={<WaterfallTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                  <RC.Bar dataKey="cumul" radius={[2, 2, 0, 0]}>
                    {waterfallWithColor.map((entry, i) => (
                      <RC.Cell key={i} fill={entry.fill} />
                    ))}
                  </RC.Bar>
                </RC.BarChart>
              </RC.ResponsiveContainer>
            );
          })() : (
            <FallbackBars bars={waterfall} />
          )}
        </div>
      </div>

      {/* C. Strategy breakdown */}
      <div className="pnl-breakdown" data-testid="pnl-breakdown">
        <div className="pnl-panel-title">STRATEGY BREAKDOWN</div>

        {bdStrategies.length === 0 ? (
          <div style={{ color: '#6e7681', fontSize: '0.82rem', padding: '1rem 0' }}>
            Awaiting first executed trade
          </div>
        ) : (
          <div className="pnl-breakdown-grid">
            {/* Donut */}
            {rcReady && rcRef.current && donutData.length > 0 ? (() => {
              const RC = rcRef.current!;
              return (
                <div className="pnl-donut-container">
                  <RC.PieChart width={120} height={120}>
                    <RC.Pie data={donutData} cx={55} cy={55} innerRadius={32} outerRadius={54}
                      dataKey="value" paddingAngle={2}>
                      {donutData.map((d, i) => (
                        <RC.Cell key={i} fill={stratColor(d.name)} />
                      ))}
                    </RC.Pie>
                  </RC.PieChart>
                </div>
              );
            })() : (
              <div style={{ width: 80 }} />
            )}

            {/* Table */}
            <table className="pnl-breakdown-table" data-testid="strategy-breakdown-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Trades</th>
                  <th>W/L</th>
                  <th>Net P&L</th>
                  <th>Win%</th>
                </tr>
              </thead>
              <tbody>
                {bdStrategies.map(s => (
                  <tr key={s.strategy}>
                    <td style={{ color: stratColor(s.strategy) }}>
                      <TermLabel term={s.strategy}>{s.strategy.replace('_', ' ')}</TermLabel>
                    </td>
                    <td>{s.trades}</td>
                    <td>{s.winners}/{s.losers}</td>
                    <td className={`td-pnl ${s.net_pnl >= 0 ? 'pos' : 'neg'}`}>
                      {s.net_pnl >= 0 ? '+' : ''}{s.net_pnl.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
                    </td>
                    <td>{(s.win_rate ?? 0).toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* D. Guardrail status */}
      <div className="pnl-guardrails" data-testid="pnl-guardrails">
        <div className="pnl-panel-title">GUARDRAILS</div>

        {/* Daily loss limit */}
        <div className="guardrail-item" data-testid="guardrail-loss-limit">
          <div className="guardrail-label-row">
            <span className="guardrail-label">
              <TermLabel term="CIRCUIT_BREAKER">Daily Loss Limit</TermLabel>
            </span>
            <span className="guardrail-value">
              ${Math.abs(totalPnL < 0 ? totalPnL : 0).toLocaleString()} of ${Math.abs(pnlData?.daily_loss_limit ?? -2500).toLocaleString()} used
            </span>
          </div>
          <div className="guardrail-track">
            <div className={`guardrail-fill ${guardrailClass(lossUsedPct)}`}
              style={{ width: `${Math.max(2, lossUsedPct)}%` }}
              data-testid="guardrail-loss-bar" />
          </div>
        </div>

        {/* Consecutive losses */}
        <div className="guardrail-item" data-testid="guardrail-consec-losses">
          <div className="guardrail-label-row">
            <span className="guardrail-label">
              <TermLabel term="CONSECUTIVE_LOSS_LIMIT">Consecutive Losses</TermLabel>
            </span>
            <span className="guardrail-value">{losers > 0 && winners === 0 ? losers : 0}/3</span>
          </div>
          <div className="consec-loss-pips">
            {[0, 1, 2].map(i => (
              <div key={i} className={`consec-loss-pip ${losers > 0 && winners === 0 && i < losers ? 'filled' : ''}`} />
            ))}
          </div>
        </div>
      </div>

    </div>
  );
};

export default LivePnLPanel;
