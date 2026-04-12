import { useState, useEffect, useRef, useCallback } from 'react';
import './Dashboard.css';
import Tooltip from '../components/Tooltip';
import TradingViewWidget from '../components/TradingViewWidget';
import SystemMonitor from '../components/SystemMonitor';

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
}

interface BrokerStatus {
    mode: string;    // 'live' | 'paper' | 'simulation'
    connected: boolean;
}

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
interface Intel {
    fetch_timestamp: number;
    news: IntelNews;
    vix: IntelVix;
    spy: IntelSpy;
    earnings: IntelEarnings;
    options_flow: IntelOptionsFlow;
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
                        <span className="signal-modal-title">
                            {signal.is_spread ? contractName(signal) : `${signal.action} ${contractName(signal)}`}
                        </span>
                    </Tooltip>
                    <button className="btn-modal-close" onClick={onClose}>×</button>
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
                        <button className="btn-view-chain" onClick={() => fetchChain()} disabled={chainLoading}>
                            {chainLoading ? 'Fetching…' : '⟳ Options Chain'}
                        </button>
                        <button className="btn-view-chain" onClick={() => fetchAudit(true)} disabled={auditLoading}>
                            {auditLoading ? 'Fetching…' : '⟳ Spot Audit'}
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
        <div className={`broker-status-banner ${cls}`}>
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
    simMode,
    onSimToggle,
    onSimReset,
    isFetching,
    lastFetchMs,
}: {
    portfolio: Portfolio;
    account: AccountSummary | null;
    simMode: string;
    onSimToggle: () => void;
    onSimReset: () => void;
    isFetching: boolean;
    lastFetchMs: number;
}) => {
    const [marketStatus, setMarketStatus] = useState(getMarketStatus());
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
        <div className="portfolio-bar">
            {/* Mode toggle pill */}
            <Tooltip text="Toggle between Paper Trading (live IBKR paper account) and Simulation (local model)">
                <div
                    className={`port-pill mode-toggle ${simMode === 'paper' ? 'paper' : 'sim'}`}
                    onClick={onSimToggle}
                    style={{ cursor: 'pointer' }}
                >
                    <span className="port-pill-label">MODE</span>
                    <span className="port-pill-value">{simMode.toUpperCase()}</span>
                </div>
            </Tooltip>

            <Tooltip text={isLiveAccount ? 'Net Liquidation Value from live IBKR paper account' : 'Simulated NAV'}>
                <div className="port-pill">
                    <span className="port-pill-label">
                        NET LIQ {isLiveAccount && <span className="ibkr-live-dot" />}
                        <span className="port-pill-source">{isPaper ? '(IBKR)' : '(SIM)'}</span>
                    </span>
                    <span className="port-pill-value neutral">{netliq !== null ? formatUSD(netliq) : '...'}</span>
                </div>
            </Tooltip>
            <Tooltip text="Available cash balance">
                <div className="port-pill">
                    <span className="port-pill-label">CASH <span className="port-pill-source">{isPaper ? '(IBKR)' : '(SIM)'}</span></span>
                    <span className="port-pill-value">{cash !== null ? formatUSD(cash) : '...'}</span>
                </div>
            </Tooltip>
            <Tooltip text="Buying power (4× cash for margin accounts)">
                <div className="port-pill">
                    <span className="port-pill-label">BUY PWR</span>
                    <span className="port-pill-value">{formatUSD(bp)}</span>
                </div>
            </Tooltip>
            <Tooltip text="Unrealized P&L: current mark-to-market on open positions">
                <div className="port-pill">
                    <span className="port-pill-label">UNREALIZED</span>
                    <span className={`port-pill-value ${unreal !== null && unreal >= 0 ? 'pos' : 'neg'}`}>
                        {unreal !== null ? (unreal !== 0 ? (unreal > 0 ? '+' : '') + formatUSD(unreal) : '$0.00') : '...'}
                    </span>
                </div>
            </Tooltip>
            <Tooltip text="Realized P&L: locked-in profit/loss from closed trades">
                <div className="port-pill">
                    <span className="port-pill-label">REALIZED <span className="port-pill-source">{isPaper ? '(IBKR)' : '(SIM)'}</span></span>
                    <span className={`port-pill-value ${real !== null && real >= 0 ? 'pos' : 'neg'}`}>
                        {real !== null ? formatUSD(real) : '...'}
                    </span>
                </div>
            </Tooltip>

            {simMode === 'sim' && (
                <Tooltip text="Reset simulation balances to $25k">
                    <button className="sim-reset-btn" onClick={onSimReset}>
                        RESET SIM
                    </button>
                </Tooltip>
            )}

            <div className="market-clock">
                <div className={`market-status-badge ${marketStatus.cls}`}>
                    {marketStatus.label}
                </div>
                <Tooltip text={`Data last refreshed ${Math.floor(staleSec)}s ago. Over 10s is considered stale — prices may not reflect current market.`}>
                    <div className="live-indicator">
                        <div className={`live-dot ${isFetching ? 'active' : isStale ? 'stale' : 'active'}`} />
                        <span>{isFetching ? 'Fetching…' : isStale ? 'Stale' : 'Live'}</span>
                    </div>
                </Tooltip>
            </div>
        </div>
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
        <div className="dash-col card">
            <div className="section-header">
                <Tooltip text="Live conviction signals from the Alpha Engine. Click any card for full details.">
                    <span className="section-title">⚡ SIGNAL COMMAND</span>
                </Tooltip>
                <div className="section-meta">
                    <span className="inline-badge">{signals.length}</span>
                    {hasNew && <span className="inline-badge new">NEW</span>}
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
                            >
                                <div className="signal-top-row">
                                    <Tooltip text={contractExplanation(s)}>
                                        <span className="signal-contract">{contract}</span>
                                    </Tooltip>
                                    <span className={`signal-action-badge ${isBull ? 'bullish' : 'bearish'}`}>
                                        {s.action}
                                    </span>
                                </div>
                                <div className="conviction-bar-wrap">
                                    <div className="conviction-bar-track">
                                        <div
                                            className="conviction-bar-fill"
                                            style={{ width: `${confPct.toFixed(1)}%` }}
                                        />
                                    </div>
                                    <div className="conviction-label">
                                        <Tooltip text="Model conviction: probability the signal is correct">
                                            <span>{confPct.toFixed(1)}% conviction</span>
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
                    <button className="btn-modal-close" onClick={onClose}>×</button>
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
//  Column B: Trading Floor (IBKR live positions + sim fallback)
// ============================================================
const TradingFloor = ({
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
            <div className="dash-col card">
                <div className="section-header">
                    <Tooltip text="Open positions from live IBKR paper account. Click any card for drill-down.">
                        <span className="section-title">🏛 TRADING FLOOR</span>
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
                    <button className="modal-close" onClick={onClose}>✕</button>
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
                    <button className="modal-close" onClick={onClose}>✕</button>
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
                            <div key={i} className="loss-mini-row" onClick={() => setSelectedLoss(lt)}>
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
const ModelScorecardPanel = ({ scorecard }: { scorecard: ModelScorecard[] }) => {
    if (scorecard.length === 0) {
        return <div className="scorecard-empty">No closed trades recorded yet.</div>;
    }
    return (
        <div className="scorecard-table-wrap">
            <table className="scorecard-table">
                <thead>
                    <tr>
                        <th>Model</th>
                        <th><Tooltip text="Total number of closed trades attributed to this model">Trades</Tooltip></th>
                        <th><Tooltip text="Percentage of closed trades that were profitable">Win%</Tooltip></th>
                        <th><Tooltip text="Average P&L per closed trade in dollars">Avg P&L</Tooltip></th>
                        <th><Tooltip text="Sum of all realized P&L from this model's trades">Total P&L</Tooltip></th>
                        <th><Tooltip text="Annualized Sharpe ratio — risk-adjusted return. >1 is good, >2 is excellent.">Sharpe</Tooltip></th>
                        <th><Tooltip text="Win rate on trades where confidence was ≥80%. Tests if high-conviction signals outperform.">Hi-Conf%</Tooltip></th>
                        <th><Tooltip text="Most common reason this model's trades were losers">Top Loss Tag</Tooltip></th>
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
//  Intel Panel
// ============================================================
const IntelPanel = ({ intel }: { intel: Intel | null }) => {
    if (!intel || !intel.vix) {
        return <div className="intel-loading">Fetching market intelligence…</div>;
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
                <div className="intel-card">
                    <Tooltip text="VIX = market fear gauge. SPY = S&P 500 ETF trend. Both influence TSLA signal confidence.">
                        <div className="intel-card-title">Market Pulse</div>
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
                <div className="intel-card">
                    <Tooltip text="NLP score from recent TSLA headlines + Musk mentions. Positive = bullish sentiment, negative = bearish.">
                        <div className="intel-card-title">
                            News Sentiment
                            <span className="intel-count-badge">{news.headline_count}</span>
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
                <div className="intel-card">
                    <Tooltip text="Put/Call ratio from the nearest expiry chain. P/C > 1.3 = bearish positioning. P/C < 0.7 = bullish. OI bars show relative call vs put open interest.">
                        <div className="intel-card-title">Options Flow</div>
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
                <div className="intel-card">
                    <Tooltip text="Next TSLA earnings date. Within 2 days = reduced signal confidence (±50% haircut) due to binary event risk.">
                        <div className="intel-card-title">Earnings Watch</div>
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
        <div className="dash-col card">
            <div className="section-header">
                <Tooltip text="All executed orders — buys and sells with realized P&L">
                    <span className="section-title">📋 {fromLog ? 'EXECUTION LOG (from system log)' : 'EXECUTION LOG'}</span>
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
                            <div key={i} className="trade-row" onClick={() => setDrillTrade(t)} style={{cursor:'pointer'}}>
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
            <div className="collapsible-panel-header" onClick={toggle}>
                <span className="collapsible-panel-title">{title}</span>
                <span className={`panel-chevron ${open ? 'open' : ''}`}>▶</span>
            </div>
            <div className={`collapsible-panel-body ${open ? '' : 'hidden'}`}>
                {children}
            </div>
        </div>
    );
};

// ============================================================
//  Main Dashboard Component
// ============================================================

const Dashboard = ({ brokerStatus }: { brokerStatus: BrokerStatus | null }) => {
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
    const [ibkrPositions, setIBKRPositions] = useState<IBKRPosition[]>([]);
    const [simMode, setSimMode] = useState<string>('paper');
    const [scorecard, setScorecard] = useState<ModelScorecard[]>([]);
    const [lossSummary, setLossSummary] = useState<LossSummary | null>(null);
    const [intel, setIntel] = useState<Intel | null>(null);
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
        try {
            const [acctRes, posRes] = await Promise.all([
                fetch('/api/account'),
                fetch('/api/positions'),
            ]);
            if (acctRes.ok) {
                const a = await acctRes.json() as AccountSummary;
                if (!a.error) setAccount(a);
            }
            if (posRes.ok) {
                const p = await posRes.json() as IBKRPosition[];
                setIBKRPositions(Array.isArray(p) ? p : []);
            }
        } catch { /* ignore */ }
    }, []);

    useEffect(() => {
        // Initial call handled by main stagger below
        const id = setInterval(fetchAccount, 10000);
        return () => clearInterval(id);
    }, [fetchAccount]);

    // Fetch scorecard + loss summary (60s interval)
    const fetchScorecard = useCallback(async () => {
        try {
            const [scRes, lsRes] = await Promise.all([
                fetch('/api/scorecard'),
                fetch('/api/losses'),
            ]);
            if (scRes.ok) setScorecard(await scRes.json() as ModelScorecard[]);
            if (lsRes.ok) setLossSummary(await lsRes.json() as LossSummary);
        } catch { /* ignore */ }
    }, []);

    useEffect(() => {
        const id = setInterval(fetchScorecard, 60000);
        return () => clearInterval(id);
    }, [fetchScorecard]);

    // Fetch intel (5 min interval — cached server-side anyway)
    const fetchIntel = useCallback(async () => {
        try {
            const r = await fetch('/api/intel');
            if (r.ok) setIntel(await r.json() as Intel);
        } catch { /* ignore */ }
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
        setTimeout(fetchScorecard, 1500);
        setTimeout(fetchIntel, 3000);
        const id = setInterval(fetchAll, 3000);
        return () => clearInterval(id);
    }, [fetchAll]);

    return (
        <>
            {/* Signal modal */}
            {selectedSignal && (
                <SignalModal signal={selectedSignal} onClose={() => setSelectedSignal(null)} />
            )}

            {/* Broker status banner — prominent at top when LIVE */}
            <BrokerStatusPill brokerStatus={brokerStatus} />

            {/* Zone 1: Portfolio Command Bar */}
            <PortfolioBar
                portfolio={portfolio}
                account={account}
                simMode={simMode}
                onSimToggle={handleSimToggle}
                onSimReset={handleSimReset}
                isFetching={isFetching}
                lastFetchMs={lastFetchMs}
            />

            {/* Data Provenance Panel — TV vs YF spot comparison */}
            <DataProvenancePanel audit={audit} />

            {/* Zone 2: 3-Column Trading Grid */}
            <div className="dashboard-grid">
                <SignalCommand
                    signals={signals}
                    newTimestamps={newTimestamps}
                    onSelectSignal={setSelectedSignal}
                    systemState={systemState}
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
                    <IntelPanel intel={intel} />
                </CollapsiblePanel>
                <CollapsiblePanel
                    storageKey="dashboard_scorecard_open"
                    title="📊 MODEL SCORECARD"
                >
                    <ModelScorecardPanel scorecard={scorecard} />
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
