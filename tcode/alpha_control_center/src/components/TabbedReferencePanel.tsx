/**
 * TabbedReferencePanel — Phase 18 UI Overhaul (Zone C)
 *
 * Replaces 6+ stacked intel panels + signal history + activity feed
 * with a single tabbed container.
 *
 * Tabs (in frequency-of-use order):
 *   [Pre-Market] [Macro] [Correlation] [Chop] [EV/Congress] [Signals] [Activity]
 *
 * Features:
 * - Dot indicators (pulsing when fresh data / alert condition)
 * - Lazy-load: only renders active tab content
 * - Signal Log and Activity default to 3 items, "Show all" expander
 * - Color law: #00C853 = profit ONLY, #FF1744 = loss ONLY
 */

import { useState, useEffect, useCallback, useRef, memo } from 'react';
import './TabbedReferencePanel.css';
import TermLabel from './TermLabel';

// ── Types ─────────────────────────────────────────────────────────────────────

type TabId = 'premarket' | 'macro' | 'correlation' | 'chop' | 'evcongress' | 'signals' | 'activity';

interface IntelData {
  fetch_timestamp?: number;
  news?: { headlines: string[]; sentiment_score: number; headline_count: number; bull_hits: number; bear_hits: number };
  vix?: { vix_level: number | null; vix_status: string };
  spy?: { spy_price: number | null; spy_change_pct: number };
  earnings?: { next_earnings_date: string | null; days_until_earnings: number | null };
  options_flow?: { pc_ratio: number; pc_signal: string; total_call_oi: number; total_put_oi: number };
  premarket?: {
    is_premarket?: boolean;
    futures_bias?: string;
    es_change_pct?: number;
    nq_change_pct?: number;
    europe_direction?: string;
    tsla_premarket_change_pct?: number;
    tsla_premarket_volume?: number;
    overnight_catalyst?: string;
  };
  congress?: {
    signal?: string;
    recent_trades?: Array<{ member: string; ticker: string; transaction_type: string; amount: string; disclosure_date: string }>;
  };
  correlation_regime?: {
    regime?: string;
    correlation?: number;
    signal_modifier?: number;
    description?: string;
  };
  chop_regime?: {
    label?: string;
    score?: number;
    components?: Record<string, number>;
    description?: string;
  };
  error?: string;
}

interface Signal {
  timestamp: number;
  action: string;
  direction: string;
  strategy_code?: string;
  confidence: number;
  exec_status?: string;
  recommended_strike?: number;
  option_type?: string;
  ticker?: string;
}

interface FeedbackRow {
  id: number;
  signal_id: string;
  ts_feedback: string;
  user_comment: string;
  tag: string | null;
  action: string;
}

interface AuditEntry {
  ts: string;
  level: string;
  message: string;
  source?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatAge(epochSec: number): string {
  const diff = Math.floor(Date.now() / 1000 - epochSec);
  if (diff < 5)  return 'just now';
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function changePctSpan(pct: number | undefined | null): string {
  if (pct == null) return '—';
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

// ── Tab definitions ───────────────────────────────────────────────────────────

const TABS: Array<{ id: TabId; label: string; title: string }> = [
  { id: 'premarket',   label: 'Pre-Market',  title: 'Pre-Market Intelligence' },
  { id: 'macro',       label: 'Macro',       title: 'Macro Pulse' },
  { id: 'correlation', label: 'Correlation', title: 'TSLA↔Mag7 Correlation' },
  { id: 'chop',        label: 'Chop',        title: 'Market-Chop Regime' },
  { id: 'evcongress',  label: 'EV/Congress', title: 'Sector & Flow' },
  { id: 'signals',     label: 'Signals',     title: 'Signal Log' },
  { id: 'activity',    label: 'Activity',    title: 'Activity Feed' },
];

// ── Tab content components ────────────────────────────────────────────────────

const PreMarketTab: React.FC<{ intel: IntelData | null }> = ({ intel }) => {
  const pm = intel?.premarket;
  const biasColor = (b?: string) => b === 'BULLISH' ? '#00C853' : b === 'BEARISH' ? '#FF1744' : '#8b949e';

  return (
    <div className="tab-content" data-testid="premarket-tab-content">
      {!pm ? (
        <div className="tab-empty">Pre-market data loading…</div>
      ) : (
        <div className="tab-grid-2">
          <div className="tab-card">
            <div className="tab-card-title">Futures Bias</div>
            <div className="tab-stat-row">
              <span className="tab-stat-label">Overall bias</span>
              <span style={{ color: biasColor(pm.futures_bias), fontWeight: 700 }}>{pm.futures_bias ?? '—'}</span>
            </div>
            <div className="tab-stat-row">
              <span className="tab-stat-label">ES (S&P futures)</span>
              <span className={`tab-change ${(pm.es_change_pct ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                {changePctSpan(pm.es_change_pct)}
              </span>
            </div>
            <div className="tab-stat-row">
              <span className="tab-stat-label">NQ (Nasdaq futures)</span>
              <span className={`tab-change ${(pm.nq_change_pct ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                {changePctSpan(pm.nq_change_pct)}
              </span>
            </div>
            <div className="tab-stat-row">
              <span className="tab-stat-label">Europe</span>
              <span style={{ color: biasColor(pm.europe_direction) }}>{pm.europe_direction ?? '—'}</span>
            </div>
          </div>
          <div className="tab-card">
            <div className="tab-card-title">TSLA Pre-Market</div>
            <div className="tab-stat-row">
              <span className="tab-stat-label">Change %</span>
              <span className={`tab-change ${(pm.tsla_premarket_change_pct ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                {changePctSpan(pm.tsla_premarket_change_pct)}
              </span>
            </div>
            <div className="tab-stat-row">
              <span className="tab-stat-label">Volume</span>
              <span className="tab-value">
                {pm.tsla_premarket_volume && pm.tsla_premarket_volume > 0
                  ? pm.tsla_premarket_volume >= 1_000_000
                    ? `${(pm.tsla_premarket_volume / 1_000_000).toFixed(1)}M`
                    : `${(pm.tsla_premarket_volume / 1000).toFixed(0)}K`
                  : '—'}
              </span>
            </div>
            {pm.overnight_catalyst && (
              <div className="tab-catalyst-note" data-testid="premarket-catalyst">
                ⚡ {pm.overnight_catalyst}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const MacroTab: React.FC<{ intel: IntelData | null }> = ({ intel }) => {
  if (!intel?.vix) return <div className="tab-empty">Macro data loading…</div>;
  const { vix, spy, news, options_flow, earnings } = intel;
  const staleLabel = intel.fetch_timestamp ? formatAge(intel.fetch_timestamp) : '—';
  const sentPct = news ? Math.round(((news.sentiment_score + 1) / 2) * 100) : 50;
  const sentColor = (news?.sentiment_score ?? 0) > 0.3 ? '#00C853' : (news?.sentiment_score ?? 0) < -0.3 ? '#FF1744' : '#FFB300';

  return (
    <div className="tab-content" data-testid="macro-tab-content">
      <div className="tab-grid-2">
        <div className="tab-card">
          <div className="tab-card-title">Market Pulse</div>
          <div className="tab-stat-row">
            <span className="tab-stat-label"><TermLabel term="VIX">VIX</TermLabel></span>
            <span className={`tab-vix-badge ${(vix?.vix_status ?? '').toLowerCase()}`}>
              {vix?.vix_level?.toFixed(1) ?? '—'} {vix?.vix_status}
            </span>
          </div>
          <div className="tab-stat-row">
            <span className="tab-stat-label">SPY</span>
            <span>
              {spy?.spy_price != null ? `$${spy.spy_price.toFixed(2)}` : '—'}
              {' '}
              <span className={`tab-change ${(spy?.spy_change_pct ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                ({changePctSpan(spy?.spy_change_pct)})
              </span>
            </span>
          </div>
          {earnings?.days_until_earnings != null && (
            <div className="tab-stat-row">
              <span className="tab-stat-label"><TermLabel term="EARNINGS_DATES">Earnings</TermLabel></span>
              <span className={earnings.days_until_earnings <= 2 ? 'tab-urgent' : earnings.days_until_earnings <= 7 ? 'tab-warn' : ''}>
                {earnings.next_earnings_date} ({earnings.days_until_earnings}d)
              </span>
            </div>
          )}
          <div className="tab-stale">Updated {staleLabel}</div>
        </div>

        <div className="tab-card">
          <div className="tab-card-title">News Sentiment</div>
          <div className="tab-sentiment-bar-row">
            <span style={{ color: '#FF1744', fontSize: '0.7rem' }}>BEAR</span>
            <div className="tab-sentiment-track">
              <div className="tab-sentiment-fill" style={{ width: `${sentPct}%`, background: sentColor }} />
            </div>
            <span style={{ color: '#00C853', fontSize: '0.7rem' }}>BULL</span>
          </div>
          <div className="tab-stat-row" style={{ marginTop: '0.4rem' }}>
            <span className="tab-stat-label">Score</span>
            <span style={{ color: sentColor, fontWeight: 700 }}>{(news?.sentiment_score ?? 0).toFixed(2)}</span>
          </div>
          <div className="tab-stat-row">
            <span className="tab-stat-label">Headlines</span>
            <span>{news?.headline_count ?? 0} ({news?.bull_hits ?? 0}↑ {news?.bear_hits ?? 0}↓)</span>
          </div>
          {options_flow && (
            <div className="tab-stat-row">
              <span className="tab-stat-label"><TermLabel term="PUT_CALL_RATIO">P/C Ratio</TermLabel></span>
              <span className={`tab-pc-badge ${options_flow.pc_signal?.toLowerCase() ?? ''}`}>
                {options_flow.pc_ratio != null ? options_flow.pc_ratio.toFixed(2) : '—'} {options_flow.pc_signal}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const CorrelationTab: React.FC<{ intel: IntelData | null }> = ({ intel }) => {
  const corr = intel?.correlation_regime;
  if (!corr) return <div className="tab-empty">Correlation data loading…</div>;

  const corrPct = Math.round(Math.abs(corr.correlation ?? 0) * 100);
  const corrColor = corrPct > 70 ? '#00C853' : corrPct > 40 ? '#FFB300' : '#FF1744';

  return (
    <div className="tab-content" data-testid="correlation-tab-content">
      <div className="tab-card" style={{ maxWidth: 480 }}>
        <div className="tab-card-title">
          <TermLabel term="CORRELATION_REGIME">TSLA ↔ Mag7 Correlation</TermLabel>
        </div>
        <div className="tab-stat-row">
          <span className="tab-stat-label">Regime</span>
          <span style={{ color: corrColor, fontWeight: 700 }}>{corr.regime ?? '—'}</span>
        </div>
        <div className="tab-stat-row">
          <span className="tab-stat-label">Correlation</span>
          <span>
            <div className="tab-bar-inline">
              <div className="tab-bar-fill" style={{ width: `${corrPct}%`, background: corrColor }} />
            </div>
            <span style={{ marginLeft: '0.4rem', color: corrColor }}>{corrPct}%</span>
          </span>
        </div>
        <div className="tab-stat-row">
          <span className="tab-stat-label">Signal modifier</span>
          <span style={{ color: '#58a6ff', fontWeight: 700 }}>
            {corr.signal_modifier != null
              ? `${corr.signal_modifier > 0 ? '+' : ''}${(corr.signal_modifier * 100).toFixed(0)}%`
              : '—'}
          </span>
        </div>
        {corr.description && (
          <div className="tab-description">{corr.description}</div>
        )}
      </div>
    </div>
  );
};

const ChopTab: React.FC<{ intel: IntelData | null }> = ({ intel }) => {
  const chop = intel?.chop_regime;
  if (!chop) return <div className="tab-empty">Chop regime data loading…</div>;

  const chopColor = chop.label === 'TRENDING' ? '#00C853' : chop.label === 'MIXED' ? '#FFB300' : '#FF1744';
  const scorePct = Math.min(100, Math.round((chop.score ?? 0) * 100));

  return (
    <div className="tab-content" data-testid="chop-tab-content">
      <div className="tab-card" style={{ maxWidth: 480 }}>
        <div className="tab-card-title">
          <TermLabel term="CHOP_REGIME">Market-Chop Regime</TermLabel>
        </div>
        <div className="tab-stat-row">
          <span className="tab-stat-label">Label</span>
          <span style={{ color: chopColor, fontWeight: 800, fontSize: '1rem' }}>{chop.label ?? '—'}</span>
        </div>
        <div className="tab-stat-row">
          <span className="tab-stat-label">Score</span>
          <span>
            <div className="tab-bar-inline">
              <div className="tab-bar-fill" style={{ width: `${scorePct}%`, background: chopColor }} />
            </div>
            <span style={{ marginLeft: '0.4rem', color: chopColor }}>{scorePct}</span>
          </span>
        </div>
        {chop.components && Object.entries(chop.components).map(([k, v]) => (
          <div className="tab-stat-row" key={k}>
            <span className="tab-stat-label">{k.replace(/_/g, ' ')}</span>
            <span style={{ color: v > 0 ? '#00C853' : '#FF1744' }}>
              {v > 0 ? '+' : ''}{(v * 100).toFixed(0)}
            </span>
          </div>
        ))}
        {chop.description && (
          <div className="tab-description">{chop.description}</div>
        )}
      </div>
    </div>
  );
};

const EvCongressTab: React.FC<{ intel: IntelData | null }> = ({ intel }) => {
  const congress = intel?.congress;

  return (
    <div className="tab-content" data-testid="evcongress-tab-content">
      <div className="tab-card">
        <div className="tab-card-title">
          <TermLabel term="CONGRESS_TRADES">Congress STOCK Act Disclosures</TermLabel>
        </div>
        {!congress ? (
          <div className="tab-empty">Congressional data loading…</div>
        ) : (
          <>
            <div className="tab-stat-row">
              <span className="tab-stat-label">Signal</span>
              <span className={`tab-congress-sig ${(congress.signal ?? '').toLowerCase()}`}>
                {congress.signal ?? '—'}
              </span>
            </div>
            {(congress.recent_trades ?? []).length > 0 ? (
              <div className="tab-congress-list">
                {(congress.recent_trades ?? []).slice(0, 5).map((t, i) => (
                  <div key={i} className="tab-congress-row" data-testid="congress-trade-row">
                    <span className="tab-congress-member">{t.member}</span>
                    <span className={`tab-congress-txn ${t.transaction_type.toLowerCase().includes('purchase') ? 'buy' : 'sell'}`}>
                      {t.transaction_type}
                    </span>
                    <span className="tab-congress-amount">{t.amount}</span>
                    <span className="tab-congress-date">{t.disclosure_date}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="tab-empty">No recent TSLA congressional disclosures</div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

type SignalFilter = 'all' | 'executed' | 'rejected';

const SignalsTab: React.FC<{ signals: Signal[]; isLoading: boolean }> = ({ signals, isLoading }) => {
  const [filter, setFilter] = useState<SignalFilter>('all');
  const [expanded, setExpanded] = useState(false);
  const COLLAPSED_COUNT = 3;

  const filtered = (() => {
    if (filter === 'executed') return signals.filter(s => s.exec_status === 'submitted' || s.exec_status === 'sim_filled');
    if (filter === 'rejected')  return signals.filter(s => s.exec_status === 'rejected');
    return signals;
  })();

  const displayed = expanded ? filtered : filtered.slice(0, COLLAPSED_COUNT);

  if (isLoading) return <div className="tab-empty">Loading signals…</div>;

  return (
    <div className="tab-content" data-testid="signals-tab-content">
      <div className="tab-filter-row">
        {(['all', 'executed', 'rejected'] as SignalFilter[]).map(f => (
          <button
            key={f}
            className={`tab-filter-btn ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}
            data-testid={`signal-filter-${f}`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="tab-empty">No {filter === 'all' ? '' : filter + ' '}signals yet</div>
      ) : (
        <>
          <div className="tab-signal-list" data-testid="signal-list">
            {displayed.map((s, i) => {
              const confPct = Math.round((s.confidence ?? 0) * 100);
              const isBull = s.direction === 'BULLISH';
              const ts = new Date(s.timestamp * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
              return (
                <div
                  key={`${s.timestamp}-${i}`}
                  className={`tab-signal-row ${isBull ? 'bullish' : 'bearish'}`}
                  data-testid="signal-row"
                >
                  <span className={`tab-sig-dir ${isBull ? 'bullish' : 'bearish'}`}>{isBull ? '▲' : '▼'}</span>
                  <span className="tab-sig-strat">{s.strategy_code?.replace('_', ' ') ?? s.action}</span>
                  <span className="tab-sig-type">{s.option_type ?? ''} {s.recommended_strike ? `$${s.recommended_strike}` : ''}</span>
                  <span className="tab-sig-conf" title={`${confPct}% confidence`}>{confPct}%</span>
                  {s.exec_status && (
                    <span className={`tab-sig-status ${s.exec_status}`}>{s.exec_status}</span>
                  )}
                  <span className="tab-sig-time">{ts}</span>
                </div>
              );
            })}
          </div>

          {filtered.length > COLLAPSED_COUNT && (
            <button
              className="tab-expander"
              onClick={() => setExpanded(v => !v)}
              data-testid="signal-log-expander"
              aria-expanded={expanded}
            >
              {expanded
                ? '▲ Show fewer'
                : `▼ Show all (${filtered.length} signals)`}
            </button>
          )}
        </>
      )}
    </div>
  );
};

type ActivityFilter = 'all' | 'feedback' | 'audit';

const ActivityTab: React.FC<{ feedback: FeedbackRow[]; audit: AuditEntry[]; isLoading: boolean }> = ({
  feedback, audit, isLoading,
}) => {
  const [filter, setFilter] = useState<ActivityFilter>('all');
  const [expanded, setExpanded] = useState(false);
  const COLLAPSED_COUNT = 3;

  // Merge and sort by time, newest first
  const merged = (() => {
    const items: Array<{ ts: string; type: 'feedback' | 'audit'; label: string; sub: string; level?: string }> = [];
    if (filter !== 'audit') {
      feedback.forEach(r => items.push({
        ts: r.ts_feedback,
        type: 'feedback',
        label: `${r.action} · ${r.user_comment || r.signal_id}`,
        sub: r.tag ?? '',
      }));
    }
    if (filter !== 'feedback') {
      audit.forEach(e => items.push({
        ts: e.ts,
        type: 'audit',
        label: e.message,
        sub: e.source ?? '',
        level: e.level,
      }));
    }
    return items.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
  })();

  const displayed = expanded ? merged : merged.slice(0, COLLAPSED_COUNT);

  const levelColor = (level?: string) => {
    if (level === 'ERROR')   return '#FF1744';
    if (level === 'WARNING') return '#FFB300';
    return '#8b949e';
  };

  if (isLoading) return <div className="tab-empty">Loading activity…</div>;

  return (
    <div className="tab-content" data-testid="activity-tab-content">
      <div className="tab-filter-row">
        {(['all', 'feedback', 'audit'] as ActivityFilter[]).map(f => (
          <button
            key={f}
            className={`tab-filter-btn ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}
            data-testid={`activity-filter-${f}`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {merged.length === 0 ? (
        <div className="tab-empty">No activity yet</div>
      ) : (
        <>
          <div className="tab-activity-list" data-testid="activity-list">
            {displayed.map((item, i) => (
              <div
                key={i}
                className={`tab-activity-row ${item.type}`}
                data-testid="activity-row"
              >
                <span className="tab-act-type-badge">{item.type === 'feedback' ? '💬' : '📋'}</span>
                <span className="tab-act-label" style={{ color: levelColor(item.level) }}>{item.label}</span>
                {item.sub && <span className="tab-act-sub">{item.sub}</span>}
                <span className="tab-act-time">
                  {new Date(item.ts).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>

          {merged.length > COLLAPSED_COUNT && (
            <button
              className="tab-expander"
              onClick={() => setExpanded(v => !v)}
              data-testid="activity-expander"
              aria-expanded={expanded}
            >
              {expanded ? '▲ Show fewer' : `▼ Show all (${merged.length} items)`}
            </button>
          )}
        </>
      )}
    </div>
  );
};

// ── TabbedReferencePanel ──────────────────────────────────────────────────────

const TabbedReferencePanel: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabId>(() => {
    // Auto-select Pre-Market during pre-market hours, else first tab
    const now = new Date();
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const t = et.getHours() * 60 + et.getMinutes();
    if (t >= 570 && t < 960) return 'signals'; // market hours → signals
    return 'premarket';
  });

  const [intel, setIntel]         = useState<IntelData | null>(null);
  const [signals, setSignals]     = useState<Signal[]>([]);
  const [feedback, setFeedback]   = useState<FeedbackRow[]>([]);
  const [audit, setAudit]         = useState<AuditEntry[]>([]);
  const [sigLoading, setSigLoading]     = useState(true);
  const [actLoading, setActLoading]     = useState(true);

  // Dot-alert state per tab
  const [dots, setDots] = useState<Partial<Record<TabId, boolean>>>({});
  const prevSigCount = useRef(0);

  // ── Fetchers ───────────────────────────────────────────────────────────────

  const fetchIntel = useCallback(async () => {
    try {
      const r = await fetch('/api/intel');
      if (r.ok) {
        setIntel(await r.json() as IntelData);
      }
    } catch { /* silent */ }
  }, []);

  const fetchSignals = useCallback(async () => {
    try {
      const r = await fetch('/api/signals/all');
      if (r.ok) {
        const data = await r.json() as Signal[];
        const sigs = data.filter(s => s.strategy_code !== 'IDLE_SCAN');
        if (sigs.length > prevSigCount.current) {
          setDots(d => ({ ...d, signals: true }));
        }
        prevSigCount.current = sigs.length;
        setSignals(sigs);
        setSigLoading(false);
      }
    } catch { /* silent */ }
  }, []);

  const fetchActivity = useCallback(async () => {
    try {
      const [fbRes, auditRes] = await Promise.allSettled([
        fetch('/api/signals/feedback/recent?limit=50'),
        fetch('/api/system/audit-feed?limit=50'),
      ]);
      if (fbRes.status === 'fulfilled' && fbRes.value.ok) {
        const d = await fbRes.value.json();
        setFeedback(d.rows ?? []);
      }
      if (auditRes.status === 'fulfilled' && auditRes.value.ok) {
        setAudit(await auditRes.value.json());
      }
      setActLoading(false);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchIntel();   const it = setInterval(fetchIntel, 5 * 60_000);
    fetchSignals(); const st = setInterval(fetchSignals, 5_000);
    fetchActivity(); const at = setInterval(fetchActivity, 30_000);
    return () => { clearInterval(it); clearInterval(st); clearInterval(at); };
  }, [fetchIntel, fetchSignals, fetchActivity]);

  // Clear dot when tab is activated
  const handleTabClick = (id: TabId) => {
    setActiveTab(id);
    setDots(d => ({ ...d, [id]: false }));
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const renderContent = () => {
    switch (activeTab) {
      case 'premarket':   return <PreMarketTab intel={intel} />;
      case 'macro':       return <MacroTab intel={intel} />;
      case 'correlation': return <CorrelationTab intel={intel} />;
      case 'chop':        return <ChopTab intel={intel} />;
      case 'evcongress':  return <EvCongressTab intel={intel} />;
      case 'signals':     return <SignalsTab signals={signals} isLoading={sigLoading} />;
      case 'activity':    return <ActivityTab feedback={feedback} audit={audit} isLoading={actLoading} />;
    }
  };

  const activeTabTitle = TABS.find(t => t.id === activeTab)?.title ?? '';

  return (
    <div className="tabbed-ref-panel" data-testid="tabbed-ref-panel">
      {/* Tab bar */}
      <div className="trp-tab-bar" role="tablist" aria-label="Reference panel tabs" data-testid="tab-bar">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`trp-tab-btn ${activeTab === tab.id ? 'active' : ''}`}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`tabpanel-${tab.id}`}
            data-testid={`tab-${tab.id}`}
            title={tab.title}
            aria-label={tab.title}
            onClick={() => handleTabClick(tab.id)}
          >
            {tab.label}
            {dots[tab.id] && (
              <span className="trp-dot-indicator" aria-label="New data available" />
            )}
          </button>
        ))}
      </div>

      {/* Panel */}
      <div
        className="trp-panel-body"
        role="tabpanel"
        id={`tabpanel-${activeTab}`}
        aria-label={activeTabTitle}
        data-testid="tab-panel-body"
      >
        {renderContent()}
      </div>
    </div>
  );
};

export default memo(TabbedReferencePanel);
