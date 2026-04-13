import { useState, useEffect, useRef, useCallback } from 'react';
import './Dashboard.css';
import Tooltip from '../components/Tooltip';
import TradingViewWidget from '../components/TradingViewWidget';
import SystemMonitor from '../components/SystemMonitor';
import { SkeletonCard, SkeletonTable } from '../components/SkeletonLoader';
import { computeEconomics, formatRR, rrColorClass } from '../lib/signal_economics';

// ============================================================
//  Types
// ============================================================
interface Signal {
    timestamp: number;
    action: string;
    direction: string;
    is_spread: boolean;
    short_strike: number;
    long_strike: number;
    recommended_strike: number;
    option_type: string;
    target_limit_price: number;
    take_profit_price: number;
    stop_loss_price: number;
    quantity: number;
    kelly_wager_pct: number;
    confidence: number;
    strategy_code?: string;
    model_id?: string;
    expiration_date?: string;
    implied_volatility?: number;
    price_source?: string;
    underlying_price?: number;
    spot_sources?: { tv?: number; yf?: number; divergence_pct?: number };
    confidence_rationale?: string;
    ticker?: string;
    ibkr_order_id?: number;
    exec_status?: string;    // "submitted" | "failed" | "sim_filled" | "rejected"
    exec_error?: string;
}

interface BrokerStatus {
    mode: string;    // 'live' | 'paper' | 'simulation'
    connected: boolean;
}

// ============================================================
//  NAV Drill-Down Modal
// ============================================================
interface NavDrillProps {
    portfolio: Portfolio;
    account: AccountSummary | null;
    simMode: string;
    onClose: () => void;
}

const NavDrillModal = ({ portfolio, account, simMode, onClose }: NavDrillProps) => {
    const isPaper = simMode === 'paper';
    const nav = isPaper ? (account?.net_liquidation ?? portfolio.nav) : portfolio.nav;
    const cash = isPaper ? (account?.cash_balance ?? portfolio.cash) : portfolio.cash;
    const unrealized = isPaper ? (account?.unrealized_pnl ?? portfolio.unrealized_pnl) : portfolio.unrealized_pnl;
    const realized = isPaper ? (account?.realized_pnl ?? portfolio.realized_pnl) : portfolio.realized_pnl;
    const posValues = Object.values(portfolio.positions ?? {}).map(p => ({
        key: `${p.ticker}_${p.option_type}_${p.strike}`,
        ticker: p.ticker,
        marketValue: (p.current_price ?? 0) * (p.quantity ?? 0) * 100,
        unrealizedPnl: p.unrealized_pnl ?? 0,
    }));
    const sumPosMV = posValues.reduce((s, p) => s + p.marketValue, 0);
    const navSource = isPaper ? 'IBKR /api/account net_liquidation' : 'Sim portfolio /api/portfolio nav';
    const ts = account?.ts ?? new Date().toISOString();

    return (
        <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="NAV Drill-Down">
            <div className="modal-card nav-drill" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <span className="modal-title">📊 NAV DRILL-DOWN</span>
                    <button className="modal-close" onClick={onClose} aria-label="Close NAV drill-down">✕</button>
                </div>
                <div className="fill-drill-body">
                    <div className="fill-pnl-banner win">
                        <span className="fill-pnl-label">NET LIQUIDATION</span>
                        <span className="fill-pnl-value">{formatUSD(nav)}</span>
                    </div>
                    <div className="fill-section">
                        <div className="fill-section-title">Composition</div>
                        <div className="fill-grid">
                            <div className="fill-row">
                                <span>Cash</span>
                                <span data-tooltip="Cash balance from broker account">{formatUSD(cash)}</span>
                            </div>
                            <div className="fill-row">
                                <span>Open Position Market Values</span>
                                <span data-tooltip="Sum of current_price × qty × 100 for all positions">{formatUSD(sumPosMV)}</span>
                            </div>
                            <div className="fill-row">
                                <span>Unrealized P&L</span>
                                <span style={{ color: (unrealized ?? 0) >= 0 ? '#3fb950' : '#f85149' }}
                                      data-tooltip="Mark-to-market gain/loss on open positions">
                                    {(unrealized ?? 0) >= 0 ? '+' : ''}{formatUSD(unrealized ?? 0)}
                                </span>
                            </div>
                            <div className="fill-row">
                                <span>Realized P&L (session)</span>
                                <span style={{ color: (realized ?? 0) >= 0 ? '#3fb950' : '#f85149' }}
                                      data-tooltip="Locked-in P&L from closed trades this session">
                                    {(realized ?? 0) >= 0 ? '+' : ''}{formatUSD(realized ?? 0)}
                                </span>
                            </div>
                            <div className="fill-row" style={{ borderTop: '1px solid #30363d', fontWeight: 700, paddingTop: '6px' }}>
                                <span>= NAV</span>
                                <span style={{ color: '#79c0ff' }}>{formatUSD(nav)}</span>
                            </div>
                        </div>
                    </div>
                    {posValues.length > 0 && (
                        <div className="fill-section">
                            <div className="fill-section-title">Position Market Values</div>
                            <div className="fill-grid">
                                {posValues.map(p => (
                                    <div key={p.key} className="fill-row">
                                        <span style={{ fontFamily: 'monospace', fontSize: '11px' }}>{p.ticker}</span>
                                        <span data-tooltip="current_price × qty × 100">{formatUSD(p.marketValue)}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    <div className="fill-section">
                        <div className="fill-section-title">Data Provenance</div>
                        <div className="fill-grid">
                            <div className="fill-row"><span>Source</span><span style={{ fontSize: '11px' }}>{navSource}</span></div>
                            <div className="fill-row"><span>Mode</span><span>{simMode.toUpperCase()}</span></div>
                            <div className="fill-row"><span>Last Updated</span><span>{ts ? new Date(ts).toLocaleTimeString() : '—'}</span></div>
                            <div className="fill-row"><span>Computation</span><span style={{ fontSize: '10px' }}>cash + Σ(position_market_values) + realized_pnl</span></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

interface SpotValidation {
    tv: number | null;
    yf: number | null;
    divergence_pct: number;
    ok: boolean;
    warning: string | null;
    timestamp: string;
}

interface DataAudit {
    spot_validation: SpotValidation;
    options_chain_source: string;
    last_chain_fetch: string;
    chain_age_sec: number;
    tv_feed_ok: boolean;
    yf_feed_ok: boolean;
    ibkr_connected: boolean;
    ibkr_spot: number;
    primary_source: string;
}

interface AccountSummary {
    net_liquidation: number;
    cash_balance: number;
    buying_power: number;
    unrealized_pnl: number;
    realized_pnl: number;
    equity_with_loan: number;
    ts: string;
    error?: string;
}

interface IBKRPosition {
    ticker: string;
    sec_type: string;
    qty: number;
    avg_cost: number;
    current_price: number;
    unrealized_pnl: number;
    market_value: number;
    option_type: string;
    strike: number;
    expiration: string;
    delta: number;
    iv: number;
    signal_id: string;
    catalyst: string;
    model_id: string;
}

interface Position {
    ticker: string;
    option_type: string;
    strike: number;
    expiry: string;
    entry_price: number;
    current_price: number;
    quantity: number;
    unrealized_pnl: number;
    entry_time: string;
}

interface Trade {
    time: string;
    action: string;
    ticker: string;
    quantity: number;
    price: number;
    cost: number;
    pnl: number;
    net_profit: number;
    // Phase 4 additions:
    id?: string;
    signal_id?: string;
    option_type?: string;
    strike?: number;
    expiration_date?: string;
    entry_price?: number;
    exit_price?: number;
    pnl_pct?: number;
    win?: boolean;
    catalyst?: string;
    model_id?: string;
    confidence_at_entry?: number;
    exit_reason?: string;
    source?: string;
}

interface ConfidenceCalibration {
    high_conf_trade_count: number;
    high_conf_win_rate: number | null;
}

interface LossTag { tag: string; count: number; }

interface ModelScorecard {
    model_id: string;
    trade_count: number;
    win_count: number;
    loss_count: number;
    win_rate: number;
    total_pnl: number;
    avg_pnl: number;
    best_trade: number;
    worst_trade: number;
    avg_confidence: number;
    sharpe: number;
    confidence_calibration: ConfidenceCalibration;
    common_loss_tags: LossTag[];
}

interface LossSummary {
    total_losses: number;
    total_loss_amount: number;
    avg_loss: number;
    loss_tags: Record<string, number>;
}

interface IntelNews {
    headlines: string[];
    sentiment_score: number;
    headline_count: number;
    bull_hits: number;
    bear_hits: number;
    error?: string;
}
interface IntelVix {
    vix_level: number | null;
    vix_status: string;
    error?: string;
}
interface IntelSpy {
    spy_price: number | null;
    spy_change_pct: number;
    error?: string;
}
interface IntelEarnings {
    next_earnings_date: string | null;
    days_until_earnings: number | null;
    error?: string;
}
interface IntelOptionsFlow {
    pc_ratio: number;
    pc_signal: string;
    total_call_oi: number;
    total_put_oi: number;
    error?: string;
}
interface IntelPremarket {
    is_premarket: boolean;
    is_signal_window: boolean;
    futures_bias: string;
    es_change_pct: number;
    nq_change_pct: number;
    europe_direction: string;
    tsla_premarket_change_pct: number;
    tsla_premarket_volume: number;
    overnight_catalyst: string | null;
}

interface Intel {
    fetch_timestamp: number;
    news: IntelNews;
    vix: IntelVix;
    spy: IntelSpy;
    earnings: IntelEarnings;
    options_flow: IntelOptionsFlow;
    premarket?: IntelPremarket;
}

interface LosingTrade {
    id: string;
    ticker: string;
    option_type: string;
    strike: number;
    expiry: string;
    entry_ts: string;
    exit_ts: string;
    entry_price: number;
    exit_price: number;
    qty: number;
    pnl: number;
    model_id: string;
    confidence_at_entry: number;
    catalyst: string;
    exit_reason: string;
    loss_tag: string;
    loss_notes: string;
    iv?: number;
    bid?: number;
    ask?: number;
    oi?: number;
    delta?: number;
}

interface Portfolio {
    positions: Record<string, Position>;
    nav: number;
    cash: number;
    realized_pnl: number;
    unrealized_pnl: number;
}

interface SystemState {
    kill_switch: boolean;
    signals_blocked_reason: string; // 'kill_switch' | 'market_closed' | 'no_signals' | ''
    daily_pnl: number;
    max_daily_loss: number;
    mode: string;
    conviction_count: number;
}

// ============================================================
//  Helpers
// ============================================================
function relativeTime(ts: number | string): string {
    const ms = typeof ts === 'number' ? ts * 1000 : new Date(ts).getTime();
    const diff = Math.floor((Date.now() - ms) / 1000);
    if (diff < 5)   return 'just now';
    if (diff < 60)  return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
}

function formatUSD(n: number | undefined): string {
    if (n === undefined || n === null || !isFinite(n)) return '$—';
    return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

function getMarketStatus(): { label: string; cls: string } {
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
    if (t >= 570 && t < 960)  return { label: 'MARKET OPEN',   cls: 'open'   }; // 9:30–16:00
    if (t >= 240 && t < 570)  return { label: 'PRE-MARKET',    cls: 'pre'    }; // 4:00–9:30
    if (t >= 960 && t < 1200) return { label: 'AFTER HOURS',   cls: 'after'  }; // 16:00–20:00
    return { label: 'MARKET CLOSED', cls: 'closed' };
}

function spreadType(s: Signal): { name: string; creditDebit: string; outlook: string } {
    if (!s.is_spread) return { name: '', creditDebit: '', outlook: '' };
    if (s.option_type === 'PUT') {
        if (s.short_strike > s.long_strike) {
            return { name: 'Bull Put Spread', creditDebit: 'Credit', outlook: 'Bullish — profits if TSLA stays above' };
        } else {
            return { name: 'Bear Put Spread', creditDebit: 'Debit', outlook: 'Bearish — profits if TSLA falls below' };
        }
    } else {
        if (s.short_strike < s.long_strike) {
            return { name: 'Bear Call Spread', creditDebit: 'Credit', outlook: 'Bearish — profits if TSLA stays below' };
        } else {
            return { name: 'Bull Call Spread', creditDebit: 'Debit', outlook: 'Bullish — profits if TSLA rises above' };
        }
    }
}

function contractName(s: Signal): string {
    if (s.is_spread) {
        const sp = spreadType(s);
        const expLabel = s.expiration_date
            ? ' exp ' + new Date(s.expiration_date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
            : '';
        return `${sp.name} $${s.short_strike}/$${s.long_strike}${expLabel}`;
    }
    const strike = s.recommended_strike?.toFixed(0) ?? '?';
    const base = `$${strike} ${s.option_type}`;
    if (s.expiration_date) {
        const exp = new Date(s.expiration_date + 'T00:00:00');
        const label = exp.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        return `${base} exp ${label}`;
    }
    return base;
}

function contractExplanation(s: Signal): string {
    const strike = s.recommended_strike?.toFixed(0) ?? '?';
    const limitPrice = s.target_limit_price ?? 0;
    const limitStr = limitPrice > 0 ? `$${(limitPrice * 100).toFixed(0)}` : '';
    if (s.is_spread) {
        const sp = spreadType(s);
        const width = Math.abs(s.short_strike - s.long_strike);
        const maxRisk = (width * 100).toFixed(0);
        const sellLeg = `Sell $${s.short_strike} ${s.option_type}`;
        const buyLeg = `Buy $${s.long_strike} ${s.option_type}`;
        if (sp.creditDebit === 'Credit') {
            const maxProfit = limitPrice > 0 ? ` Max profit ${limitStr}/contract (net credit received).` : '';
            return `${sp.name} (${sp.creditDebit}): ${sellLeg}, ${buyLeg}. Max risk $${maxRisk}/contract.${maxProfit} ${sp.outlook} $${s.short_strike} at expiry.`;
        } else {
            return `${sp.name} (${sp.creditDebit}): ${buyLeg}, ${sellLeg}. Max risk = debit paid. Max profit $${maxRisk}/contract minus debit. ${sp.outlook} $${s.long_strike} at expiry.`;
        }
    }
    if (s.action === 'BUY' && s.option_type === 'CALL') {
        return `Long Call at $${strike}: Profits if TSLA rises above $${strike} + premium. Max profit: unlimited. Max loss = premium paid${limitPrice > 0 ? ` (${limitStr}/contract)` : ''}.`;
    }
    if (s.action === 'BUY' && s.option_type === 'PUT') {
        const maxProfit = `$${(Number(strike) * 100).toLocaleString()} minus premium`;
        return `Long Put at $${strike}: Profits if TSLA falls below $${strike} - premium. Max profit: ${maxProfit} (if TSLA goes to $0). Max loss = premium paid${limitPrice > 0 ? ` (${limitStr}/contract)` : ''}.`;
    }
    if (s.action === 'SELL' && s.option_type === 'PUT') {
        return `Short Put at $${strike}: Max profit = premium collected${limitPrice > 0 ? ` (${limitStr}/contract)` : ''}. Max risk: $${(Number(strike) * 100).toLocaleString()} if TSLA goes to $0. Bullish outlook.`;
    }
    if (s.action === 'SELL' && s.option_type === 'CALL') {
        return `Short Call at $${strike}: Max profit = premium collected${limitPrice > 0 ? ` (${limitStr}/contract)` : ''}. Max risk: unlimited if TSLA rises. Bearish outlook.`;
    }
    return `${s.action} ${s.option_type} at $${strike}`;
}

// ============================================================
//  Signal Detail Modal (with chain drill-down)
// ============================================================
const SignalModal = ({ signal, onClose }: { signal: Signal; onClose: () => void }) => {
    const isBull = signal.direction === 'BULLISH';
    const [auditData, setAuditData] = useState<DataAudit | null>(null);
    const [auditLoading, setAuditLoading] = useState(false);
    const [chainData, setChainData] = useState<any>(null);
    const [chainLoading, setChainLoading] = useState(false);
    const [chainTab, setChainTab] = useState<'calls' | 'puts'>(signal.option_type === 'PUT' ? 'puts' : 'calls');
    const chainScrollRef = useRef<HTMLDivElement>(null);
    const highlightRef = useRef<HTMLTableRowElement>(null);

    const fetchChain = async () => {
        setChainLoading(true);
        try {
            const expiry = signal.expiration_date || '';
            const r = await fetch(`/api/options/chain?expiry=${expiry}`);
            if (r.ok) setChainData(await r.json());
        } catch { /* ignore */ }
        setChainLoading(false);
    };

    // Auto-fetch chain on open
    useEffect(() => {
        fetchChain();
    }, []);

    // Scroll to highlighted strike row when chain data loads or tab changes
    useEffect(() => {
        if (chainData) {
            // Wait for React to render the highlighted row before scrolling
            const timer = setTimeout(() => {
                if (highlightRef.current) {
                    highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }, 100);
            return () => clearTimeout(timer);
        }
    }, [chainData, chainTab]);

    const fetchAudit = async (refresh = false) => {
        setAuditLoading(true);
        try {
            const r = await fetch(refresh ? '/api/data/audit?refresh=true' : '/api/data/audit');
            if (r.ok) setAuditData(await r.json());
        } catch { /* ignore */ }
        setAuditLoading(false);
    };

    // Derive strike snap description from signal fields
    const targetSpot = signal.underlying_price ?? 0;
    const moneyness = signal.option_type === 'CALL' ? 1.05 : 0.95;
    const targetStrike = targetSpot > 0 ? (targetSpot * moneyness).toFixed(2) : '—';
    const snappedStrike = signal.is_spread
        ? `$${signal.short_strike} / $${signal.long_strike}`
        : `$${signal.recommended_strike?.toFixed(0) ?? '—'}`;

    // Spot sources from the signal payload
    const ss = signal.spot_sources;
    const hasSS = ss && (ss.tv != null || ss.yf != null);

    // Only highlight on the correct tab for the signal's option type
    // For spreads, highlight the short strike (sold leg); for singles, use recommended_strike
    const signalStrike = signal.is_spread ? signal.short_strike : (signal.recommended_strike ?? 0);
    const signalTabMatch = (signal.option_type === 'PUT' && chainTab === 'puts') ||
                           (signal.option_type === 'CALL' && chainTab === 'calls');

    const getHighlightIdx = (): number | null => {
        if (!chainData || !signalTabMatch || signalStrike <= 0) return null;
        const rows = chainTab === 'calls' ? chainData.calls : chainData.puts;
        if (!rows || rows.length === 0) return null;
        let bestIdx = 0;
        let bestDist = Infinity;
        for (let i = 0; i < rows.length; i++) {
            const dist = Math.abs(rows[i].strike - signalStrike);
            if (dist < bestDist) {
                bestDist = dist;
                bestIdx = i;
            }
        }
        return bestIdx;
    };
    const highlightIdx = getHighlightIdx();

    // For spreads, also find nearest index for the long (protection) strike
    const getLongHighlightIdx = (): number | null => {
        if (!chainData || !signalTabMatch || !signal.is_spread || signal.long_strike <= 0) return null;
        const rows = chainTab === 'calls' ? chainData.calls : chainData.puts;
        if (!rows || rows.length === 0) return null;
        let bestIdx = 0;
        let bestDist = Infinity;
        for (let i = 0; i < rows.length; i++) {
            const dist = Math.abs(rows[i].strike - signal.long_strike);
            if (dist < bestDist) {
                bestDist = dist;
                bestIdx = i;
            }
        }
        // Don't highlight if it's the same row as the short strike
        if (bestIdx === highlightIdx) return null;
        return bestIdx;
    };
    const longHighlightIdx = getLongHighlightIdx();

    return (
        <div className="signal-modal-overlay" onClick={onClose}>
            <div className="signal-modal" onClick={e => e.stopPropagation()}>
                <div className="signal-modal-header">
                    <Tooltip text={contractExplanation(signal)}>
                        <span className="signal-modal-title" data-tooltip={contractExplanation(signal)}>
                            {signal.is_spread ? contractName(signal) : `${signal.action} ${contractName(signal)}`}
                        </span>
                    </Tooltip>
                    <button className="btn-modal-close" onClick={onClose} aria-label="Close signal detail modal">×</button>
                </div>

                {/* ── Core fields ─────────────────────────────────── */}
                <div className="modal-grid">
                    <div className="modal-row full-width">
                        <span className="modal-key">Direction</span>
                        <span className={`modal-val ${isBull ? 'green' : 'red'}`}>{signal.direction}</span>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Confidence</span>
                        <span className="modal-val blue">{(signal.confidence * 100).toFixed(1)}%</span>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Kelly %</span>
                        <span className="modal-val">{(signal.kelly_wager_pct * 100).toFixed(1)}%</span>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Quantity</span>
                        <span className="modal-val">{signal.quantity}</span>
                    </div>
                    {!signal.is_spread && (
                        <div className="modal-row">
                            <span className="modal-key">Contract</span>
                            <span className="modal-val">{signal.action === 'BUY' ? 'Long' : 'Short'} {signal.option_type}</span>
                        </div>
                    )}
                    <div className="modal-row">
                        <span className="modal-key">Target Limit</span>
                        <span className="modal-val">{formatUSD(signal.target_limit_price)}</span>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Take Profit</span>
                        <span className="modal-val green">{formatUSD(signal.take_profit_price)}</span>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Stop Loss</span>
                        <span className="modal-val red">{formatUSD(signal.stop_loss_price)}</span>
                    </div>
                    {signal.implied_volatility != null && signal.implied_volatility > 0 && (
                        <div className="modal-row">
                            <span className="modal-key">Implied Vol</span>
                            <span className="modal-val">{(signal.implied_volatility * 100).toFixed(1)}%</span>
                        </div>
                    )}
                    {signal.is_spread && (
                        <>
                            <div className="modal-row full-width">
                                <span className="modal-key">Strategy</span>
                                <span className="modal-val">{spreadType(signal).name} ({spreadType(signal).creditDebit})</span>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Sell Leg</span>
                                <span className="modal-val red">${signal.short_strike} {signal.option_type}</span>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Buy Leg</span>
                                <span className="modal-val green">${signal.long_strike} {signal.option_type}</span>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Width</span>
                                <span className="modal-val">${Math.abs(signal.short_strike - signal.long_strike)}</span>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Max Risk</span>
                                <span className="modal-val red">${(Math.abs(signal.short_strike - signal.long_strike) * 100).toFixed(0)}/contract</span>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Max Profit</span>
                                <span className="modal-val green">{signal.target_limit_price > 0 ? `$${(signal.target_limit_price * 100).toFixed(0)}/contract` : 'Net credit'}</span>
                            </div>
                        </>
                    )}
                    {!signal.is_spread && (
                        <div className="modal-row">
                            <span className="modal-key">Strike</span>
                            <span className="modal-val">${signal.recommended_strike?.toFixed(0)}</span>
                        </div>
                    )}
                    {signal.strategy_code && (
                        <div className="modal-row">
                            <span className="modal-key">Strategy</span>
                            <span className="modal-val">{signal.strategy_code}</span>
                        </div>
                    )}
                    {signal.model_id && (
                        <div className="modal-row">
                            <span className="modal-key">Model</span>
                            <span className="modal-val">{signal.model_id}</span>
                        </div>
                    )}
                    {signal.expiration_date && (
                        <div className="modal-row full-width">
                            <span className="modal-key">Expiration</span>
                            <span className="modal-val">
                                {new Date(signal.expiration_date + 'T00:00:00').toLocaleDateString('en-US', {
                                    weekday: 'short', year: 'numeric', month: 'short', day: 'numeric'
                                })}
                            </span>
                        </div>
                    )}
                    <div className="modal-row full-width">
                        <span className="modal-key">Timestamp</span>
                        <span className="modal-val">{new Date(signal.timestamp * 1000).toLocaleString()}</span>
                    </div>
                    {signal.confidence_rationale && (
                        <div className="modal-row full-width">
                            <span className="modal-key">Rationale</span>
                            <span className="modal-val" style={{ fontSize: '12px', lineHeight: '1.4', color: '#c9d1d9' }}>
                                {signal.confidence_rationale}
                            </span>
                        </div>
                    )}
                    {signal.exec_status && (
                        <div className="modal-row full-width">
                            <span className="modal-key">Order Status</span>
                            <span className="modal-val" style={{
                                fontFamily: 'monospace',
                                fontWeight: 700,
                                color: signal.exec_status === 'failed' || signal.exec_status === 'rejected'
                                    ? '#f85149'
                                    : signal.exec_status === 'submitted'
                                    ? '#3fb950'
                                    : '#79c0ff',
                            }}>
                                {signal.exec_status.toUpperCase()}
                                {signal.exec_error && (
                                    <span style={{ fontWeight: 400, marginLeft: '8px', color: '#f85149', fontSize: '11px' }}>
                                        — {signal.exec_error}
                                    </span>
                                )}
                            </span>
                        </div>
                    )}
                    {signal.ibkr_order_id != null && signal.ibkr_order_id > 0 && (
                        <div className="modal-row">
                            <span className="modal-key">IBKR Order ID</span>
                            <span className="modal-val" style={{ fontFamily: 'monospace', color: '#3fb950', fontWeight: 700 }}>
                                #{signal.ibkr_order_id}
                            </span>
                        </div>
                    )}
                    {signal.underlying_price != null && signal.underlying_price > 0 && (
                        <div className="modal-row">
                            <span className="modal-key">Spot Price</span>
                            <span className="modal-val">${signal.underlying_price.toFixed(2)}</span>
                        </div>
                    )}
                    {signal.ticker && (
                        <div className="modal-row">
                            <span className="modal-key">Ticker</span>
                            <span className="modal-val">{signal.ticker}</span>
                        </div>
                    )}
                </div>

                {/* Strategy explanation */}
                <div style={{ padding: '0.5rem 0.6rem', fontSize: '12px', color: '#8b949e', borderTop: '1px solid #21262d', lineHeight: '1.5' }}>
                    {contractExplanation(signal)}
                </div>

                {/* ── Trade Economics section ──────────────────────── */}
                {(() => {
                    const eco = computeEconomics(signal);
                    const ts = new Date(signal.timestamp * 1000).toLocaleString();
                    if (eco.is_na) {
                        return (
                            <div className="trade-economics-section">
                                <div className="trade-economics-title">💰 TRADE ECONOMICS</div>
                                <div className="econ-na-notice"
                                     data-tooltip={eco.na_reason}>
                                    Max profit: N/A ({eco.na_reason})
                                </div>
                            </div>
                        );
                    }
                    const fmtUSD = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
                    const fmtPct = (n: number) => `${(n * 100).toFixed(1)}%`;
                    const rrCls = rrColorClass(eco.risk_reward_ratio);
                    return (
                        <div className="trade-economics-section">
                            <div className="trade-economics-title">💰 TRADE ECONOMICS</div>

                            {/* Entry block */}
                            <div className="econ-block">
                                <div className="econ-block-label">Entry</div>
                                <div className="econ-ledger-row">
                                    <span
                                        data-tooltip={`Premium: ${fmtUSD(signal.target_limit_price)} × ${signal.quantity} contracts × 100 multiplier`}>
                                        Premium
                                    </span>
                                    <span data-tooltip={`${fmtUSD(signal.target_limit_price)} × ${signal.quantity} × 100`}>
                                        {fmtUSD(signal.target_limit_price * 100 * signal.quantity)}
                                    </span>
                                </div>
                                <div className="econ-ledger-row">
                                    <span
                                        data-tooltip={`IBKR Pro: max($${(0.65).toFixed(2)} × ${signal.quantity} contracts, $1.00 min) per leg`}>
                                        Open commission
                                    </span>
                                    <span data-tooltip={`max($0.65 × ${signal.quantity}, $1.00) = ${fmtUSD(eco.commission_open)}`}>
                                        {fmtUSD(eco.commission_open)}
                                    </span>
                                </div>
                                {eco.cost_basis > 0 && (
                                    <div className="econ-ledger-row bold">
                                        <span data-tooltip="Premium + open commission = total cash at risk">Cost basis</span>
                                        <span>{fmtUSD(eco.cost_basis)}</span>
                                    </div>
                                )}
                            </div>

                            {/* At Take Profit */}
                            <div className="econ-block">
                                <div className="econ-block-label">At Take Profit ({fmtUSD(signal.take_profit_price)})</div>
                                <div className="econ-ledger-row green">
                                    <span
                                        data-tooltip={`(${fmtUSD(signal.take_profit_price)} - ${fmtUSD(signal.target_limit_price)}) × ${signal.quantity} × 100`}>
                                        Gross profit
                                    </span>
                                    <span>{fmtUSD(eco.gross_profit_at_tp)}</span>
                                </div>
                                <div className="econ-ledger-row">
                                    <span
                                        data-tooltip={`Round-trip commission: open (${fmtUSD(eco.commission_open)}) + close (${fmtUSD(eco.commission_close)})`}>
                                        Round-trip commission
                                    </span>
                                    <span>{fmtUSD(eco.round_trip_commission)}</span>
                                </div>
                                <div className="econ-ledger-row green bold">
                                    <span data-tooltip="Gross profit minus round-trip commissions">Net profit</span>
                                    <span data-testid="econ-max-profit">{fmtUSD(eco.max_profit_at_tp)}</span>
                                </div>
                                {eco.cost_basis > 0 && (
                                    <div className="econ-ledger-row">
                                        <span data-tooltip="Net profit ÷ cost basis">Return on cost basis</span>
                                        <span>{fmtPct(eco.return_on_cost_basis_tp)}</span>
                                    </div>
                                )}
                            </div>

                            {/* At Stop Loss */}
                            <div className="econ-block">
                                <div className="econ-block-label">At Stop Loss ({fmtUSD(signal.stop_loss_price)})</div>
                                <div className="econ-ledger-row red">
                                    <span
                                        data-tooltip={`(${fmtUSD(signal.target_limit_price)} - ${fmtUSD(signal.stop_loss_price)}) × ${signal.quantity} × 100`}>
                                        Gross loss
                                    </span>
                                    <span>−{fmtUSD(eco.gross_loss_at_sl)}</span>
                                </div>
                                <div className="econ-ledger-row">
                                    <span data-tooltip="Commission paid on both legs">Round-trip commission</span>
                                    <span>{fmtUSD(eco.round_trip_commission)}</span>
                                </div>
                                <div className="econ-ledger-row red bold">
                                    <span data-tooltip="Gross loss plus round-trip commissions">Net loss</span>
                                    <span data-testid="econ-max-loss">−{fmtUSD(eco.max_loss_at_sl)}</span>
                                </div>
                                {eco.cost_basis > 0 && (
                                    <div className="econ-ledger-row">
                                        <span data-tooltip="Net loss ÷ cost basis">Return on cost basis</span>
                                        <span style={{ color: '#f85149' }}>{fmtPct(eco.return_on_cost_basis_sl)}</span>
                                    </div>
                                )}
                            </div>

                            {/* Summary */}
                            <div className="econ-block">
                                <div className="econ-block-label">Summary</div>
                                <div className="econ-ledger-row">
                                    <span
                                        data-tooltip={`entry + round_trip_commission / qty / 100 = ${fmtUSD(signal.target_limit_price)} + ${fmtUSD(eco.round_trip_commission)} / ${signal.quantity} / 100`}>
                                        Breakeven
                                    </span>
                                    <span data-testid="econ-breakeven" className="neutral" style={{ color: '#79c0ff' }}>
                                        {fmtUSD(eco.breakeven)}
                                    </span>
                                </div>
                                <div className="econ-ledger-row">
                                    <span
                                        data-tooltip={`net_profit / net_loss = ${fmtUSD(eco.max_profit_at_tp)} / ${fmtUSD(eco.max_loss_at_sl)} — per $1 risked, ${fmtUSD(eco.risk_reward_ratio)} expected at TP`}>
                                        Risk : Reward
                                    </span>
                                    <span data-testid="econ-rr"
                                          style={{ color: rrCls === 'green' ? '#3fb950' : rrCls === 'amber' ? '#d29922' : '#f85149' }}>
                                        {formatRR(eco.risk_reward_ratio)}
                                    </span>
                                </div>
                                {eco.theoretical_max_profit === 'unlimited' ? (
                                    <div className="econ-ledger-row">
                                        <span data-tooltip="Long call: profit unlimited as underlying rises">Theoretical max</span>
                                        <span style={{ color: '#3fb950' }}>∞ unlimited</span>
                                    </div>
                                ) : eco.theoretical_max_profit > 0 && (
                                    <div className="econ-ledger-row">
                                        <span data-tooltip="Long put: bounded by underlying going to zero">Theoretical max</span>
                                        <span style={{ color: '#3fb950' }}>{fmtUSD(eco.theoretical_max_profit as number)}</span>
                                    </div>
                                )}
                            </div>

                            <div className="econ-source-note">
                                <span data-tooltip="IBKR Pro tiered: $0.65/contract, $1.00 minimum per leg, 2 legs for long options">
                                    Source: IBKR Pro · $0.65/contract · $1 min/leg · {signal.is_spread ? '4' : '2'} legs round-trip
                                </span>
                                <br />
                                <span style={{ color: '#30363d' }}>Signal timestamp: {ts}</span>
                            </div>
                        </div>
                    );
                })()}

                {/* ── Data Provenance section ──────────────────────── */}
                <div className="modal-provenance">
                    <div className="modal-provenance-title">🔗 DATA PROVENANCE</div>

                    <div className="prov-row">
                        <span className="prov-key">Strike snap</span>
                        <span className="prov-val">
                            {targetSpot > 0
                                ? `nearest_liquid(target=${targetStrike}) → ${snappedStrike}`
                                : snappedStrike}
                        </span>
                    </div>
                    <div className="prov-row">
                        <span className="prov-key">Chain source</span>
                        <span className="prov-val">yfinance · OI≥100 filter · 60s TTL cache</span>
                    </div>
                    {signal.implied_volatility != null && signal.implied_volatility > 0 && (
                        <div className="prov-row">
                            <span className="prov-key">IV source</span>
                            <span className="prov-val">
                                {`chain[strike=${signal.recommended_strike?.toFixed(0)}, ${signal.option_type}]`}
                                <span className="prov-badge">
                                    {(signal.implied_volatility * 100).toFixed(1)}%
                                </span>
                            </span>
                        </div>
                    )}
                    <div className="prov-row">
                        <span className="prov-key">Expiry calc</span>
                        <span className="prov-val">
                            nearest_expiry_with_liquidity(min_dte=1)
                            {signal.expiration_date && (
                                <span className="prov-badge">{signal.expiration_date}</span>
                            )}
                        </span>
                    </div>
                    {signal.price_source && (
                        <div className="prov-row">
                            <span className="prov-key">Price source</span>
                            <span className="prov-val">{signal.price_source}</span>
                        </div>
                    )}
                    {hasSS && (
                        <div className="prov-row">
                            <span className="prov-key">Spot at signal</span>
                            <span className="prov-val">
                                {ss!.tv != null && <span className="prov-src-badge tv">TV ${ss!.tv.toFixed(2)}</span>}
                                {ss!.yf != null && <span className="prov-src-badge yf">YF ${ss!.yf.toFixed(2)}</span>}
                                {ss!.divergence_pct != null && (
                                    <span className={`prov-src-badge ${ss!.divergence_pct >= 2 ? 'warn' : 'ok'}`}>
                                        Δ{ss!.divergence_pct.toFixed(3)}%
                                    </span>
                                )}
                            </span>
                        </div>
                    )}

                    {/* Options Chain + Spot Audit buttons */}
                    <div className="prov-chain-row" style={{ display: 'flex', gap: '8px' }}>
                        <button className="btn-view-chain" onClick={() => fetchChain()} disabled={chainLoading} aria-busy={chainLoading}>
                            ⟳ Options Chain
                        </button>
                        <button className="btn-view-chain" onClick={() => fetchAudit(true)} disabled={auditLoading} aria-busy={auditLoading}>
                            ⟳ Spot Audit
                        </button>
                    </div>

                    {chainData && !chainData.error && (
                        <div className="prov-audit-result">
                            <div className="prov-audit-header">
                                Options Chain: {chainData.expiry}
                                <div style={{ marginTop: '4px', display: 'flex', gap: '8px' }}>
                                    <button
                                        className={`btn-view-chain${chainTab === 'calls' ? ' active' : ''}`}
                                        onClick={() => setChainTab('calls')}
                                        style={{ padding: '2px 10px', fontSize: '11px' }}
                                    >CALLS ({chainData.calls?.length ?? 0})</button>
                                    <button
                                        className={`btn-view-chain${chainTab === 'puts' ? ' active' : ''}`}
                                        onClick={() => setChainTab('puts')}
                                        style={{ padding: '2px 10px', fontSize: '11px' }}
                                    >PUTS ({chainData.puts?.length ?? 0})</button>
                                </div>
                            </div>
                            <div ref={chainScrollRef} style={{ overflowX: 'auto', maxHeight: '250px', overflowY: 'auto' }}>
                                <table style={{ width: '100%', fontSize: '11px', borderCollapse: 'collapse', color: '#c9d1d9' }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid #30363d', color: '#8b949e' }}>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Strike</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Bid</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Ask</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Mid</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Last</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>IV%</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>OI</th>
                                            <th style={{ padding: '4px 6px', textAlign: 'right' }}>Sprd%</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(chainTab === 'calls' ? chainData.calls : chainData.puts)?.map((row: any, i: number) => {
                                            const isATM = Math.abs(row.strike - (signal.underlying_price ?? 0)) < 1;
                                            const isSignalStrike = i === highlightIdx;
                                            const isLongStrike = i === longHighlightIdx;
                                            return (
                                                <tr key={i} ref={isSignalStrike ? highlightRef : undefined} style={{
                                                    borderBottom: '1px solid #21262d',
                                                    backgroundColor: isSignalStrike ? 'rgba(56,139,253,0.25)' : isLongStrike ? 'rgba(56,139,253,0.12)' : isATM ? 'rgba(139,148,158,0.05)' : 'transparent',
                                                    borderLeft: isSignalStrike ? '3px solid #388bfd' : isLongStrike ? '2px solid rgba(56,139,253,0.5)' : 'none',
                                                }}>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right', fontWeight: isSignalStrike ? 700 : 400 }}>
                                                        ${row.strike}{isSignalStrike ? ' ◄ SELL' : ''}{isLongStrike ? ' ◄ BUY' : ''}
                                                    </td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right' }}>${row.bid.toFixed(2)}</td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right' }}>${row.ask.toFixed(2)}</td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right', color: '#58a6ff' }}>${row.mid.toFixed(2)}</td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right' }}>${row.last.toFixed(2)}</td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right', color: row.iv > 60 ? '#f0883e' : '#8b949e' }}>{row.iv.toFixed(1)}%</td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right' }}>{row.oi.toLocaleString()}</td>
                                                    <td style={{ padding: '3px 6px', textAlign: 'right', color: row.spread_pct > 15 ? '#da3633' : '#8b949e' }}>{row.spread_pct.toFixed(1)}%</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {auditData && (
                        <div className="prov-audit-result">
                            <div className="prov-audit-header">Live spot validation (just refreshed)</div>
                            <div className="prov-audit-grid">
                                <span className="prov-key">TradingView</span>
                                <span className="prov-val">
                                    {auditData.spot_validation.tv != null
                                        ? `$${auditData.spot_validation.tv.toFixed(2)}`
                                        : '—'}
                                    {auditData.tv_feed_ok
                                        ? <span className="prov-src-badge ok">OK</span>
                                        : <span className="prov-src-badge err">ERR</span>}
                                </span>
                                <span className="prov-key">yfinance</span>
                                <span className="prov-val">
                                    {auditData.spot_validation.yf != null
                                        ? `$${auditData.spot_validation.yf.toFixed(2)}`
                                        : '—'}
                                    {auditData.yf_feed_ok
                                        ? <span className="prov-src-badge ok">OK</span>
                                        : <span className="prov-src-badge err">ERR</span>}
                                </span>
                                <span className="prov-key">Divergence</span>
                                <span className={`prov-val ${auditData.spot_validation.divergence_pct >= 2 ? 'warn' : 'green'}`}>
                                    {auditData.spot_validation.divergence_pct.toFixed(3)}%
                                    {auditData.spot_validation.ok
                                        ? <span className="prov-src-badge ok">IN SYNC</span>
                                        : <span className="prov-src-badge err">OUT OF SYNC</span>}
                                </span>
                                <span className="prov-key">Chain source</span>
                                <span className="prov-val">{auditData.options_chain_source}</span>
                                <span className="prov-key">Chain age</span>
                                <span className={`prov-val ${auditData.chain_age_sec > 300 ? 'warn' : ''}`}>
                                    {auditData.chain_age_sec.toFixed(0)}s
                                    {auditData.chain_age_sec > 300 && <span className="stale-badge">STALE</span>}
                                </span>
                            </div>
                            {auditData.spot_validation.warning && (
                                <div className={`prov-audit-warn ${auditData.spot_validation.divergence_pct >= 5 ? 'error' : 'warn'}`}>
                                    {auditData.spot_validation.warning}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

// ============================================================
//  Broker Status Pill
// ============================================================
const BrokerStatusPill = ({ brokerStatus }: { brokerStatus: BrokerStatus | null }) => {
    if (!brokerStatus) return null;
    const mode = brokerStatus.mode ?? 'simulation';
    const isLive = mode === 'live';
    const isPaper = mode === 'paper';
    const label = isLive ? '⚠ LIVE TRADING' : isPaper ? 'PAPER TRADING' : 'SIM MODE';
    const cls = isLive ? 'broker-live' : isPaper ? 'broker-paper' : 'broker-sim';
    return (
        <div
            className={`broker-status-banner ${cls}`}
            role="alert"
            aria-label={`Trading mode: ${label}`}
            data-tooltip={isLive ? 'LIVE mode — real orders are being executed against your live IBKR account.' : isPaper ? 'Paper mode — orders execute against IBKR paper account (no real money).' : 'Simulation mode — no real orders, local model only.'}
        >
            <span className="broker-pill">{label}</span>
            {isLive && (
                <span className="broker-banner-note">
                    Real orders active — all signals execute against live account
                </span>
            )}
        </div>
    );
};

// ============================================================
//  Data Provenance Panel (TV vs YF spot comparison)
// ============================================================
const DataProvenancePanel = ({ audit }: { audit: DataAudit | null }) => {
    if (!audit) return null;
    const sv = audit.spot_validation;
    const chainAgeSec = audit.chain_age_sec;
    const chainStale = chainAgeSec > 300;

    // Compute age of spot validation timestamp
    const auditAgeMs = sv.timestamp ? Date.now() - new Date(sv.timestamp).getTime() : 0;
    const auditAgeSec = auditAgeMs / 1000;
    const spotStale = auditAgeSec > 300;

    const fmtAge = (sec: number) => {
        if (sec < 60) return `${Math.floor(sec)}s ago`;
        return `${Math.floor(sec / 60)}m ago`;
    };

    const divClass = sv.divergence_pct >= 5
        ? 'div-error'
        : sv.divergence_pct >= 2
        ? 'div-warn'
        : 'div-ok';

    return (
        <div className="provenance-panel">
            <div className="prov-panel-label">DATA SOURCES</div>

            {/* TV price */}
            <div className="prov-source-chip">
                <span className={`prov-source-dot ${audit.tv_feed_ok ? 'ok' : 'err'}`} />
                <span className="prov-source-name">TV</span>
                <span className="prov-source-price">
                    {sv.tv != null ? `$${sv.tv.toFixed(2)}` : '—'}
                </span>
                <span className={`prov-age-badge ${spotStale ? 'stale' : ''}`}>
                    {spotStale ? 'STALE' : fmtAge(auditAgeSec)}
                </span>
            </div>

            <div className="prov-divider">vs</div>

            {/* YF price */}
            <div className="prov-source-chip">
                <span className={`prov-source-dot ${audit.yf_feed_ok ? 'ok' : 'err'}`} />
                <span className="prov-source-name">YF</span>
                <span className="prov-source-price">
                    {sv.yf != null ? `$${sv.yf.toFixed(2)}` : '—'}
                </span>
                <span className={`prov-age-badge ${spotStale ? 'stale' : ''}`}>
                    {spotStale ? 'STALE' : fmtAge(auditAgeSec)}
                </span>
            </div>

            {/* Divergence */}
            <div className={`prov-divergence ${divClass}`}>
                Δ {sv.divergence_pct.toFixed(3)}%
            </div>

            <div className="prov-panel-sep" />

            {/* Chain */}
            <div className="prov-source-chip">
                <span className="prov-source-dot ok" />
                <span className="prov-source-name">CHAIN</span>
                <span className="prov-source-price">{audit.options_chain_source}</span>
                <span className={`prov-age-badge ${chainStale ? 'stale' : ''}`}>
                    {chainStale ? 'STALE' : `${chainAgeSec.toFixed(0)}s`}
                </span>
            </div>

            {sv.warning && sv.divergence_pct >= 2 && (
                <div className={`prov-warn-inline ${sv.divergence_pct >= 5 ? 'error' : 'warn'}`}>
                    {sv.divergence_pct >= 5 ? '⛔' : '⚠'} {sv.warning}
                </div>
            )}
        </div>
    );
};

// ============================================================
//  Zone 1: Portfolio Command Bar (live IBKR account data)
// ============================================================
const PortfolioBar = ({
    portfolio,
    account,
    accountError,
    accountLoading: _accountLoading,
    onRetryAccount,
    simMode,
    onSimToggle,
    onSimReset,
    isFetching,
    lastFetchMs,
}: {
    portfolio: Portfolio;
    account: AccountSummary | null;
    accountError: string | null;
    accountLoading: boolean;
    onRetryAccount: () => void;
    simMode: string;
    onSimToggle: () => void;
    onSimReset: () => void;
    isFetching: boolean;
    lastFetchMs: number;
}) => {
    const [marketStatus, setMarketStatus] = useState(getMarketStatus());
    const [navDrillOpen, setNavDrillOpen] = useState(false);

    useEffect(() => {
        const id = setInterval(() => setMarketStatus(getMarketStatus()), 10000);
        return () => clearInterval(id);
    }, []);

    const staleSec = (Date.now() - lastFetchMs) / 1000;
    const isStale = staleSec > 10 && lastFetchMs > 0;

    const isPaper = simMode === 'paper';

    // In paper mode: show IBKR account data, or null (→ "...") if not yet fetched
    // In sim mode: show sim portfolio data, with IBKR overlay if available
    const netliq = isPaper ? (account ? account.net_liquidation : null) : (account?.net_liquidation ?? portfolio.nav);
    const cash   = isPaper ? (account ? account.cash_balance : null) : (account?.cash_balance ?? portfolio.cash);
    const bp     = account?.buying_power ?? 0;
    const unreal = isPaper ? (account ? account.unrealized_pnl : null) : (account?.unrealized_pnl ?? Object.values(portfolio.positions ?? {}).reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0));
    const real   = isPaper ? (account ? account.realized_pnl : null) : (account?.realized_pnl ?? portfolio.realized_pnl);
    const isLiveAccount = !!account && !account.error;

    return (
        <>
            {navDrillOpen && (
                <NavDrillModal
                    portfolio={portfolio}
                    account={account}
                    simMode={simMode}
                    onClose={() => setNavDrillOpen(false)}
                />
            )}
            {accountError && isPaper && (
                <div className="ibkr-error-banner" role="alert" aria-live="polite">
                    <span className="ibkr-error-icon">⚠</span>
                    <span className="ibkr-error-msg">{accountError}</span>
                    <button
                        className="ibkr-retry-btn"
                        onClick={onRetryAccount}
                        aria-label="Retry IBKR connection"
                        title="Retry connecting to IB Gateway"
                    >
                        Retry
                    </button>
                </div>
            )}
            <div className="portfolio-bar" role="region" aria-label="Portfolio summary">
                {/* Mode toggle pill */}
                <Tooltip text="Toggle between Paper Trading (live IBKR paper account) and Simulation (local model)">
                    <div
                        className={`port-pill mode-toggle ${simMode === 'paper' ? 'paper' : 'sim'}`}
                        onClick={onSimToggle}
                        style={{ cursor: 'pointer' }}
                        role="button"
                        tabIndex={0}
                        aria-label={`Trading mode: ${simMode}. Click to toggle.`}
                        data-tooltip="Toggle between Paper Trading (live IBKR paper account) and Simulation (local model)"
                        onKeyDown={e => e.key === 'Enter' && onSimToggle()}
                    >
                        <span className="port-pill-label">MODE</span>
                        <span className="port-pill-value">{simMode.toUpperCase()}</span>
                    </div>
                </Tooltip>

                <Tooltip text={isLiveAccount ? 'Net Liquidation Value from live IBKR paper account. Click for full NAV breakdown.' : 'Simulated NAV. Click for breakdown.'}>
                    <div
                        className="port-pill clickable"
                        onClick={() => setNavDrillOpen(true)}
                        role="button"
                        tabIndex={0}
                        aria-label={`Net Liquidation Value: ${netliq !== null ? formatUSD(netliq) : 'loading'}. Click for breakdown.`}
                        data-tooltip={isLiveAccount ? 'Net Liquidation Value from live IBKR paper account. Click for full NAV breakdown.' : 'Simulated NAV. Click for breakdown.'}
                        onKeyDown={e => e.key === 'Enter' && setNavDrillOpen(true)}
                        style={{ cursor: 'pointer' }}
                    >
                        <span className="port-pill-label">
                            NET LIQ {isLiveAccount && <span className="ibkr-live-dot" />}
                            <span className="port-pill-source">{isPaper ? '(IBKR)' : '(SIM)'}</span>
                        </span>
                        <span className="port-pill-value neutral">{netliq !== null ? formatUSD(netliq) : '—'}</span>
                    </div>
                </Tooltip>
                <Tooltip text="Available cash balance — spendable funds excluding open position values">
                    <div
                        className="port-pill"
                        data-tooltip="Available cash balance — spendable funds excluding open position values"
                        aria-label={`Cash balance: ${cash !== null ? formatUSD(cash) : 'loading'}`}
                    >
                        <span className="port-pill-label">CASH <span className="port-pill-source">{isPaper ? '(IBKR)' : '(SIM)'}</span></span>
                        <span className="port-pill-value">{cash !== null ? formatUSD(cash) : '—'}</span>
                    </div>
                </Tooltip>
                <Tooltip text="Buying power (4× cash for margin accounts)">
                    <div
                        className="port-pill"
                        data-tooltip="Buying power (4× cash for margin accounts)"
                        aria-label={`Buying power: ${formatUSD(bp)}`}
                    >
                        <span className="port-pill-label">BUY PWR</span>
                        <span className="port-pill-value">{formatUSD(bp)}</span>
                    </div>
                </Tooltip>
                <Tooltip text="Unrealized P&L: current mark-to-market on open positions">
                    <div
                        className="port-pill"
                        data-tooltip="Unrealized P&L: current mark-to-market on open positions"
                        aria-label={`Unrealized P&L: ${unreal !== null ? formatUSD(unreal) : 'loading'}`}
                    >
                        <span className="port-pill-label">UNREALIZED</span>
                        <span className={`port-pill-value ${unreal !== null && unreal >= 0 ? 'pos' : 'neg'}`}>
                            {unreal !== null ? (unreal !== 0 ? (unreal > 0 ? '+' : '') + formatUSD(unreal) : '$0.00') : '—'}
                        </span>
                    </div>
                </Tooltip>
                <Tooltip text="Realized P&L: locked-in profit/loss from closed trades">
                    <div
                        className="port-pill"
                        data-tooltip="Realized P&L: locked-in profit/loss from closed trades"
                        aria-label={`Realized P&L: ${real !== null ? formatUSD(real) : 'loading'}`}
                    >
                        <span className="port-pill-label">REALIZED <span className="port-pill-source">{isPaper ? '(IBKR)' : '(SIM)'}</span></span>
                        <span className={`port-pill-value ${real !== null && real >= 0 ? 'pos' : 'neg'}`}>
                            {real !== null ? formatUSD(real) : '—'}
                        </span>
                    </div>
                </Tooltip>

                {simMode === 'sim' && (
                    <Tooltip text="Reset simulation balances to $25k">
                        <button
                            className="sim-reset-btn"
                            onClick={onSimReset}
                            aria-label="Reset simulation portfolio to $25,000"
                            data-tooltip="Reset simulation balances to $25k"
                        >
                            RESET SIM
                        </button>
                    </Tooltip>
                )}

                <div className="market-clock">
                    <div
                        className={`market-status-badge ${marketStatus.cls}`}
                        aria-label={`Market status: ${marketStatus.label}`}
                        role="status"
                    >
                        {marketStatus.label}
                    </div>
                    <Tooltip text={`Data last refreshed ${Math.floor(staleSec)}s ago. Over 10s is considered stale — prices may not reflect current market.`}>
                        <div
                            className="live-indicator"
                            data-tooltip={`Data last refreshed ${Math.floor(staleSec)}s ago. Over 10s is stale.`}
                            aria-label={isStale ? 'Data is stale' : isFetching ? 'Refreshing data' : 'Data is live'}
                            role="status"
                        >
                            <div className={`live-dot ${isStale ? 'stale' : 'active'}`} aria-hidden="true" />
                            <span>{isStale ? 'Stale' : 'Live'}</span>
                        </div>
                    </Tooltip>
                </div>
            </div>
        </>
    );
};

// ============================================================
//  Column A: Signal Command
// ============================================================
const SignalCommand = ({
    signals,
    newTimestamps,
    onSelectSignal,
    systemState,
}: {
    signals: Signal[];
    newTimestamps: Set<number>;
    onSelectSignal: (s: Signal) => void;
    systemState: SystemState | null;
}) => {
    const displayed = signals.slice(0, 10);
    const hasNew = newTimestamps.size > 0;

    const emptyContent = () => {
        const reason = systemState?.signals_blocked_reason;
        if (reason === 'kill_switch') return (
            <div className="empty-position-card">
                <div className="empty-position-icon" style={{ color: '#f85149' }}>⛔</div>
                <div style={{ color: '#8b949e' }}>Kill Switch active — signals monitored, no orders</div>
            </div>
        );
        if (reason === 'market_closed') return (
            <div className="empty-position-card">
                <div className="empty-position-icon">🌙</div>
                <div style={{ color: '#8b949e' }}>Market closed — next open Mon-Fri 9:30am ET</div>
            </div>
        );
        if (reason === 'no_signals') return (
            <div className="empty-position-card">
                <div className="empty-position-icon">📡</div>
                <div style={{ color: '#8b949e' }}>Scanning… no high-conviction signals yet</div>
            </div>
        );
        return (
            <div className="empty-position-card">
                <div className="empty-position-icon">📡</div>
                <div>Awaiting signals from Alpha Engine…</div>
                <div style={{ fontSize: '0.72rem', marginTop: '0.35rem', color: '#30363d' }}>
                    Signals appear here when the intelligence layer fires
                </div>
            </div>
        );
    };

    return (
        <div className="dash-col card" role="region" aria-label="Signal Command — live Alpha Engine signals">
            <div className="section-header">
                <Tooltip text="Live conviction signals from the Alpha Engine. Click any card for full details.">
                    <span className="section-title" data-tooltip="Live conviction signals from the Alpha Engine. Click any card for full details.">⚡ SIGNAL COMMAND</span>
                </Tooltip>
                <div className="section-meta">
                    <span className="inline-badge" aria-label={`${signals.length} signals`}>{signals.length}</span>
                    {hasNew && <span className="inline-badge new" aria-label="New signals available">NEW</span>}
                </div>
            </div>
            <div className="scroll-col">
                {displayed.length === 0 ? emptyContent() : (
                    displayed.map((s, i) => {
                        const isBull = s.direction === 'BULLISH';
                        const isNew = newTimestamps.has(s.timestamp);
                        const isStale = (Date.now() / 1000 - s.timestamp) > 86400; // >24h
                        const contract = contractName(s);
                        const confPct = (s.confidence ?? 0) * 100;
                        return (
                            <div
                                key={`${s.timestamp}-${i}`}
                                className={`signal-card ${isBull ? 'bullish' : 'bearish'} ${isNew ? 'signal-new' : ''} ${isStale ? 'signal-stale' : ''}`}
                                onClick={() => onSelectSignal(s)}
                                title="Click for full signal breakdown"
                                role="button"
                                tabIndex={0}
                                aria-label={`${s.direction} signal: ${contract}, ${confPct.toFixed(1)}% confidence. Click for full breakdown.`}
                                onKeyDown={e => e.key === 'Enter' && onSelectSignal(s)}
                            >
                                <div className="signal-top-row">
                                    <Tooltip text={contractExplanation(s)}>
                                        <span className="signal-contract">{contract}</span>
                                    </Tooltip>
                                    <span className={`signal-action-badge ${isBull ? 'bullish' : 'bearish'}`}>
                                        {s.action}
                                    </span>
                                </div>
                                <div className="conviction-bar-wrap" role="meter" aria-valuenow={confPct} aria-valuemin={0} aria-valuemax={100} aria-label={`Conviction: ${confPct.toFixed(1)}%`}>
                                    <div className="conviction-bar-track">
                                        <div
                                            className="conviction-bar-fill"
                                            style={{ width: `${confPct.toFixed(1)}%` }}
                                        />
                                    </div>
                                    <div className="conviction-label">
                                        <Tooltip text="Model conviction: probability the signal is correct">
                                            <span data-tooltip="Model conviction: probability the signal is correct">{confPct.toFixed(1)}% conviction</span>
                                        </Tooltip>
                                    </div>
                                </div>
                                <div className="signal-stats-grid">
                                    <div className="signal-stat">
                                        <span className="signal-stat-key">
                                            <Tooltip text="Target limit price: ideal entry">LIMIT</Tooltip>
                                        </span>
                                        <span className="signal-stat-val">{formatUSD(s.target_limit_price)}</span>
                                    </div>
                                    <div className="signal-stat">
                                        <span className="signal-stat-key">
                                            <Tooltip text="Take profit target">EXIT</Tooltip>
                                        </span>
                                        <span className="signal-stat-val green">{formatUSD(s.take_profit_price)}</span>
                                    </div>
                                    <div className="signal-stat">
                                        <span className="signal-stat-key">
                                            <Tooltip text="Stop loss level">STOP</Tooltip>
                                        </span>
                                        <span className="signal-stat-val red">{formatUSD(s.stop_loss_price)}</span>
                                    </div>
                                    <div className="signal-stat">
                                        <span className="signal-stat-key">
                                            <Tooltip text="Kelly criterion: recommended capital allocation %">KELLY</Tooltip>
                                        </span>
                                        <span className="signal-stat-val">{(s.kelly_wager_pct * 100).toFixed(1)}%</span>
                                    </div>
                                    <div className="signal-stat">
                                        <span className="signal-stat-key">
                                            <Tooltip text="Recommended contract quantity">QTY</Tooltip>
                                        </span>
                                        <span className="signal-stat-val">{s.quantity}</span>
                                    </div>
                                    {s.implied_volatility != null && s.implied_volatility > 0 && (
                                        <div className="signal-stat">
                                            <span className="signal-stat-key">
                                                <Tooltip text="Implied volatility from live options chain">IV</Tooltip>
                                            </span>
                                            <span className="signal-stat-val">{(s.implied_volatility * 100).toFixed(0)}%</span>
                                        </div>
                                    )}
                                    {s.is_spread && (
                                        <div className="signal-stat">
                                            <span className="signal-stat-key">SPREAD</span>
                                            <span className="signal-stat-val">
                                                ${s.short_strike}/${s.long_strike}
                                            </span>
                                        </div>
                                    )}
                                </div>
                                {/* ── Economics row ─────────────── */}
                                {(() => {
                                    const eco = computeEconomics(s);
                                    const fmtC = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 });
                                    const fmtPrice = (n: number) => `$${n.toFixed(3)}`;
                                    const rrCls = rrColorClass(eco.risk_reward_ratio);
                                    if (eco.is_na) {
                                        return (
                                            <div className="signal-economics-row">
                                                <span className="econ-pill">
                                                    <span className="econ-pill-key">Economics</span>
                                                    <Tooltip text={eco.na_reason ?? 'Cannot compute'}>
                                                        <span className="econ-pill-val na" data-tooltip={eco.na_reason}>N/A</span>
                                                    </Tooltip>
                                                </span>
                                            </div>
                                        );
                                    }
                                    return (
                                        <div className="signal-economics-row" data-testid="signal-economics-row">
                                            <span className="econ-pill">
                                                <span className="econ-pill-key">Max Profit</span>
                                                <Tooltip text={`Net profit at TP ${s.take_profit_price}: (TP - entry) × qty × 100 - round-trip commission`}>
                                                    <span className="econ-pill-val green"
                                                          data-tooltip={`(${s.take_profit_price} - ${s.target_limit_price}) × ${s.quantity} × 100 - ${eco.round_trip_commission.toFixed(2)}`}
                                                          data-testid="card-max-profit">
                                                        {fmtC(eco.max_profit_at_tp)} (at ${s.take_profit_price})
                                                    </span>
                                                </Tooltip>
                                            </span>
                                            <span className="econ-pill">
                                                <span className="econ-pill-key">Max Loss</span>
                                                <Tooltip text={`Net loss at SL ${s.stop_loss_price}: (entry - SL) × qty × 100 + round-trip commission`}>
                                                    <span className="econ-pill-val red"
                                                          data-tooltip={`(${s.target_limit_price} - ${s.stop_loss_price}) × ${s.quantity} × 100 + ${eco.round_trip_commission.toFixed(2)}`}
                                                          data-testid="card-max-loss">
                                                        {fmtC(eco.max_loss_at_sl)} (at ${s.stop_loss_price})
                                                    </span>
                                                </Tooltip>
                                            </span>
                                            <span className="econ-pill">
                                                <span className="econ-pill-key">R:R</span>
                                                <Tooltip text={`Risk:Reward = net_profit / net_loss = ${eco.max_profit_at_tp.toFixed(2)} / ${eco.max_loss_at_sl.toFixed(2)}`}>
                                                    <span className={`econ-pill-val ${rrCls}`}
                                                          data-tooltip={`Per $1 risked, $${eco.risk_reward_ratio.toFixed(2)} expected at TP`}
                                                          data-testid="card-rr">
                                                        {formatRR(eco.risk_reward_ratio)}
                                                    </span>
                                                </Tooltip>
                                            </span>
                                            <span className="econ-pill">
                                                <span className="econ-pill-key">Breakeven</span>
                                                <Tooltip text={`entry + round_trip_commission / qty / 100 = ${s.target_limit_price} + ${eco.round_trip_commission.toFixed(2)} / ${s.quantity} / 100`}>
                                                    <span className="econ-pill-val neutral"
                                                          data-tooltip={`entry (${s.target_limit_price}) + commission spread (${(eco.round_trip_commission / s.quantity / 100).toFixed(4)})`}
                                                          data-testid="card-breakeven">
                                                        {fmtPrice(eco.breakeven)}
                                                    </span>
                                                </Tooltip>
                                            </span>
                                        </div>
                                    );
                                })()}
                                <div className="signal-bottom-row">
                                    <span className="signal-time">
                                        {new Date(s.timestamp * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })}
                                        {' · '}
                                        {relativeTime(s.timestamp)}
                                    </span>
                                    <Tooltip text={`Signal from ${s.model_id ?? 'unknown'} model. Spot at signal: $${s.underlying_price?.toFixed(2) ?? '—'}`}>
                                        <span className={`direction-badge ${isBull ? 'bullish' : 'bearish'}`}>
                                            {s.direction}
                                        </span>
                                    </Tooltip>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
};

// ============================================================
//  Position Drill-Down Modal
// ============================================================
const PositionModal = ({ pos, onClose }: { pos: IBKRPosition; onClose: () => void }) => {
    const label = pos.option_type
        ? `${pos.ticker} $${pos.strike}${pos.option_type[0]} ${pos.expiration}`
        : pos.ticker;
    const pnl = pos.unrealized_pnl;
    const pnlPct = pos.avg_cost > 0 ? ((pos.current_price - pos.avg_cost) / pos.avg_cost * 100) : 0;
    return (
        <div className="signal-modal-overlay" onClick={onClose}>
            <div className="signal-modal" onClick={e => e.stopPropagation()}>
                <div className="signal-modal-header">
                    <span className="signal-modal-title">{label}</span>
                    <button className="btn-modal-close" onClick={onClose} aria-label="Close position detail">×</button>
                </div>
                <div className="modal-grid">
                    <div className="modal-row">
                        <span className="modal-key">Quantity</span>
                        <Tooltip text="Number of contracts held">
                            <span className="modal-val">{pos.qty}</span>
                        </Tooltip>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Avg Cost</span>
                        <Tooltip text="Average fill price per contract">
                            <span className="modal-val">{formatUSD(pos.avg_cost)}</span>
                        </Tooltip>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Current</span>
                        <Tooltip text="Latest IBKR delayed market price">
                            <span className="modal-val">{pos.current_price > 0 ? formatUSD(pos.current_price) : '—'}</span>
                        </Tooltip>
                    </div>
                    <div className="modal-row">
                        <span className="modal-key">Market Value</span>
                        <span className="modal-val">{formatUSD(pos.market_value)}</span>
                    </div>
                    <div className="modal-row full-width">
                        <span className="modal-key">Unrealized P&L</span>
                        <span className={`modal-val ${pnl >= 0 ? 'green' : 'red'}`}>
                            {pnl >= 0 ? '+' : ''}{formatUSD(pnl)} ({pnlPct.toFixed(1)}%)
                        </span>
                    </div>
                    {pos.option_type && (
                        <>
                            <div className="modal-row">
                                <span className="modal-key">Strike</span>
                                <Tooltip text="Option strike price">
                                    <span className="modal-val">${pos.strike}</span>
                                </Tooltip>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Expiration</span>
                                <Tooltip text="Option expiration date">
                                    <span className="modal-val">{pos.expiration}</span>
                                </Tooltip>
                            </div>
                            <div className="modal-row">
                                <span className="modal-key">Type</span>
                                <span className="modal-val">{pos.option_type}</span>
                            </div>
                        </>
                    )}
                    {pos.catalyst && (
                        <div className="modal-row full-width">
                            <span className="modal-key">Catalyst</span>
                            <span className="modal-val" style={{ fontSize: '0.78rem', lineHeight: 1.4 }}>
                                {pos.model_id && <span className="inline-badge" style={{ marginRight: '0.4rem' }}>{pos.model_id}</span>}
                                {pos.catalyst}
                            </span>
                        </div>
                    )}
                    <div className="modal-row full-width" style={{ marginTop: '0.5rem' }}>
                        <a
                            href="https://www.interactivebrokers.com/en/trading/portfolio.php"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ibkr-verify-link"
                        aria-label="Verify this position on IBKR website (opens in new tab)"
                        title="Verify this position on the IBKR portfolio page"
                        >
                            Verify on IBKR →
                        </a>
                    </div>
                </div>
            </div>
        </div>
    );
};

// ============================================================
//  Pending Orders — interface + components
// ============================================================
interface PendingOrder {
    orderId: number;
    status: string;
    symbol: string;
    action: string;
    qty: number;
    strike: number;
    expiry: string;
    option_type: string;
    limit_price: number;
    filled_qty: number;
    avg_fill_price: number;
    timestamp: string;
    rank?: number;  // signal rank [0,1] from pending cap tracker
}

interface PendingOrdersResponse {
    active: PendingOrder[];
    cancelled: PendingOrder[];
    source: string;
    cap?: number;   // MAX_PENDING_ORDERS value
    error?: string;
}

interface CapEvent {
    ts: string;
    kind: 'REPLACE' | 'REJECT-CAP';
    cancelled_id: number;
    cancelled_rank: number;
    incoming_rank: number;
}

function orderLabel(o: PendingOrder): string {
    const strike = o.strike > 0 ? `$${o.strike}` : '';
    const type   = o.option_type ? o.option_type[0] : '';
    const exp    = o.expiry || '';
    return `${o.symbol} ${strike}${type} ${exp}`.trim();
}

function fillWindowLabel(): string {
    const now = new Date();
    const fmt = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        hour: 'numeric', minute: 'numeric', hour12: false,
        weekday: 'short',
    });
    const parts = fmt.formatToParts(now);
    const h = parseInt(parts.find(p => p.type === 'hour')?.value ?? '0', 10);
    const m = parseInt(parts.find(p => p.type === 'minute')?.value ?? '0', 10);
    const wd = parts.find(p => p.type === 'weekday')?.value ?? '';
    const t = h * 60 + m;
    const isWeekend = ['Sat', 'Sun'].includes(wd);
    const marketOpen = t >= 570 && t < 960; // 9:30–16:00 ET
    if (marketOpen && !isWeekend) return 'fills next tick';
    return 'fills next Monday 9:30 AM ET';
}

/** Returns today's date as YYYY-MM-DD in Eastern Time for expiry comparison. */
function todayET(): string {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const y = parts.find(p => p.type === 'year')?.value  ?? '';
    const mo = parts.find(p => p.type === 'month')?.value ?? '';
    const d  = parts.find(p => p.type === 'day')?.value   ?? '';
    return `${y}-${mo}-${d}`;
}

/** Minutes remaining until 15:30 ET today (negative if already past). */
function minsUntilExpiryClose(): number {
    const fmt = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        hour: 'numeric', minute: 'numeric', hour12: false,
    });
    const parts = fmt.formatToParts(new Date());
    const h = parseInt(parts.find(p => p.type === 'hour')?.value ?? '0', 10);
    const m = parseInt(parts.find(p => p.type === 'minute')?.value ?? '0', 10);
    return (15 * 60 + 30) - (h * 60 + m);
}

const PendingOrderModal = ({ order, onClose }: { order: PendingOrder; onClose: () => void }) => {
    // Approximate rank breakdown for display (mirrors computeRank in subscriber.go).
    const rankBreakdown = order.rank != null ? { total: order.rank } : null;

    return (
        <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Pending Order Detail">
            <div className="modal-card fill-drill" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <span className="modal-title">⏳ PENDING ORDER #{order.orderId}</span>
                    <button className="modal-close" onClick={onClose} aria-label="Close pending order detail">✕</button>
                </div>
                <div className="fill-drill-body pending-order-modal-body">
                    <div className={`fill-pnl-banner ${order.status === 'Filled' ? 'win' : ''}`}
                         style={{ background: 'rgba(139,92,246,0.12)', borderColor: 'rgba(139,92,246,0.4)' }}>
                        <span className="fill-pnl-label" style={{ color: '#c9d1d9' }}>{order.action} {orderLabel(order)}</span>
                        <span className="fill-pnl-value" style={{ color: '#e6a200' }}>{order.status}</span>
                    </div>
                    <div className="fill-section">
                        <div className="fill-section-title">Order Detail</div>
                        <div className="fill-grid">
                            <div className="fill-row"><span>Order ID</span><span>#{order.orderId}</span></div>
                            <div className="fill-row"><span>Status</span><span style={{ color: '#e6a200', fontWeight: 700 }}>{order.status}</span></div>
                            <div className="fill-row"><span>Symbol</span><span>{order.symbol}</span></div>
                            <div className="fill-row"><span>Action</span><span>{order.action}</span></div>
                            <div className="fill-row"><span>Option Type</span><span>{order.option_type || '—'}</span></div>
                            <div className="fill-row"><span>Strike</span><span>{order.strike > 0 ? `$${order.strike}` : '—'}</span></div>
                            <div className="fill-row"><span>Expiry</span><span>{order.expiry || '—'}</span></div>
                            <div className="fill-row"><span>Qty</span><span>{order.qty}</span></div>
                            <div className="fill-row"><span>Limit Price</span><span>{order.limit_price > 0 ? formatUSD(order.limit_price) : '—'}</span></div>
                            <div className="fill-row"><span>Filled Qty</span><span>{order.filled_qty}</span></div>
                            <div className="fill-row"><span>Avg Fill Price</span><span>{order.avg_fill_price > 0 ? formatUSD(order.avg_fill_price) : '—'}</span></div>
                            <div className="fill-row"><span>Submitted At</span><span>{order.timestamp ? new Date(order.timestamp).toLocaleString() : '—'}</span></div>
                        </div>
                    </div>
                    {rankBreakdown && (
                        <div className="fill-section">
                            <div className="fill-section-title">Signal Rank</div>
                            <div className="fill-grid">
                                <div className="fill-row">
                                    <span>Overall Rank</span>
                                    <span style={{ color: '#58a6ff', fontWeight: 700 }}>{rankBreakdown.total.toFixed(3)}</span>
                                </div>
                                <div className="fill-row" style={{ fontSize: '0.72rem', color: '#8b949e' }}>
                                    <span>Formula</span>
                                    <span>confidence×0.5 + roi×0.3 + recency×0.2</span>
                                </div>
                            </div>
                        </div>
                    )}
                    <div className="fill-section">
                        <div className="fill-section-title">Fill Window</div>
                        <div style={{ fontSize: '0.78rem', color: '#58a6ff', padding: '4px 0' }}>{fillWindowLabel()}</div>
                    </div>
                    <div className="fill-section">
                        <div className="fill-section-title">Raw IBKR State</div>
                        <div className="raw-order-block">{JSON.stringify(order, null, 2)}</div>
                    </div>
                </div>
            </div>
        </div>
    );
};

// ============================================================
//  Column B: Pending Orders
// ============================================================
const PendingOrdersPanel = ({
    orders,
    source,
    capEvents,
    replacementToast,
}: {
    orders: PendingOrdersResponse | null;
    source: string;
    capEvents: CapEvent[];
    replacementToast: CapEvent | null;
}) => {
    const [selectedOrder, setSelectedOrder] = useState<PendingOrder | null>(null);
    const [cancelledOpen, setCancelledOpen] = useState(false);
    const [eventsOpen, setEventsOpen] = useState(false);

    const active    = orders?.active    ?? [];
    const cancelled = orders?.cancelled ?? [];
    const cap       = orders?.cap       ?? 2;
    const hasError  = !!orders?.error;

    // Header color: red at cap, amber at cap-1, green below
    const capColor = active.length >= cap
        ? '#f85149'
        : active.length >= cap - 1
        ? '#e6a200'
        : '#3fb950';

    // Expiring-today badge
    const [today, setToday] = useState(todayET());
    const [minsLeft, setMinsLeft] = useState(minsUntilExpiryClose());
    useEffect(() => {
        const id = setInterval(() => {
            setToday(todayET());
            setMinsLeft(minsUntilExpiryClose());
        }, 60_000);
        return () => clearInterval(id);
    }, []);
    const expiringTodayCount = active.filter(o => o.expiry === today).length;
    const countdownLabel = minsLeft > 0
        ? `auto-close in ${Math.floor(minsLeft / 60)}h ${minsLeft % 60}m`
        : 'auto-close window passed';

    return (
        <>
            {selectedOrder && (
                <PendingOrderModal order={selectedOrder} onClose={() => setSelectedOrder(null)} />
            )}

            {/* Replacement toast */}
            {replacementToast && (
                <div
                    role="status"
                    aria-live="polite"
                    data-testid="cap-replacement-toast"
                    style={{
                        position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
                        background: 'rgba(88,166,255,0.15)',
                        border: '1px solid rgba(88,166,255,0.5)',
                        borderRadius: 8, padding: '10px 16px',
                        fontSize: '0.78rem', color: '#c9d1d9', maxWidth: 320,
                    }}
                >
                    {replacementToast.kind === 'REPLACE'
                        ? `Better signal replaced orderId=${replacementToast.cancelled_id} (rank ${replacementToast.cancelled_rank.toFixed(2)} → ${replacementToast.incoming_rank.toFixed(2)})`
                        : `Signal rejected — cap full (rank ${replacementToast.incoming_rank.toFixed(2)} below cutoff ${replacementToast.cancelled_rank.toFixed(2)})`
                    }
                </div>
            )}

            <div className="dash-col card" role="region" aria-label="Pending Orders — queued IBKR orders">
                <div className="section-header">
                    <Tooltip text="Orders submitted to IBKR but not yet filled. Click any row for full state.">
                        <span className="section-title" data-tooltip="Orders submitted to IBKR but not yet filled. Click any row for full state.">
                            ⏳ PENDING ORDERS
                        </span>
                    </Tooltip>
                    <div className="section-meta">
                        {/* "Pending (N/max)" badge — color by saturation */}
                        <span
                            className="inline-badge"
                            aria-label={`${active.length} of ${cap} pending orders`}
                            style={{ color: capColor, border: `1px solid ${capColor}44`, background: `${capColor}11` }}
                        >
                            {active.length}/{cap}
                        </span>
                        {/* Expiring-today badge — shown when any pending order expires today */}
                        {expiringTodayCount > 0 && (
                            <span
                                className="inline-badge"
                                aria-label={`${expiringTodayCount} order${expiringTodayCount !== 1 ? 's' : ''} expiring today — ${countdownLabel}`}
                                title={`${expiringTodayCount} expiring today — ${countdownLabel}`}
                                style={{ background: 'rgba(248,81,73,0.12)', color: '#f85149', border: '1px solid rgba(248,81,73,0.4)' }}
                            >
                                ⚠ {expiringTodayCount} expiring
                            </span>
                        )}
                        {source && source !== 'SIMULATION' && (
                            <span className="inline-badge" style={{ background: 'rgba(139,92,246,0.15)', color: '#a371f7' }}>
                                {source.replace('IBKR_', '')}
                            </span>
                        )}
                    </div>
                </div>
                <div className="scroll-col">
                    {hasError && (
                        <div className="empty-position-card">
                            <div className="empty-position-icon" style={{ color: '#f85149' }}>⚠</div>
                            <div style={{ color: '#f85149', fontSize: '0.72rem' }}>{orders!.error}</div>
                        </div>
                    )}
                    {!hasError && active.length === 0 && (
                        <div className="empty-position-card">
                            <div className="empty-position-icon" style={{ color: '#8b949e' }}>📭</div>
                            <div style={{ color: '#8b949e' }}>No pending orders</div>
                        </div>
                    )}
                    {active.map(o => (
                        <div
                            key={o.orderId}
                            className="pending-order-row"
                            onClick={() => setSelectedOrder(o)}
                            role="button"
                            tabIndex={0}
                            aria-label={`Order #${o.orderId} — ${o.action} ${orderLabel(o)} — ${o.status}. Click for detail.`}
                            onKeyDown={e => e.key === 'Enter' && setSelectedOrder(o)}
                        >
                            <div className="pending-order-top">
                                <span>{o.action}</span>
                                <Tooltip text={`${o.option_type || ''} ${o.strike > 0 ? `strike $${o.strike}` : ''} exp ${o.expiry}`}>
                                    <span>{orderLabel(o)}</span>
                                </Tooltip>
                                <span className="pending-status-badge">{o.status}</span>
                                <span className="pending-order-id">#{o.orderId}</span>
                                {/* Rank badge */}
                                {o.rank != null && o.rank > 0 && (
                                    <span
                                        className="inline-badge"
                                        title={`Signal rank: ${o.rank.toFixed(3)}`}
                                        style={{ fontSize: '0.65rem', color: '#58a6ff', background: 'rgba(88,166,255,0.1)', border: '1px solid rgba(88,166,255,0.3)' }}
                                    >
                                        rank: {o.rank.toFixed(2)}
                                    </span>
                                )}
                            </div>
                            <div className="pending-order-detail">
                                <span>×{o.qty}</span>
                                {o.limit_price > 0 && <span>@ {formatUSD(o.limit_price)}</span>}
                                {o.filled_qty > 0 && <span style={{ color: '#3fb950' }}>filled {o.filled_qty}</span>}
                            </div>
                            <div className="pending-fill-window">{fillWindowLabel()}</div>
                        </div>
                    ))}
                    {cancelled.length > 0 && (
                        <div className="cancelled-accordion">
                            <div
                                className="cancelled-accordion-header"
                                onClick={() => setCancelledOpen(v => !v)}
                                role="button"
                                tabIndex={0}
                                aria-expanded={cancelledOpen}
                                aria-label={`Cancelled today — ${cancelled.length} orders. Click to ${cancelledOpen ? 'collapse' : 'expand'}.`}
                                onKeyDown={e => e.key === 'Enter' && setCancelledOpen(v => !v)}
                            >
                                <span>Cancelled today ({cancelled.length})</span>
                                <span style={{ transform: cancelledOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}>▶</span>
                            </div>
                            {cancelledOpen && cancelled.map((o, i) => (
                                <div
                                    key={`${o.orderId}-${i}`}
                                    className="cancelled-order-row"
                                    onClick={() => setSelectedOrder(o)}
                                    role="button"
                                    tabIndex={0}
                                    aria-label={`Cancelled: order #${o.orderId} ${orderLabel(o)}`}
                                    onKeyDown={e => e.key === 'Enter' && setSelectedOrder(o)}
                                    style={{ cursor: 'pointer' }}
                                >
                                    #{o.orderId} {o.action} {orderLabel(o)} — Cancelled
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Cap event feed — last 10 REPLACE / REJECT-CAP events */}
                    {capEvents.length > 0 && (
                        <div className="cancelled-accordion" style={{ marginTop: 8 }}>
                            <div
                                className="cancelled-accordion-header"
                                onClick={() => setEventsOpen(v => !v)}
                                role="button"
                                tabIndex={0}
                                aria-expanded={eventsOpen}
                                aria-label={`Cap events — ${capEvents.length} recent. Click to ${eventsOpen ? 'collapse' : 'expand'}.`}
                                onKeyDown={e => e.key === 'Enter' && setEventsOpen(v => !v)}
                            >
                                <span>Cap events ({capEvents.length})</span>
                                <span style={{ transform: eventsOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}>▶</span>
                            </div>
                            {eventsOpen && capEvents.map((ev, i) => (
                                <div
                                    key={`${ev.ts}-${i}`}
                                    style={{
                                        padding: '4px 10px',
                                        fontSize: '0.7rem',
                                        color: ev.kind === 'REPLACE' ? '#3fb950' : '#e6a200',
                                        borderBottom: '1px solid rgba(48,54,61,0.5)',
                                    }}
                                >
                                    <span style={{ color: '#8b949e', marginRight: 6 }}>{new Date(ev.ts).toLocaleTimeString()}</span>
                                    {ev.kind === 'REPLACE'
                                        ? `[REPLACE] cancelled #${ev.cancelled_id} (rank ${ev.cancelled_rank.toFixed(2)}) → ${ev.incoming_rank.toFixed(2)}`
                                        : `[REJECT-CAP] rank ${ev.incoming_rank.toFixed(2)} below ${ev.cancelled_rank.toFixed(2)}`
                                    }
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </>
    );
};

// ============================================================
//  Column C: Trading Floor (IBKR live positions + sim fallback)
// ============================================================
const TradingFloor = ({  // Column C in 4-col grid
    portfolio,
    ibkrPositions,
    brokerStatus,
}: {
    portfolio: Portfolio;
    ibkrPositions: IBKRPosition[];
    brokerStatus: BrokerStatus | null;
}) => {
    const [selectedPos, setSelectedPos] = useState<IBKRPosition | null>(null);
    const prevPnlRef = useRef<Record<string, number>>({});
    const [flipKeys, setFlipKeys] = useState<Set<string>>(new Set());

    const useIBKR = ibkrPositions.length > 0;
    // Show sim positions when no IBKR positions (IBKR client is a stub, sim positions are the real activity)
    const showSimPortfolio = !useIBKR;

    const totalUnrealized = useIBKR
        ? ibkrPositions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0)
        : showSimPortfolio
        ? Object.values(portfolio.positions ?? {}).reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0)
        : 0;

    // Flip detection for IBKR positions
    useEffect(() => {
        if (!useIBKR) return;
        const prev = prevPnlRef.current;
        const newFlips = new Set<string>();
        for (const pos of ibkrPositions) {
            const key = `${pos.ticker}_${pos.strike}_${pos.expiration}`;
            const prevPnl = prev[key];
            if (prevPnl !== undefined) {
                if ((prevPnl >= 0) !== (pos.unrealized_pnl >= 0)) newFlips.add(key);
            }
            prev[key] = pos.unrealized_pnl;
        }
        if (newFlips.size > 0) {
            setFlipKeys(newFlips);
            const id = setTimeout(() => setFlipKeys(new Set()), 1000);
            return () => clearTimeout(id);
        }
    }, [ibkrPositions, useIBKR]);

    return (
        <>
            {selectedPos && <PositionModal pos={selectedPos} onClose={() => setSelectedPos(null)} />}
            <div className="dash-col card" role="region" aria-label="Trading Floor — open positions">
                <div className="section-header">
                    <Tooltip text="Open positions from live IBKR paper account. Click any card for drill-down.">
                        <span className="section-title" data-tooltip="Open positions from live IBKR paper account. Click any card for drill-down.">🏛 TRADING FLOOR</span>
                    </Tooltip>
                    <div className="section-meta">
                        {brokerStatus && (
                            <span className={`inline-badge mode-${brokerStatus.mode}`}>{brokerStatus.mode.toUpperCase()}</span>
                        )}
                        {(useIBKR ? ibkrPositions.length : Object.keys(portfolio.positions ?? {}).length) > 0 && (
                            <span className={`inline-badge ${totalUnrealized >= 0 ? 'green' : 'red'}`}>
                                {formatUSD(totalUnrealized)}
                            </span>
                        )}
                    </div>
                </div>
                <div className="scroll-col">
                    {useIBKR ? (
                        ibkrPositions.map((pos) => {
                            const key = `${pos.ticker}_${pos.strike}_${pos.expiration}_${pos.option_type}`;
                            const pnl = pos.unrealized_pnl ?? 0;
                            const isProfit = pnl >= 0;
                            const isFlip = flipKeys.has(key);
                            const label = pos.option_type
                                ? `${pos.ticker} $${pos.strike}${pos.option_type[0]} ${pos.expiration}`
                                : pos.ticker;
                            const pnlPct = pos.avg_cost > 0
                                ? ((pos.current_price - pos.avg_cost) / pos.avg_cost * 100)
                                : 0;
                            return (
                                <div
                                    key={key}
                                    className={`position-card ${isProfit ? 'profit' : 'loss'} ${isFlip ? 'pnl-flip' : ''}`}
                                    onClick={() => setSelectedPos(pos)}
                                    style={{ cursor: 'pointer' }}
                                    role="button"
                                    tabIndex={0}
                                    aria-label={`${label} — ${pnl >= 0 ? '+' : ''}${formatUSD(pnl)} unrealized P&L. Click for drill-down.`}
                                    onKeyDown={e => e.key === 'Enter' && setSelectedPos(pos)}
                                >
                                    <div className="position-header">
                                        <Tooltip text={`${pos.ticker} ${pos.option_type || 'STOCK'} — click for full details`}>
                                            <span className="position-contract">{label}</span>
                                        </Tooltip>
                                        <Tooltip text={`${pos.qty} contract${Math.abs(pos.qty) !== 1 ? 's' : ''} held`}>
                                            <span className="qty-badge">×{pos.qty}</span>
                                        </Tooltip>
                                    </div>
                                    <div className="position-stats">
                                        <div className="pos-stat">
                                            <div className="pos-stat-key">
                                                <Tooltip text="Average fill price">ENTRY</Tooltip>
                                            </div>
                                            <div className="pos-stat-val">{formatUSD(pos.avg_cost)}</div>
                                        </div>
                                        <div className="pos-stat">
                                            <div className="pos-stat-key">
                                                <Tooltip text="Current IBKR delayed price">CURRENT</Tooltip>
                                            </div>
                                            <div className="pos-stat-val">
                                                {pos.current_price > 0 ? formatUSD(pos.current_price) : '—'}
                                            </div>
                                        </div>
                                    </div>
                                    <div className={`position-pnl ${isProfit ? 'pos' : 'neg'}`}>
                                        <Tooltip text="Unrealized P&L — click card for drill-down">
                                            <span>
                                                {isProfit ? '+' : ''}{formatUSD(pnl)}
                                                <span style={{ fontSize: '0.72rem', marginLeft: '0.4rem', opacity: 0.7 }}>
                                                    ({pnlPct.toFixed(1)}%)
                                                </span>
                                            </span>
                                        </Tooltip>
                                    </div>
                                    {pos.catalyst && (
                                        <div className="position-catalyst">
                                            <span className="catalyst-model">{pos.model_id}</span>
                                            <span className="catalyst-text">{pos.catalyst.slice(0, 80)}{pos.catalyst.length > 80 ? '…' : ''}</span>
                                        </div>
                                    )}
                                </div>
                            );
                        })
                    ) : showSimPortfolio ? (
                        // Sim fallback (only when explicitly in sim mode)
                        Object.keys(portfolio.positions ?? {}).length === 0 ? (
                            <div className="empty-position-card">
                                <div className="empty-position-icon">🎯</div>
                                <div>NO OPEN POSITIONS — STANDING BY</div>
                                <div style={{ fontSize: '0.72rem', marginTop: '0.35rem', color: '#30363d' }}>
                                    Positions appear here when signals execute
                                </div>
                            </div>
                        ) : (
                            Object.entries(portfolio.positions ?? {}).map(([key, pos]) => {
                                const pnl = pos.unrealized_pnl ?? 0;
                                const isProfit = pnl >= 0;
                                return (
                                    <div key={key} className={`position-card ${isProfit ? 'profit' : 'loss'}`}>
                                        <div className="position-header">
                                            <span className="position-contract" title={key}>{key}</span>
                                            <Tooltip text="Number of contracts held">
                                                <span className="qty-badge">×{pos.quantity}</span>
                                            </Tooltip>
                                        </div>
                                        <div className="position-stats">
                                            <div className="pos-stat">
                                                <div className="pos-stat-key">
                                                    <Tooltip text="Average entry price paid per contract">ENTRY</Tooltip>
                                                </div>
                                                <div className="pos-stat-val">{formatUSD(pos.entry_price)}</div>
                                            </div>
                                            <div className="pos-stat">
                                                <div className="pos-stat-key">
                                                    <Tooltip text="Current mark-to-market price">CURRENT</Tooltip>
                                                </div>
                                                <div className="pos-stat-val">{formatUSD(pos.current_price)}</div>
                                            </div>
                                        </div>
                                        <div className={`position-pnl ${isProfit ? 'pos' : 'neg'}`}>
                                            <Tooltip text="Unrealized P&L: profit/loss if closed now">
                                                <span>{isProfit ? '+' : ''}{formatUSD(pnl)}</span>
                                            </Tooltip>
                                        </div>
                                        {pos.entry_time && (
                                            <div className="position-footer">Opened {relativeTime(pos.entry_time)}</div>
                                        )}
                                    </div>
                                );
                            })
                        )
                    ) : (
                        // Paper mode — waiting for first IBKR position
                        <div className="empty-position-card">
                            <div className="empty-position-icon">📡</div>
                            <div>PAPER MODE — NO OPEN POSITIONS</div>
                            <div style={{ fontSize: '0.72rem', marginTop: '0.35rem', color: '#58a6ff' }}>
                                Waiting for first signal execution via IBKR
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </>
    );
};

// ============================================================
//  Column C: Execution Log
// ============================================================

interface FillDetail {
    fill: Record<string, unknown> | null;
    closed_trade: Record<string, unknown> | null;
    signal: Record<string, unknown> | null;
    options_snapshot: Record<string, unknown> | null;
}

const LOSS_TAGS = [
    ['bad_signal', 'Bad Signal'],
    ['bad_timing', 'Bad Timing'],
    ['macro_event', 'Macro Event'],
    ['stop_loss', 'Stop Loss'],
    ['expiry_decay', 'Expiry Decay'],
    ['oversize', 'Oversize'],
    ['manual_error', 'Manual Error'],
    ['unknown', 'Unknown'],
] as const;

const FillDrilldownModal = ({ trade, onClose }: { trade: Trade; onClose: () => void }) => {
    const [detail, setDetail] = useState<FillDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [lossTag, setLossTag] = useState('');
    const [lossNotes, setLossNotes] = useState('');
    const [tagSaving, setTagSaving] = useState(false);
    const [tagSaved, setTagSaved] = useState(false);

    useEffect(() => {
        if (!trade.id && !trade.signal_id) { setLoading(false); return; }
        const id = trade.id || trade.signal_id;
        fetch(`/api/fills/detail?id=${id}`)
            .then(r => r.json())
            .then(d => { setDetail(d); setLoading(false); })
            .catch(() => setLoading(false));
    }, [trade]);

    const ct = detail?.closed_trade as Record<string, unknown> | null;
    const sig = detail?.signal as Record<string, unknown> | null;
    const snap = detail?.options_snapshot as Record<string, unknown> | null;

    useEffect(() => {
        if (ct?.loss_tag) setLossTag(ct.loss_tag as string);
        if (ct?.loss_notes) setLossNotes(ct.loss_notes as string);
    }, [ct?.loss_tag, ct?.loss_notes]);

    const saveTag = async () => {
        const id = trade.id || (ct?.id as string);
        if (!id || !lossTag) return;
        setTagSaving(true);
        try {
            const r = await fetch('/api/fills/tag', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, tag: lossTag, notes: lossNotes }),
            });
            if (r.ok) setTagSaved(true);
        } catch { /* ignore */ }
        setTagSaving(false);
    };

    const pnl = (trade.pnl ?? (ct?.pnl as number) ?? 0);
    const pnlPos = pnl >= 0;

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card fill-drill" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <span className="modal-title">
                        📋 {trade.ticker} {trade.option_type || ''} {trade.strike ? `$${trade.strike}` : ''} — Trade Detail
                    </span>
                    <button className="modal-close" onClick={onClose} aria-label="Close trade detail">✕</button>
                </div>

                {loading ? (
                    <div className="modal-loading">Loading detail…</div>
                ) : (
                    <div className="fill-drill-body">
                        {/* P&L Summary */}
                        <div className={`fill-pnl-banner ${pnlPos ? 'win' : 'loss'}`}>
                            <span className="fill-pnl-label">{pnlPos ? '✓ WIN' : '✗ LOSS'}</span>
                            <span className="fill-pnl-value">{pnlPos ? '+' : ''}{formatUSD(pnl)}</span>
                            {trade.pnl_pct !== undefined && (
                                <span className="fill-pnl-pct">({trade.pnl_pct?.toFixed(1)}%)</span>
                            )}
                        </div>

                        {/* Leg-by-leg */}
                        <div className="fill-section">
                            <div className="fill-section-title">Leg Detail</div>
                            <div className="fill-grid">
                                <div className="fill-row"><span>Entry Price</span><span>{trade.entry_price ? formatUSD(trade.entry_price) : '—'}</span></div>
                                <div className="fill-row"><span>Exit Price</span><span>{trade.exit_price ? formatUSD(trade.exit_price) : '—'}</span></div>
                                <div className="fill-row"><span>Quantity</span><span>{trade.quantity ?? ct?.qty as number ?? '—'} contracts</span></div>
                                <div className="fill-row"><span>Cost Basis</span><span>{formatUSD(trade.cost)}</span></div>
                                {trade.exit_reason && <div className="fill-row"><span>Exit Reason</span><span className="fill-exit-reason">{trade.exit_reason}</span></div>}
                            </div>
                        </div>

                        {/* Signal provenance */}
                        {sig && (
                            <div className="fill-section">
                                <div className="fill-section-title">Signal Provenance</div>
                                <div className="fill-grid">
                                    <div className="fill-row"><span>Model</span><span>{sig.model_id as string || '—'}</span></div>
                                    <div className="fill-row"><span>Direction</span><span>{sig.direction as string || '—'}</span></div>
                                    <div className="fill-row"><span>Confidence</span><span>{trade.confidence_at_entry !== undefined ? `${((trade.confidence_at_entry) * 100).toFixed(0)}%` : sig.confidence !== undefined ? `${((sig.confidence as number) * 100).toFixed(0)}%` : '—'}</span></div>
                                    <div className="fill-row"><span>Kelly Wager</span><span>{sig.kelly_wager_pct !== undefined ? `${((sig.kelly_wager_pct as number) * 100).toFixed(1)}%` : '—'}</span></div>
                                    <div className="fill-row"><span>Spot at Entry</span><span>{sig.underlying_price !== undefined ? formatUSD(sig.underlying_price as number) : '—'}</span></div>
                                    <div className="fill-row"><span>Price Source</span><span>{sig.price_source as string || '—'}</span></div>
                                </div>
                            </div>
                        )}

                        {/* Catalyst */}
                        {(trade.catalyst || (ct?.catalyst as string)) && (
                            <div className="fill-section">
                                <div className="fill-section-title">Catalyst</div>
                                <div className="fill-catalyst">{trade.catalyst || ct?.catalyst as string}</div>
                            </div>
                        )}

                        {/* Options snapshot at entry */}
                        {snap && (
                            <div className="fill-section">
                                <div className="fill-section-title">Options Chain at Entry</div>
                                <div className="fill-grid">
                                    <div className="fill-row"><span>IV</span><span>{snap.iv !== undefined ? `${((snap.iv as number) * 100).toFixed(1)}%` : '—'}</span></div>
                                    <div className="fill-row"><span>Bid/Ask</span><span>{snap.bid !== undefined ? `${formatUSD(snap.bid as number)} / ${formatUSD(snap.ask as number)}` : '—'}</span></div>
                                    <div className="fill-row"><span>OI</span><span>{snap.oi !== undefined ? (snap.oi as number).toLocaleString() : '—'}</span></div>
                                    <div className="fill-row"><span>Delta</span><span>{snap.delta !== undefined ? (snap.delta as number).toFixed(3) : '—'}</span></div>
                                </div>
                            </div>
                        )}

                        {/* Loss reconstruction + tagging (only for losses) */}
                        {!pnlPos && (
                            <>
                                <div className="fill-section loss-recon">
                                    <div className="fill-section-title">⚠ Loss Reconstruction</div>
                                    <div className="fill-catalyst">
                                        {trade.exit_reason === 'stop_loss' && 'Stop-loss triggered — position moved against signal direction.'}
                                        {trade.exit_reason === 'expiry' && 'Option expired worthless — signal did not resolve before expiration.'}
                                        {trade.exit_reason === 'manual' && 'Manually closed — check system log for operator notes.'}
                                        {!trade.exit_reason && 'No exit reason recorded — check fills log for details.'}
                                    </div>
                                </div>
                                <div className="fill-section loss-tag-section">
                                    <div className="fill-section-title">🏷 Tag This Loss</div>
                                    <div className="loss-tag-form">
                                        <select
                                            className="loss-tag-select"
                                            value={lossTag}
                                            onChange={e => { setLossTag(e.target.value); setTagSaved(false); }}
                                        >
                                            <option value="">— select reason —</option>
                                            {LOSS_TAGS.map(([val, label]) => (
                                                <option key={val} value={val}>{label}</option>
                                            ))}
                                        </select>
                                        <textarea
                                            className="loss-notes-input"
                                            placeholder="Notes (optional)"
                                            value={lossNotes}
                                            onChange={e => { setLossNotes(e.target.value); setTagSaved(false); }}
                                            rows={2}
                                        />
                                        <button
                                            className={`loss-tag-save-btn ${tagSaved ? 'saved' : ''}`}
                                            onClick={saveTag}
                                            disabled={tagSaving || !lossTag}
                                        >
                                            {tagSaved ? '✓ Saved' : tagSaving ? 'Saving…' : 'Save Tag'}
                                        </button>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

// ============================================================
//  Loss Detail Modal
// ============================================================
const EXIT_EXPLANATIONS: Record<string, string> = {
    stop_loss:    'Price moved against the position, triggering stop-loss protection.',
    expiry_decay: 'Option lost value from time decay approaching expiration.',
    take_profit:  'Profit target was reached and position was closed.',
    manual:       'Manually closed by operator — check system log for notes.',
    expiry:       'Option expired worthless — signal did not resolve before expiration.',
};

const LossDetailModal = ({
    trade,
    onClose,
}: { trade: LosingTrade | Trade; onClose: () => void }) => {
    const [lossTag, setLossTag] = useState((trade as LosingTrade).loss_tag || '');
    const [lossNotes, setLossNotes] = useState((trade as LosingTrade).loss_notes || '');
    const [tagSaving, setTagSaving] = useState(false);
    const [tagSaved, setTagSaved] = useState(false);

    const pnl = (trade as LosingTrade).pnl ?? (trade as Trade).pnl ?? 0;
    const isWin = pnl > 0;
    const pnlPct = (trade as Trade).pnl_pct;
    const entryPrice = (trade as LosingTrade).entry_price ?? (trade as Trade).entry_price;
    const exitPrice  = (trade as LosingTrade).exit_price  ?? (trade as Trade).exit_price;
    const entryTs    = (trade as LosingTrade).entry_ts    ?? (trade as Trade).time;
    const exitTs     = (trade as LosingTrade).exit_ts;
    const qty        = (trade as LosingTrade).qty         ?? (trade as Trade).quantity ?? 0;
    const ticker     = trade.ticker;
    const optType    = (trade as LosingTrade).option_type ?? (trade as Trade).option_type ?? '';
    const strike     = (trade as LosingTrade).strike      ?? (trade as Trade).strike;
    const expiry     = (trade as LosingTrade).expiry      ?? (trade as Trade).expiration_date;
    const modelId    = (trade as LosingTrade).model_id    ?? (trade as Trade).model_id ?? '—';
    const conf       = (trade as LosingTrade).confidence_at_entry ?? (trade as Trade).confidence_at_entry;
    const catalyst   = (trade as LosingTrade).catalyst    ?? (trade as Trade).catalyst ?? '';
    const exitReason = (trade as LosingTrade).exit_reason ?? (trade as Trade).exit_reason ?? '';
    const tradeId    = (trade as LosingTrade).id          ?? (trade as Trade).id ?? '';

    // Duration
    let durationStr = '—';
    if (entryTs && exitTs) {
        const ms = new Date(exitTs).getTime() - new Date(entryTs).getTime();
        const mins = Math.round(ms / 60000);
        durationStr = mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
    }

    const saveTag = async () => {
        if (!tradeId || !lossTag) return;
        setTagSaving(true);
        try {
            const r = await fetch('/api/fills/tag', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: tradeId, tag: lossTag, notes: lossNotes }),
            });
            if (r.ok) setTagSaved(true);
        } catch { /* ignore */ }
        setTagSaving(false);
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card loss-detail-modal" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <span className="modal-title">
                        📉 {ticker} {optType && `${optType} `}{strike ? `$${strike}` : ''}{expiry ? ` · ${expiry}` : ''} — Loss Analysis
                    </span>
                    <button className="modal-close" onClick={onClose} aria-label="Close loss analysis">✕</button>
                </div>
                <div className="loss-detail-body">
                    {/* P&L Banner */}
                    <div className={`fill-pnl-banner ${isWin ? 'win' : 'loss'}`}>
                        <span className="fill-pnl-label">{isWin ? '✓ WIN' : '✗ LOSS'}</span>
                        <span className="fill-pnl-value">{isWin ? '+' : ''}{formatUSD(pnl)}</span>
                        {pnlPct !== undefined && <span className="fill-pnl-pct">({pnlPct.toFixed(1)}%)</span>}
                    </div>

                    {/* Section 1: Trade Timeline */}
                    <div className="loss-analysis-section">
                        <div className="loss-analysis-title">Trade Timeline</div>
                        <div className="loss-timeline">
                            <div className="loss-timeline-entry">
                                <div className="loss-timeline-label">Entry</div>
                                <div className="loss-timeline-price">{entryPrice ? formatUSD(entryPrice) : '—'}</div>
                                <div className="loss-timeline-date">{entryTs ? new Date(entryTs).toLocaleString() : '—'}</div>
                                <div className="loss-timeline-date">{qty} contract{qty !== 1 ? 's' : ''}</div>
                            </div>
                            <div className="loss-timeline-arrow">→</div>
                            <div className="loss-timeline-exit">
                                <div className="loss-timeline-label">Exit</div>
                                <div className="loss-timeline-price">{exitPrice ? formatUSD(exitPrice) : '—'}</div>
                                <div className="loss-timeline-date">{exitTs ? new Date(exitTs).toLocaleString() : '—'}</div>
                            </div>
                        </div>
                        <div className="loss-duration">Duration: {durationStr} · Cost basis: {formatUSD((entryPrice ?? 0) * qty * 100)}</div>
                    </div>

                    {/* Section 2: Signal Analysis */}
                    <div className="loss-analysis-section">
                        <div className="loss-analysis-title">Signal Analysis</div>
                        <div className="fill-grid">
                            <div className="fill-row"><span>Model</span><span className="sc-model-id">{modelId}</span></div>
                            {conf !== undefined && conf !== null && (
                                <div className="fill-row">
                                    <span>Confidence at Entry</span>
                                    <span>{(conf * 100).toFixed(0)}%</span>
                                </div>
                            )}
                        </div>
                        {conf !== undefined && conf !== null && (
                            <div className="loss-confidence-bar">
                                <div className="loss-confidence-fill" style={{
                                    width: `${conf * 100}%`,
                                    background: conf > 0.7 ? '#3fb950' : conf > 0.5 ? '#f0883e' : '#f85149',
                                }} />
                            </div>
                        )}
                        {catalyst && (
                            <div className="fill-catalyst" style={{ marginTop: '8px' }}>
                                <span style={{ fontSize: '10px', color: '#8b949e', display: 'block', marginBottom: '4px' }}>CATALYST</span>
                                {catalyst}
                            </div>
                        )}
                    </div>

                    {/* Section 3: Exit Analysis */}
                    {exitReason && (
                        <div className="loss-analysis-section">
                            <div className="loss-analysis-title">Exit Analysis</div>
                            <div className="fill-row" style={{ marginBottom: '8px' }}>
                                <span>Exit Reason</span>
                                <span className="fill-exit-reason">{exitReason}</span>
                            </div>
                            <div className="loss-exit-explanation">
                                {EXIT_EXPLANATIONS[exitReason] || 'No explanation recorded for this exit type.'}
                            </div>
                        </div>
                    )}

                    {/* Section 4: Lessons Learned */}
                    <div className="loss-analysis-section loss-lessons">
                        <div className="loss-analysis-title">Lessons Learned</div>
                        <div className="loss-tag-form">
                            <select
                                className="loss-tag-select"
                                value={lossTag}
                                onChange={e => { setLossTag(e.target.value); setTagSaved(false); }}
                            >
                                <option value="">— select reason —</option>
                                {LOSS_TAGS.map(([val, label]) => (
                                    <option key={val} value={val}>{label}</option>
                                ))}
                            </select>
                            <textarea
                                className="loss-notes-input"
                                placeholder="Notes (optional)"
                                value={lossNotes}
                                onChange={e => { setLossNotes(e.target.value); setTagSaved(false); }}
                                rows={2}
                            />
                            <button
                                className={`loss-tag-save-btn ${tagSaved ? 'saved' : ''}`}
                                onClick={saveTag}
                                disabled={tagSaving || !lossTag || !tradeId}
                            >
                                {tagSaved ? '✓ Tagged!' : tagSaving ? 'Saving…' : 'Save Tag'}
                            </button>
                            {tagSaved && <span className="loss-tag-saved">Analysis saved to database</span>}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

// ============================================================
//  Loss Summary Mini-Widget
// ============================================================
const LossSummaryWidget = ({ summary }: { summary: LossSummary | null }) => {
    const [selectedLoss, setSelectedLoss] = useState<LosingTrade | null>(null);
    const [losingTrades, setLosingTrades] = useState<LosingTrade[]>([]);

    useEffect(() => {
        if (!summary || summary.total_losses === 0) return;
        fetch('/api/losing_trades')
            .then(r => r.ok ? r.json() : [])
            .then(d => setLosingTrades(Array.isArray(d) ? d : []))
            .catch(() => {});
    }, [summary?.total_losses]);

    if (!summary || summary.total_losses === 0) return null;
    const tags = Object.entries(summary.loss_tags).sort((a, b) => b[1] - a[1]);
    const total = summary.total_losses;
    return (
        <>
            {selectedLoss && <LossDetailModal trade={selectedLoss} onClose={() => setSelectedLoss(null)} />}
            <div className="loss-summary-widget">
                <div className="loss-summary-title">Loss Breakdown</div>
                <div className="loss-summary-stats">
                    <span className="loss-stat">{summary.total_losses} losses</span>
                    <span className="loss-stat red">{formatUSD(summary.total_loss_amount)}</span>
                    <span className="loss-stat">avg {formatUSD(summary.avg_loss)}</span>
                </div>
                <div className="loss-tag-bars">
                    {tags.slice(0, 5).map(([tag, cnt]) => {
                        const lossTagDescriptions: Record<string, string> = {
                            exit_timing: 'Exit timing was off — position was correct but closed too early or too late',
                            stop_loss: 'Hit stop loss — risk management worked as designed',
                            macro_event: 'External event overrode the signal (Fed, earnings, geopolitical)',
                            expiry_decay: 'Option expired worthless or lost value from time decay',
                            spread_slippage: 'Fill prices differed significantly from expected',
                        };
                        return (
                            <div key={tag} className="loss-tag-bar-row">
                                <Tooltip text={lossTagDescriptions[tag] ?? tag}>
                                    <span className="loss-tag-label">{tag.replace('_', ' ')}</span>
                                </Tooltip>
                                <div className="loss-tag-bar-bg">
                                    <div className="loss-tag-bar-fill" style={{ width: `${(cnt / total) * 100}%` }} />
                                </div>
                                <span className="loss-tag-count">{cnt}</span>
                            </div>
                        );
                    })}
                </div>
                {losingTrades.length > 0 && (
                    <div className="loss-mini-list">
                        {losingTrades.slice(0, 5).map((lt, i) => (
                            <div
                                key={i}
                                className="loss-mini-row"
                                onClick={() => setSelectedLoss(lt)}
                                role="button"
                                tabIndex={0}
                                aria-label={`${lt.ticker}${lt.strike ? ` $${lt.strike}` : ''} — ${formatUSD(lt.pnl)} loss. Click to analyze.`}
                                title="Click to open loss analysis"
                                onKeyDown={e => e.key === 'Enter' && setSelectedLoss(lt)}
                            >
                                <span style={{ color: '#8b949e', fontSize: '10px' }}>
                                    {lt.entry_ts ? new Date(lt.entry_ts).toLocaleDateString() : '—'}
                                </span>
                                <span>{lt.ticker}{lt.strike ? ` $${lt.strike}` : ''}</span>
                                <span className="loss-mini-pnl">{formatUSD(lt.pnl)}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </>
    );
};

// ============================================================
//  Model Scorecard Panel
// ============================================================
const ModelScorecardPanel = ({ scorecard, isLoading }: { scorecard: ModelScorecard[]; isLoading?: boolean }) => {
    if (isLoading) {
        return (
            <div aria-busy="true" aria-label="Loading scorecard…">
                <SkeletonTable rows={3} cols={8} />
            </div>
        );
    }
    if (scorecard.length === 0) {
        return <div className="scorecard-empty">No closed trades recorded yet.</div>;
    }
    return (
        <div className="scorecard-table-wrap">
            <table className="scorecard-table">
                <thead>
                    <tr>
                        <th scope="col">Model</th>
                        <th scope="col"><Tooltip text="Total number of closed trades attributed to this model"><span data-tooltip="Total number of closed trades attributed to this model">Trades</span></Tooltip></th>
                        <th scope="col"><Tooltip text="Percentage of closed trades that were profitable"><span data-tooltip="Percentage of closed trades that were profitable">Win%</span></Tooltip></th>
                        <th scope="col"><Tooltip text="Average P&L per closed trade in dollars"><span data-tooltip="Average P&L per closed trade in dollars">Avg P&L</span></Tooltip></th>
                        <th scope="col"><Tooltip text="Sum of all realized P&L from this model's trades"><span data-tooltip="Sum of all realized P&L from this model's trades">Total P&L</span></Tooltip></th>
                        <th scope="col"><Tooltip text="Annualized Sharpe ratio — risk-adjusted return. >1 is good, >2 is excellent."><span data-tooltip="Annualized Sharpe ratio — risk-adjusted return. >1 is good, >2 is excellent.">Sharpe</span></Tooltip></th>
                        <th scope="col"><Tooltip text="Win rate on trades where confidence was ≥80%. Tests if high-conviction signals outperform."><span data-tooltip="Win rate on trades where confidence was ≥80%. Tests if high-conviction signals outperform.">Hi-Conf%</span></Tooltip></th>
                        <th scope="col"><Tooltip text="Most common reason this model's trades were losers"><span data-tooltip="Most common reason this model's trades were losers">Top Loss Tag</span></Tooltip></th>
                    </tr>
                </thead>
                <tbody>
                    {scorecard.map(m => {
                        const rowCls = m.win_rate > 0.6 ? 'sc-row-green' : m.win_rate < 0.4 ? 'sc-row-red' : '';
                        const hcWR = m.confidence_calibration?.high_conf_win_rate;
                        const topTag = m.common_loss_tags?.[0];
                        return (
                            <tr key={m.model_id} className={`sc-row ${rowCls}`}>
                                <td className="sc-model-id">{m.model_id}</td>
                                <td>{m.trade_count}</td>
                                <td className={m.win_rate > 0.6 ? 'sc-green' : m.win_rate < 0.4 ? 'sc-red' : ''}>
                                    {(m.win_rate * 100).toFixed(0)}%
                                </td>
                                <td style={{ color: m.avg_pnl >= 0 ? '#3fb950' : '#f85149' }}>
                                    {formatUSD(m.avg_pnl)}
                                </td>
                                <td style={{ color: m.total_pnl >= 0 ? '#3fb950' : '#f85149' }}>
                                    {formatUSD(m.total_pnl)}
                                </td>
                                <td className={m.sharpe > 1 ? 'sc-green' : m.sharpe < 0 ? 'sc-red' : ''}>
                                    {m.sharpe.toFixed(2)}
                                </td>
                                <td>
                                    {hcWR !== null && hcWR !== undefined
                                        ? <span className={`sc-badge ${hcWR > 0.6 ? 'sc-badge-green' : hcWR < 0.4 ? 'sc-badge-red' : 'sc-badge-gray'}`}>
                                            {(hcWR * 100).toFixed(0)}% ({m.confidence_calibration.high_conf_trade_count})
                                          </span>
                                        : '—'}
                                </td>
                                <td>
                                    {topTag
                                        ? <span className="sc-loss-tag">{topTag.tag.replace('_', ' ')} ×{topTag.count}</span>
                                        : '—'}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};

// ============================================================
//  Pre-Market Panel
// ============================================================
const PreMarketPanel = ({ intel, isLoading }: { intel: Intel | null; isLoading?: boolean }) => {
    const [drillTarget, setDrillTarget] = useState<string | null>(null);

    if (isLoading) {
        return (
            <div aria-busy="true" aria-label="Loading pre-market data…">
                <SkeletonCard rows={3} />
            </div>
        );
    }

    const pm = intel?.premarket;

    // Countdown to US market open/close
    const marketCountdown = (() => {
        const now = new Date();
        const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
        const h = et.getHours(), m = et.getMinutes();
        const totalMin = h * 60 + m;
        const isWeekend = et.getDay() === 0 || et.getDay() === 6;
        if (isWeekend) return 'Market closed — weekend';
        if (totalMin < 570) {
            const minsLeft = 570 - totalMin;
            return `Next US open: ${Math.floor(minsLeft / 60)}h ${minsLeft % 60}m`;
        }
        if (totalMin < 960) {
            const minsLeft = 960 - totalMin;
            return `Market open — closes in ${Math.floor(minsLeft / 60)}h ${minsLeft % 60}m`;
        }
        return 'Market closed — after hours';
    })();

    const biasColor = (bias: string) => {
        if (bias === 'BULLISH') return '#3fb950';
        if (bias === 'BEARISH') return '#f85149';
        return '#8b949e';
    };

    const changePctLabel = (pct: number | undefined) => {
        if (pct === undefined || pct === null) return '—';
        const sign = pct >= 0 ? '+' : '';
        return <span style={{ color: pct >= 0 ? '#3fb950' : '#f85149' }}>{sign}{pct.toFixed(2)}%</span>;
    };

    return (
        <div className="intel-panel" data-testid="premarket-panel" aria-label="Pre-Market Intelligence">
            {drillTarget && (
                <div className="modal-overlay" onClick={() => setDrillTarget(null)} role="dialog" aria-modal="true" aria-label="Pre-Market Drill-Down">
                    <div className="modal-card nav-drill" onClick={e => e.stopPropagation()}>
                        <div className="modal-header">
                            <span className="modal-title">📡 PRE-MARKET: {drillTarget}</span>
                            <button className="modal-close" onClick={() => setDrillTarget(null)} aria-label="Close">✕</button>
                        </div>
                        <div className="fill-drill-body">
                            {drillTarget === 'TSLA' && pm && (
                                <>
                                    <div className="fill-row">
                                        <span>Extended-hours change</span>
                                        <span>{changePctLabel(pm.tsla_premarket_change_pct)}</span>
                                    </div>
                                    <div className="fill-row">
                                        <span>Extended-hours volume</span>
                                        <span>{pm.tsla_premarket_volume > 0 ? pm.tsla_premarket_volume.toLocaleString() : 'No pre/post activity'}</span>
                                    </div>
                                    {pm.overnight_catalyst && (
                                        <div className="fill-row"><span>Catalyst</span><span>{pm.overnight_catalyst}</span></div>
                                    )}
                                    <div className="fill-row"><span>Source</span><span style={{fontSize:'11px'}}>yfinance TSLA prepost=True (1d)</span></div>
                                </>
                            )}
                            {drillTarget === 'Futures' && pm && (
                                <>
                                    <div className="fill-row"><span>ES (S&P 500)</span><span>{changePctLabel(pm.es_change_pct)}</span></div>
                                    <div className="fill-row"><span>NQ (Nasdaq 100)</span><span>{changePctLabel(pm.nq_change_pct)}</span></div>
                                    <div className="fill-row"><span>RTY</span><span style={{color:'#8b949e'}}>Not yet wired — see Phase A stash</span></div>
                                    <div className="fill-row"><span>Bias</span><span style={{color:biasColor(pm.futures_bias)}}>{pm.futures_bias}</span></div>
                                    <div className="fill-row"><span>Source</span><span style={{fontSize:'11px'}}>yfinance ES=F, NQ=F (2d history)</span></div>
                                </>
                            )}
                            {drillTarget === 'Europe' && pm && (
                                <>
                                    <div className="fill-row"><span>STOXX50E</span><span style={{color:biasColor(pm.europe_direction)}}>{pm.europe_direction}</span></div>
                                    <div className="fill-row"><span>DAX (^GDAXI)</span><span style={{color:'#8b949e'}}>Not yet wired — see Phase A stash</span></div>
                                    <div className="fill-row"><span>FTSE (^FTSE)</span><span style={{color:'#8b949e'}}>Not yet wired — see Phase A stash</span></div>
                                    <div className="fill-row"><span>Source</span><span style={{fontSize:'11px'}}>yfinance ^STOXX50E (2d history)</span></div>
                                </>
                            )}
                            {drillTarget === 'Composite Bias' && pm && (
                                <>
                                    <div className="fill-row"><span>ES change</span><span>{changePctLabel(pm.es_change_pct)} × 0.35</span></div>
                                    <div className="fill-row"><span>NQ change</span><span>{changePctLabel(pm.nq_change_pct)} × 0.35</span></div>
                                    <div className="fill-row"><span>Europe</span><span style={{color:biasColor(pm.europe_direction)}}>{pm.europe_direction} × 0.30</span></div>
                                    <div className="fill-row"><span>Result</span><span style={{color:biasColor(pm.futures_bias),fontWeight:700}}>{pm.futures_bias}</span></div>
                                </>
                            )}
                        </div>
                    </div>
                </div>
            )}

            <div className="intel-grid">
                {/* TSLA Pre/Post */}
                <div className="intel-card" role="region" aria-label="TSLA Extended Hours"
                     style={{cursor:'pointer'}} onClick={() => setDrillTarget('TSLA')}>
                    <Tooltip text="TSLA extended-hours price change vs prior close. Click for detail.">
                        <div className="intel-card-title">TSLA Pre/Post</div>
                    </Tooltip>
                    {pm ? (
                        <>
                            <div className="intel-row">
                                <span className="intel-label">Change</span>
                                <span>{changePctLabel(pm.tsla_premarket_change_pct)}</span>
                            </div>
                            <div className="intel-row">
                                <span className="intel-label">Volume</span>
                                <span style={{color:'#8b949e'}}>
                                    {pm.tsla_premarket_volume > 0
                                        ? pm.tsla_premarket_volume.toLocaleString()
                                        : 'No pre/post activity'}
                                </span>
                            </div>
                        </>
                    ) : (
                        <div style={{color:'#8b949e',fontSize:'12px'}}>No pre/post activity — awaiting data</div>
                    )}
                </div>

                {/* US Futures */}
                <div className="intel-card" role="region" aria-label="US Futures"
                     style={{cursor:'pointer'}} onClick={() => setDrillTarget('Futures')}>
                    <Tooltip text="ES and NQ futures change vs prior session. Click for detail.">
                        <div className="intel-card-title">US Futures</div>
                    </Tooltip>
                    {pm ? (
                        <>
                            <div className="intel-row">
                                <span className="intel-label">ES (S&P)</span>
                                <span>{changePctLabel(pm.es_change_pct)}</span>
                            </div>
                            <div className="intel-row">
                                <span className="intel-label">NQ (NDX)</span>
                                <span>{changePctLabel(pm.nq_change_pct)}</span>
                            </div>
                            <div className="intel-row">
                                <span className="intel-label">RTY</span>
                                <span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span>
                            </div>
                        </>
                    ) : (
                        <div style={{color:'#8b949e',fontSize:'12px'}}>Not yet wired — see Phase A stash</div>
                    )}
                </div>

                {/* European Indices */}
                <div className="intel-card" role="region" aria-label="European Indices"
                     style={{cursor:'pointer'}} onClick={() => setDrillTarget('Europe')}>
                    <Tooltip text="European equity session direction. STOXX50E wired; DAX/FTSE planned Phase A.">
                        <div className="intel-card-title">Europe</div>
                    </Tooltip>
                    {pm ? (
                        <>
                            <div className="intel-row">
                                <span className="intel-label">STOXX50E</span>
                                <span style={{color:biasColor(pm.europe_direction)}}>{pm.europe_direction}</span>
                            </div>
                            <div className="intel-row">
                                <span className="intel-label">DAX</span>
                                <span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span>
                            </div>
                            <div className="intel-row">
                                <span className="intel-label">FTSE</span>
                                <span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span>
                            </div>
                        </>
                    ) : (
                        <div style={{color:'#8b949e',fontSize:'12px'}}>Not yet wired — see Phase A stash</div>
                    )}
                </div>

                {/* Asian Indices — Phase A stash */}
                <div className="intel-card" role="region" aria-label="Asian Indices">
                    <Tooltip text="Asian equity session indices — planned in Phase A stash. Not yet wired.">
                        <div className="intel-card-title">Asia</div>
                    </Tooltip>
                    <div className="intel-row"><span className="intel-label">Nikkei</span><span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span></div>
                    <div className="intel-row"><span className="intel-label">Hang Seng</span><span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span></div>
                    <div className="intel-row"><span className="intel-label">Shanghai</span><span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span></div>
                </div>

                {/* FX Risk Barometer — Phase A stash */}
                <div className="intel-card" role="region" aria-label="FX Risk Barometer">
                    <Tooltip text="USDJPY carry, EURUSD, DXY — risk-on/risk-off signal. Planned Phase A.">
                        <div className="intel-card-title">FX Barometer</div>
                    </Tooltip>
                    <div className="intel-row"><span className="intel-label">USDJPY</span><span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span></div>
                    <div className="intel-row"><span className="intel-label">DXY</span><span style={{color:'#d29922',fontSize:'11px'}}>Not yet wired</span></div>
                </div>

                {/* Composite Bias */}
                <div className="intel-card" role="region" aria-label="Composite Pre-Market Bias"
                     style={{cursor:'pointer'}} onClick={() => pm && setDrillTarget('Composite Bias')}>
                    <Tooltip text="Composite pre-market bias computed from futures + Europe. Click for breakdown.">
                        <div className="intel-card-title">Composite Bias</div>
                    </Tooltip>
                    {pm ? (
                        <>
                            <div className="intel-row">
                                <span className="intel-label">Bias</span>
                                <span style={{color:biasColor(pm.futures_bias),fontWeight:700,fontSize:'14px'}}>{pm.futures_bias}</span>
                            </div>
                            <div className="intel-row">
                                <span className="intel-label">Signal window</span>
                                <span style={{color: pm.is_signal_window ? '#3fb950' : '#8b949e'}}>
                                    {pm.is_signal_window ? 'ACTIVE' : 'INACTIVE'}
                                </span>
                            </div>
                        </>
                    ) : (
                        <div style={{color:'#8b949e',fontSize:'12px'}}>Awaiting intel data</div>
                    )}
                    <div className="intel-stale" data-testid="market-countdown">{marketCountdown}</div>
                </div>
            </div>
        </div>
    );
};

// ============================================================
//  Intel Panel
// ============================================================
const IntelPanel = ({ intel, isLoading }: { intel: Intel | null; isLoading?: boolean }) => {
    if (isLoading) {
        return (
            <div aria-busy="true" aria-label="Loading market intelligence…">
                <SkeletonCard rows={4} />
            </div>
        );
    }
    if (!intel || !intel.vix) {
        return <div className="scorecard-empty">No market intelligence data available.</div>;
    }
    const news = intel.news ?? { headlines: [], sentiment_score: 0, headline_count: 0, bull_hits: 0, bear_hits: 0 };
    const vix = intel.vix ?? { vix_level: 0, vix_status: 'NORMAL' };
    const spy = intel.spy ?? { spy_price: 0, spy_change_pct: 0 };
    const earnings = intel.earnings ?? { next_earnings_date: '', days_until_earnings: 0 };
    const options_flow = intel.options_flow ?? { pc_ratio: 1, pc_signal: 'NEUTRAL', total_call_oi: 0, total_put_oi: 0 };
    const staleMs = Date.now() - intel.fetch_timestamp * 1000;
    const staleSec = Math.floor(staleMs / 1000);
    const staleLabel = staleSec < 60 ? `${staleSec}s ago` : `${Math.floor(staleSec / 60)}m ago`;

    const vixCls = vix.vix_status === 'LOW' ? 'low'
        : vix.vix_status === 'HIGH' ? 'high'
        : vix.vix_status === 'EXTREME' ? 'extreme'
        : 'normal';

    const pcCls = options_flow.pc_signal === 'BULLISH' ? 'bullish'
        : options_flow.pc_signal === 'BEARISH' ? 'bearish'
        : 'neutral';

    // Sentiment bar: map -1..1 to 0..100%
    const sentPct = Math.round(((news.sentiment_score + 1) / 2) * 100);
    const sentColor = news.sentiment_score > 0.3 ? '#3fb950'
        : news.sentiment_score < -0.3 ? '#f85149'
        : '#f0883e';

    const totalOI = options_flow.total_call_oi + options_flow.total_put_oi;
    const callPct = totalOI > 0 ? Math.round((options_flow.total_call_oi / totalOI) * 100) : 50;
    const putPct = 100 - callPct;

    const earningsCls = earnings.days_until_earnings !== null && earnings.days_until_earnings <= 2 ? 'earnings-urgent'
        : earnings.days_until_earnings !== null && earnings.days_until_earnings <= 7 ? 'earnings-soon'
        : 'earnings-ok';

    const spyColor = spy.spy_change_pct >= 0 ? '#3fb950' : '#f85149';

    return (
        <div className="intel-panel">
            <div className="intel-grid">
                {/* Card 1: Market Pulse */}
                <div className="intel-card" role="region" aria-label="Market Pulse — VIX and SPY">
                    <Tooltip text="VIX = market fear gauge. SPY = S&P 500 ETF trend. Both influence TSLA signal confidence.">
                        <div className="intel-card-title" data-tooltip="VIX = market fear gauge. SPY = S&P 500 ETF trend. Both influence TSLA signal confidence.">Market Pulse</div>
                    </Tooltip>
                    <div className="intel-row">
                        <span className="intel-label">VIX</span>
                        <span className={`vix-badge ${vixCls}`}>
                            {vix.vix_level !== null ? vix.vix_level.toFixed(1) : '—'} {vix.vix_status}
                        </span>
                    </div>
                    <div className="intel-row">
                        <span className="intel-label">SPY</span>
                        <span style={{ color: spyColor }}>
                            {spy.spy_price !== null ? `$${spy.spy_price.toFixed(2)}` : '—'}
                            {' '}
                            {spy.spy_change_pct !== 0 ? `(${spy.spy_change_pct >= 0 ? '+' : ''}${spy.spy_change_pct.toFixed(2)}%)` : ''}
                        </span>
                    </div>
                    <div className="intel-stale">Updated {staleLabel}</div>
                </div>

                {/* Card 2: News Sentiment */}
                <div className="intel-card" role="region" aria-label="News Sentiment">
                    <Tooltip text="NLP score from recent TSLA headlines + Musk mentions. Positive = bullish sentiment, negative = bearish.">
                        <div className="intel-card-title" data-tooltip="NLP score from recent TSLA headlines + Musk mentions. Positive = bullish sentiment, negative = bearish.">
                            News Sentiment
                            <span className="intel-count-badge" aria-label={`${news.headline_count} headlines`}>{news.headline_count}</span>
                        </div>
                    </Tooltip>
                    <div className="sentiment-bar-wrap">
                        <div className="sentiment-bar">
                            <div className="sentiment-bar-fill" style={{ width: `${sentPct}%`, background: sentColor }} />
                            <div className="sentiment-marker" style={{ left: `${sentPct}%` }} />
                        </div>
                        <div className="sentiment-labels">
                            <span>Bearish</span>
                            <span style={{ color: sentColor, fontWeight: 600 }}>
                                {news.sentiment_score >= 0 ? '+' : ''}{news.sentiment_score.toFixed(2)}
                            </span>
                            <span>Bullish</span>
                        </div>
                    </div>
                    <div className="intel-headlines">
                        {news.headlines.slice(0, 3).map((h, i) => (
                            <div key={i} className="intel-headline" title={h}>
                                {h.length > 60 ? h.slice(0, 57) + '…' : h}
                            </div>
                        ))}
                        {news.headlines.length === 0 && <div className="intel-no-news">No headlines available</div>}
                    </div>
                </div>

                {/* Card 3: Options Flow */}
                <div className="intel-card" role="region" aria-label="Options Flow — put/call ratio">
                    <Tooltip text="Put/Call ratio from the nearest expiry chain. P/C > 1.3 = bearish positioning. P/C < 0.7 = bullish. OI bars show relative call vs put open interest.">
                        <div className="intel-card-title" data-tooltip="Put/Call ratio from the nearest expiry chain. P/C > 1.3 = bearish positioning. P/C < 0.7 = bullish. OI bars show relative call vs put open interest.">Options Flow</div>
                    </Tooltip>
                    <div className="intel-row">
                        <span className="intel-label">P/C Ratio</span>
                        <span>
                            <span className="intel-pcval">{options_flow.pc_ratio.toFixed(2)}</span>
                            {' '}
                            <span className={`pc-badge ${pcCls}`}>{options_flow.pc_signal}</span>
                        </span>
                    </div>
                    <div className="intel-oi-bars">
                        <div className="intel-oi-row">
                            <span className="intel-oi-label">CALLS</span>
                            <div className="intel-oi-bar-bg">
                                <div className="intel-oi-bar-call" style={{ width: `${callPct}%` }} />
                            </div>
                            <span className="intel-oi-num">{options_flow.total_call_oi > 0 ? (options_flow.total_call_oi / 1000).toFixed(0) + 'k' : '—'}</span>
                        </div>
                        <div className="intel-oi-row">
                            <span className="intel-oi-label">PUTS</span>
                            <div className="intel-oi-bar-bg">
                                <div className="intel-oi-bar-put" style={{ width: `${putPct}%` }} />
                            </div>
                            <span className="intel-oi-num">{options_flow.total_put_oi > 0 ? (options_flow.total_put_oi / 1000).toFixed(0) + 'k' : '—'}</span>
                        </div>
                    </div>
                </div>

                {/* Card 4: Earnings Watch */}
                <div className="intel-card" role="region" aria-label="Earnings Watch">
                    <Tooltip text="Next TSLA earnings date. Within 2 days = reduced signal confidence (±50% haircut) due to binary event risk.">
                        <div className="intel-card-title" data-tooltip="Next TSLA earnings date. Within 2 days = reduced signal confidence (±50% haircut) due to binary event risk.">Earnings Watch</div>
                    </Tooltip>
                    {earnings.next_earnings_date ? (
                        <>
                            <div className="intel-row">
                                <span className="intel-label">Next</span>
                                <span>{earnings.next_earnings_date}</span>
                            </div>
                            <div className={`intel-row earnings-days ${earningsCls}`}>
                                <span className="intel-label">Days</span>
                                <span>
                                    {earnings.days_until_earnings !== null ? earnings.days_until_earnings : '—'}
                                    {earnings.days_until_earnings !== null && earnings.days_until_earnings <= 0 && ' (TODAY)'}
                                </span>
                            </div>
                            {earnings.days_until_earnings !== null && earnings.days_until_earnings <= 2 && (
                                <div className="earnings-warning">⚠ EARNINGS IMMINENT — reduced conviction</div>
                            )}
                        </>
                    ) : (
                        <div className="intel-no-news">Earnings date unavailable</div>
                    )}
                </div>
            </div>
        </div>
    );
};

const ExecutionLog = ({ trades, fromLog, brokerStatus, lossSummary, simMode }: { trades: Trade[]; fromLog?: boolean; brokerStatus: BrokerStatus | null; lossSummary: LossSummary | null; simMode?: string }) => {
    const [drillTrade, setDrillTrade] = useState<Trade | null>(null);
    const [analyzeTrade, setAnalyzeTrade] = useState<Trade | null>(null);
    const closingTrades = trades.filter(t => t.action === 'SELL' && t.pnl !== 0);
    const winRate = closingTrades.length > 0
        ? ((closingTrades.filter(t => t.pnl > 0).length / closingTrades.length) * 100).toFixed(0)
        : '—';
    const totalPnl = trades.reduce((sum, t) => sum + (t.pnl ?? 0), 0);

    const today = new Date().toDateString();
    const todayTrades = trades.filter(t => new Date(t.time).toDateString() === today);

    return (
        <div className="dash-col card" role="region" aria-label="Execution Log — all executed trades">
            <div className="section-header">
                <Tooltip text="All executed orders — buys and sells with realized P&L">
                    <span className="section-title" data-tooltip="All executed orders — buys and sells with realized P&L">📋 {fromLog ? 'EXECUTION LOG (from system log)' : 'EXECUTION LOG'}</span>
                </Tooltip>
                <div className="section-meta">
                    {brokerStatus && (
                        <span className={`inline-badge mode-${brokerStatus.mode}`}>{brokerStatus.mode.toUpperCase()}</span>
                    )}
                    <span className="inline-badge">{trades.length}</span>
                </div>
            </div>
            <div className="exec-log-stats">
                <span className="exec-stat">
                    <Tooltip text="Total trades executed">Trades: <span>{trades.length}</span></Tooltip>
                </span>
                <span className="exec-stat">
                    <Tooltip text="Win rate on closing trades (SELL with P&L)">Win%: <span>{winRate}%</span></Tooltip>
                </span>
                <span className="exec-stat">
                    <Tooltip text="Sum of all realized P&L">
                        P&L: <span style={{ color: totalPnl >= 0 ? '#3fb950' : '#f85149' }}>{formatUSD(totalPnl)}</span>
                    </Tooltip>
                </span>
            </div>
            <div className="scroll-col">
                {trades.length === 0 ? (
                    <div className="empty-position-card">
                        <div className="empty-position-icon">📝</div>
                        <div>{simMode === 'paper' ? 'No IBKR fills yet — waiting for first execution.' : 'No trades executed yet.'}</div>
                        {simMode === 'paper' && (
                            <div style={{ fontSize: '0.72rem', marginTop: '0.35rem', color: '#58a6ff' }}>
                                Fills from IBKR paper account will appear here
                            </div>
                        )}
                    </div>
                ) : (
                    trades.map((t, i) => {
                        const isBuy = t.action === 'BUY';
                        const hasPnl = t.pnl !== 0 && t.pnl !== undefined;
                        const pnlPos = (t.pnl ?? 0) >= 0;
                        return (
                            <div
                                key={i}
                                className="trade-row"
                                onClick={() => setDrillTrade(t)}
                                style={{cursor:'pointer'}}
                                role="button"
                                tabIndex={0}
                                aria-label={`${t.action} ${t.ticker}${hasPnl ? ` — P&L ${formatUSD(t.pnl)}` : ''}. Click for trade detail.`}
                                title="Click to open trade drill-down"
                                onKeyDown={e => e.key === 'Enter' && setDrillTrade(t)}
                            >
                                <span className="trade-time">
                                    {new Date(t.time).toLocaleTimeString('en-US', {
                                        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
                                    })}
                                </span>
                                <span className={`trade-action ${isBuy ? 'buy' : 'sell'}`}>
                                    {t.action}
                                </span>
                                {t.entry_price && t.exit_price && (
                                    <span style={{ fontSize: '10px', color: '#8b949e', marginLeft: '4px' }}>
                                        {formatUSD(t.entry_price)} → {formatUSD(t.exit_price)}
                                    </span>
                                )}
                                <span className="trade-ticker" title={t.ticker}>{t.ticker}</span>
                                <span className="trade-cost">
                                    <Tooltip text="Total transaction cost">{formatUSD(t.cost)}</Tooltip>
                                </span>
                                <span className={`trade-pnl ${hasPnl ? (pnlPos ? 'pos' : 'neg') : 'zero'}`}>
                                    {hasPnl ? `${pnlPos ? '+' : ''}${formatUSD(t.pnl)}` : '—'}
                                </span>
                                {hasPnl && !pnlPos && (
                                    <button
                                        className="analyze-btn"
                                        onClick={e => { e.stopPropagation(); setAnalyzeTrade(t); }}
                                        aria-label={`Analyze loss on ${t.ticker} — open loss reconstruction detail`}
                                        title="Analyze this losing trade — view loss reconstruction, exit reason, and tagging"
                                    >ANALYZE</button>
                                )}
                            </div>
                        );
                    })
                )}
            </div>
            {drillTrade && <FillDrilldownModal trade={drillTrade} onClose={() => setDrillTrade(null)} />}
            {analyzeTrade && <LossDetailModal trade={analyzeTrade} onClose={() => setAnalyzeTrade(null)} />}
            {trades.length > 0 && (
                <div className="exec-totals">
                    <div className="exec-total-item">
                        Today: <span>{todayTrades.length}</span>
                    </div>
                    <div className="exec-total-item">
                        Win Rate: <span>{winRate}%</span>
                    </div>
                    <div className="exec-total-item">
                        Realized: <span style={{ color: totalPnl >= 0 ? '#3fb950' : '#f85149' }}>
                            {formatUSD(totalPnl)}
                        </span>
                    </div>
                </div>
            )}
            <LossSummaryWidget summary={lossSummary} />
        </div>
    );
};

// ============================================================
//  Zone 3: Collapsible Panel
// ============================================================
const CollapsiblePanel = ({
    storageKey,
    title,
    children,
}: {
    storageKey: string;
    title: string;
    children: React.ReactNode;
}) => {
    const [open, setOpen] = useState(() => {
        const v = localStorage.getItem(storageKey);
        return v === null ? true : v === 'true';
    });

    const toggle = () => {
        const next = !open;
        setOpen(next);
        localStorage.setItem(storageKey, String(next));
    };

    return (
        <div className="collapsible-panel">
            <div
                className="collapsible-panel-header"
                onClick={toggle}
                role="button"
                tabIndex={0}
                aria-expanded={open}
                aria-label={`${title} panel — click to ${open ? 'collapse' : 'expand'}`}
                onKeyDown={e => e.key === 'Enter' && toggle()}
            >
                <span className="collapsible-panel-title">{title}</span>
                <span className={`panel-chevron ${open ? 'open' : ''}`} aria-hidden="true">▶</span>
            </div>
            <div className={`collapsible-panel-body ${open ? '' : 'hidden'}`} aria-hidden={!open}>
                {children}
            </div>
        </div>
    );
};

// ============================================================
//  Main Dashboard Component
// ============================================================

const Dashboard = ({ brokerStatus, integrityRed = false }: { brokerStatus: BrokerStatus | null; integrityRed?: boolean }) => {
    const [signals, setSignals] = useState<Signal[]>([]);
    const [trades, setTrades] = useState<Trade[]>([]);
    const [portfolio, setPortfolio] = useState<Portfolio>({
        positions: {},
        nav: 0,
        cash: 0,
        realized_pnl: 0,
        unrealized_pnl: 0,
    });
    const [account, setAccount] = useState<AccountSummary | null>(null);
    const [accountError, setAccountError] = useState<string | null>(null);
    const [accountLoading, setAccountLoading] = useState(true);
    const [ibkrPositions, setIBKRPositions] = useState<IBKRPosition[]>([]);
    const [pendingOrders, setPendingOrders] = useState<PendingOrdersResponse | null>(null);
    const [capEvents, setCapEvents] = useState<CapEvent[]>([]);
    const [replacementToast, setReplacementToast] = useState<CapEvent | null>(null);
    const prevCapEventsLenRef = useRef(0);
    const [simMode, setSimMode] = useState<string>('paper');
    const [scorecard, setScorecard] = useState<ModelScorecard[]>([]);
    const [scorecardLoading, setScorecardLoading] = useState(true);
    const [lossSummary, setLossSummary] = useState<LossSummary | null>(null);
    const [intel, setIntel] = useState<Intel | null>(null);
    const [intelLoading, setIntelLoading] = useState(true);
    const [systemState, setSystemState] = useState<SystemState | null>(null);
    const [audit, setAudit] = useState<DataAudit | null>(null);
    const [isFetching, setIsFetching] = useState(false);
    const [lastFetchMs, setLastFetchMs] = useState(0);
    const [selectedSignal, setSelectedSignal] = useState<Signal | null>(null);
    const [newTimestamps, setNewTimestamps] = useState<Set<number>>(new Set());

    const prevSignalTsRef = useRef<Set<number>>(new Set());

    // Fetch audit on a slower interval (30s) — runs Python subprocess
    const fetchAudit = useCallback(async () => {
        try {
            const r = await fetch('/api/data/audit');
            if (r.ok) setAudit(await r.json());
        } catch { /* ignore */ }
    }, []);

    useEffect(() => {
        fetchAudit();
        const id = setInterval(fetchAudit, 30000);
        return () => clearInterval(id);
    }, [fetchAudit]);

    // Fetch live IBKR account + positions (10s interval)
    const fetchAccount = useCallback(async () => {
        setAccountLoading(true);
        try {
            const [acctRes, posRes] = await Promise.all([
                fetch('/api/account'),
                fetch('/api/positions'),
            ]);
            if (acctRes.ok) {
                const a = await acctRes.json() as AccountSummary;
                if (a.error) {
                    setAccountError(a.error);
                } else {
                    setAccount(a);
                    setAccountError(null);
                }
            } else {
                setAccountError(`IBKR account endpoint returned ${acctRes.status} — last attempt ${new Date().toLocaleTimeString()}`);
            }
            if (posRes.ok) {
                const p = await posRes.json() as IBKRPosition[];
                setIBKRPositions(Array.isArray(p) ? p : []);
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : String(e);
            setAccountError(`IB Gateway not reachable — ${msg} — last attempt ${new Date().toLocaleTimeString()}`);
        }
        setAccountLoading(false);
    }, []);

    // Fetch pending IBKR orders (30s interval — subprocess is slow)
    const fetchPendingOrders = useCallback(async () => {
        try {
            const r = await fetch('/api/orders/pending');
            if (r.ok) setPendingOrders(await r.json() as PendingOrdersResponse);
        } catch { /* ignore — endpoint unavailable or gateway down */ }
    }, []);

    // Fetch cap events (15s interval — in-memory, fast)
    const fetchCapEvents = useCallback(async () => {
        try {
            const r = await fetch('/api/orders/cap-events');
            if (!r.ok) return;
            const data = await r.json() as { events: CapEvent[] };
            const events: CapEvent[] = data.events ?? [];
            setCapEvents(events);
            // Show toast if a new event arrived since last poll
            if (events.length > prevCapEventsLenRef.current && events.length > 0) {
                setReplacementToast(events[0]);
                setTimeout(() => setReplacementToast(null), 6000);
            }
            prevCapEventsLenRef.current = events.length;
        } catch { /* ignore */ }
    }, []);

    useEffect(() => {
        // Initial call handled by main stagger below
        const id = setInterval(fetchAccount, 10000);
        return () => clearInterval(id);
    }, [fetchAccount]);

    useEffect(() => {
        const id = setInterval(fetchPendingOrders, 30000);
        return () => clearInterval(id);
    }, [fetchPendingOrders]);

    useEffect(() => {
        const id = setInterval(fetchCapEvents, 15000);
        return () => clearInterval(id);
    }, [fetchCapEvents]);

    // Fetch scorecard + loss summary (60s interval)
    const fetchScorecard = useCallback(async () => {
        setScorecardLoading(true);
        try {
            const [scRes, lsRes] = await Promise.all([
                fetch('/api/scorecard'),
                fetch('/api/losses'),
            ]);
            if (scRes.ok) setScorecard(await scRes.json() as ModelScorecard[]);
            if (lsRes.ok) setLossSummary(await lsRes.json() as LossSummary);
        } catch { /* ignore */ }
        setScorecardLoading(false);
    }, []);

    useEffect(() => {
        const id = setInterval(fetchScorecard, 60000);
        return () => clearInterval(id);
    }, [fetchScorecard]);

    // Fetch intel (5 min interval — cached server-side anyway)
    const fetchIntel = useCallback(async () => {
        setIntelLoading(true);
        try {
            const r = await fetch('/api/intel');
            if (r.ok) setIntel(await r.json() as Intel);
        } catch { /* ignore */ }
        setIntelLoading(false);
    }, []);

    useEffect(() => {
        const id = setInterval(fetchIntel, 300000);
        return () => clearInterval(id);
    }, [fetchIntel]);

    const handleSimToggle = useCallback(async () => {
        try {
            const r = await fetch('/api/sim/toggle');
            if (r.ok) {
                const d = await r.json() as { mode: string };
                setSimMode(d.mode);
            }
        } catch { /* ignore */ }
    }, []);

    const handleSimReset = useCallback(async () => {
        try {
            await fetch('/api/sim/reset', { method: 'POST' });
        } catch { /* ignore */ }
    }, []);

    const fetchAll = useCallback(async () => {
        setIsFetching(true);
        try {
            const [portRes, tradesRes, sigRes, stateRes] = await Promise.all([
                fetch('/api/portfolio'),
                fetch('/api/closed_trades'),
                fetch('/api/signals/all'),
                fetch('/api/system/state'),
            ]);

            if (portRes.ok) {
                const raw = await portRes.json();
                setPortfolio(raw as Portfolio);
            }
            if (tradesRes.ok) {
                const closedRaw = await tradesRes.json();
                // Map closed_trades DB fields to Trade interface
                const closedTrades = (closedRaw as any[]).map(t => ({
                    time: t.ts || t.time || '',
                    action: t.exit_price ? 'SELL' : 'BUY',
                    ticker: t.option_type ? `${t.ticker}_${t.option_type}_${t.expiration_date}_${t.strike}` : (t.ticker || ''),
                    quantity: t.qty ?? t.quantity ?? 0,
                    price: t.exit_price ?? t.entry_price ?? t.price ?? 0,
                    cost: (t.qty ?? t.quantity ?? 0) * (t.entry_price ?? t.price ?? 0) * 100,
                    pnl: t.pnl ?? 0,
                    net_profit: t.pnl ?? 0,
                    id: t.id,
                    signal_id: t.signal_id,
                    option_type: t.option_type,
                    strike: t.strike,
                    expiration_date: t.expiration_date,
                    entry_price: t.entry_price,
                    exit_price: t.exit_price,
                    exit_reason: t.exit_reason,
                    model_id: t.model_id,
                    confidence_at_entry: t.confidence_at_entry,
                    source: t.source,
                } as Trade));

                // Fetch live in-memory trades as primary source
                try {
                    const liveRes = await fetch('/api/trades');
                    if (liveRes.ok) {
                        const liveTrades = await liveRes.json() as Trade[];
                        // Merge: live trades first, then closed trades; dedupe by time+ticker
                        const merged = [...liveTrades, ...closedTrades];
                        const seen = new Set<string>();
                        const unique = merged.filter(t => {
                            const key = `${t.time}-${t.ticker}`;
                            if (seen.has(key)) return false;
                            seen.add(key);
                            return true;
                        });
                        setTrades(unique);
                    } else {
                        setTrades(closedTrades);
                    }
                } catch {
                    setTrades(closedTrades);
                }
            }
            if (sigRes.ok) {
                const raw = await sigRes.json();
                const sigs = (raw as Signal[]).filter(s => s.strategy_code !== 'IDLE_SCAN');

                // Detect new signals by comparing timestamps
                const incoming = new Set(sigs.map(s => s.timestamp));
                const prev = prevSignalTsRef.current;
                const freshTs: Set<number> = new Set();
                for (const ts of incoming) {
                    if (!prev.has(ts)) freshTs.add(ts);
                }
                prevSignalTsRef.current = incoming;

                if (freshTs.size > 0) {
                    setNewTimestamps(freshTs);
                    setTimeout(() => setNewTimestamps(new Set()), 3000);
                }

                setSignals(sigs);
            }
            if (stateRes.ok) {
                const raw = await stateRes.json();
                setSystemState(raw as SystemState);
                if (raw.mode) setSimMode(raw.mode);
            }

            setLastFetchMs(Date.now());
        } catch { /* ignore */ }
        setIsFetching(false);
    }, []);

    useEffect(() => {
        fetchAll();
        setTimeout(fetchAccount, 500);
        setTimeout(fetchPendingOrders, 2000);
        setTimeout(fetchCapEvents, 2500);
        setTimeout(fetchScorecard, 1500);
        setTimeout(fetchIntel, 3000);
        const id = setInterval(fetchAll, 3000);
        return () => clearInterval(id);
    }, [fetchAll, fetchPendingOrders, fetchCapEvents]);

    return (
        <>
            {/* Signal modal */}
            {selectedSignal && (
                <SignalModal signal={selectedSignal} onClose={() => setSelectedSignal(null)} />
            )}

            {/* Integrity RED warning banner */}
            {integrityRed && (
                <div
                    role="alert"
                    aria-live="assertive"
                    style={{
                        background: 'rgba(248,81,73,0.12)',
                        border: '1px solid rgba(248,81,73,0.4)',
                        borderRadius: '6px',
                        padding: '8px 16px',
                        margin: '8px 0 0',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '10px',
                        fontSize: '0.8rem',
                        color: '#f85149',
                        fontWeight: 600,
                    }}
                >
                    <span>⛔</span>
                    <span>INTEGRITY ALERT — Price or chain data integrity check failed. New trades are blocked. Check INTEGRITY indicators in the header.</span>
                    <button
                        data-testid="new-trade-blocked"
                        disabled
                        style={{
                            marginLeft: 'auto',
                            padding: '4px 14px',
                            borderRadius: '4px',
                            border: '1px solid rgba(248,81,73,0.4)',
                            background: 'rgba(248,81,73,0.1)',
                            color: '#f85149',
                            cursor: 'not-allowed',
                            fontSize: '0.72rem',
                            fontWeight: 700,
                        }}
                        aria-label="New trade button — disabled while integrity check fails"
                        aria-disabled="true"
                    >
                        NEW TRADE (BLOCKED)
                    </button>
                </div>
            )}

            {/* Broker status banner — prominent at top when LIVE */}
            <BrokerStatusPill brokerStatus={brokerStatus} />

            {/* Zone 1: Portfolio Command Bar */}
            <PortfolioBar
                portfolio={portfolio}
                account={account}
                accountError={accountError}
                accountLoading={accountLoading}
                onRetryAccount={fetchAccount}
                simMode={simMode}
                onSimToggle={handleSimToggle}
                onSimReset={handleSimReset}
                isFetching={isFetching}
                lastFetchMs={lastFetchMs}
            />

            {/* Data Provenance Panel — TV vs YF spot comparison */}
            <DataProvenancePanel audit={audit} />

            {/* Pre-Market Intelligence Panel */}
            <CollapsiblePanel
                storageKey="dashboard_premarket_open"
                title="📡 PRE-MARKET INTELLIGENCE"
            >
                <PreMarketPanel intel={intel} isLoading={intelLoading} />
            </CollapsiblePanel>

            {/* Zone 2: 4-Column Trading Grid */}
            <div className="dashboard-grid">
                <SignalCommand
                    signals={signals}
                    newTimestamps={newTimestamps}
                    onSelectSignal={setSelectedSignal}
                    systemState={systemState}
                />
                <PendingOrdersPanel
                    orders={pendingOrders}
                    source={pendingOrders?.source ?? ''}
                    capEvents={capEvents}
                    replacementToast={replacementToast}
                />
                <TradingFloor portfolio={portfolio} ibkrPositions={ibkrPositions} brokerStatus={brokerStatus} />
                <ExecutionLog trades={trades} brokerStatus={brokerStatus} lossSummary={lossSummary} simMode={simMode} />
            </div>

            {/* Zone 3: Chart + Monitor Strip */}
            <div className="bottom-strip">
                <CollapsiblePanel
                    storageKey="dashboard_intel_open"
                    title="🔭 MARKET INTELLIGENCE"
                >
                    <IntelPanel intel={intel} isLoading={intelLoading} />
                </CollapsiblePanel>
                <CollapsiblePanel
                    storageKey="dashboard_scorecard_open"
                    title="📊 MODEL SCORECARD"
                >
                    <ModelScorecardPanel scorecard={scorecard} isLoading={scorecardLoading} />
                </CollapsiblePanel>
                <CollapsiblePanel
                    storageKey="dashboard_chart_open"
                    title="📈 TSLA CHART"
                >
                    <TradingViewWidget />
                </CollapsiblePanel>
                <CollapsiblePanel
                    storageKey="dashboard_monitor_open"
                    title="🖥 SYSTEM MONITOR"
                >
                    <SystemMonitor />
                </CollapsiblePanel>
            </div>
        </>
    );
};

export default Dashboard;
