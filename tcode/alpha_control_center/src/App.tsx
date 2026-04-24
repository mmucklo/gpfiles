import { useState, useEffect, useCallback, Component, type ReactNode, type ErrorInfo } from 'react';
import { Routes, Route, Link } from 'react-router-dom';
import './App.css';
import './components/Tooltip.css';
import Tooltip from './components/Tooltip';

// ── Root ErrorBoundary — catches any render crash and shows a red banner ─────
// Without this, a single panel crash blanks the entire dashboard (React default).
interface EBState { hasError: boolean; error: string | null }
export class RootErrorBoundary extends Component<{ children: ReactNode }, EBState> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(err: Error): EBState {
    return { hasError: true, error: err?.message ?? String(err) };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('[RootErrorBoundary] render crash:', err, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: '2rem', background: '#0d1117', minHeight: '100vh',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            background: '#6e1919', border: '2px solid #f85149', borderRadius: '8px',
            padding: '1.5rem 2rem', maxWidth: '640px', width: '100%',
          }}>
            <div style={{ color: '#f85149', fontWeight: 800, fontSize: '16px', marginBottom: '8px' }}>
              Dashboard render error — click to reload
            </div>
            <div style={{ color: '#ffa657', fontFamily: 'monospace', fontSize: '12px', marginBottom: '16px' }}>
              {this.state.error}
            </div>
            <button
              style={{ background: '#f85149', border: 'none', color: 'white', padding: '8px 20px', borderRadius: '4px', cursor: 'pointer', fontWeight: 700 }}
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
import IntegrityStatus from './components/IntegrityStatus';
import HelpPanel from './components/HelpPanel';
import SystemHealthPanel, { type HealthSummary } from './components/SystemHealthPanel';
import PauseOverlay, { PauseCountdown, type PauseStatus } from './components/PauseOverlay';
import PauseWatchdogBadge from './components/PauseWatchdogBadge';
import { Shield, Menu, Home, Share2, Map, HelpCircle } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import Architecture from './pages/Architecture';
import Gastown from './pages/Gastown';
import { useDataFetching } from './hooks';

const API_BASE = '/api/config';

// ── Phase 16: Market-hours theme ──────────────────────────────────────────────
// Returns 'open' | 'premarket' | 'afterhours' | 'closed'
function getMarketState(): 'open' | 'premarket' | 'afterhours' | 'closed' {
  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const h = et.getHours(), m = et.getMinutes();
  const t = h * 60 + m;
  const wd = et.getDay();
  if (wd === 0 || wd === 6) return 'closed';          // weekend
  if (t >= 570 && t < 960)  return 'open';            // 9:30–16:00
  if (t >= 240 && t < 570)  return 'premarket';       // 4:00–9:30
  if (t >= 960 && t < 1200) return 'afterhours';      // 16:00–20:00
  return 'closed';                                     // midnight–4:00, 20:00–24:00
}

function App() {
  const [config, setConfig] = useState({
    ibkr: { host: '127.0.0.1', port: 7497, username: '', password: '' },
    telegram: { token: '', chat_id: '' },
  });
  const [portfolio, setPortfolio] = useState<any>({ positions: {} });
  const [loading, setLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [integrityRed, setIntegrityRed] = useState(false);
  const [healthSummary, setHealthSummary] = useState<HealthSummary | null>(null);

  // Notional account size state
  const [notional, setNotional] = useState<number>(25000);
  const [notionalInput, setNotionalInput] = useState<string>('');
  const [showNotionalAdjust, setShowNotionalAdjust] = useState(false);
  const [notionalToast, setNotionalToast] = useState<string>('');
  const [notionalPendingRestart, setNotionalPendingRestart] = useState(false);
  const [showNotionalDrill, setShowNotionalDrill] = useState(false);
  const [showHealthModal, setShowHealthModal] = useState(false);

  // Phase 16.1: Pause state (synced with PauseOverlay via callback)
  const [pauseStatus, setPauseStatus] = useState<PauseStatus>({ paused: true, unpause_until: null, remaining_sec: 0 });

  const handlePause = async () => {
    try {
      const r = await fetch('/api/system/pause', { method: 'POST' });
      if (r.ok) {
        const s: PauseStatus = await r.json();
        setPauseStatus(s);
      }
    } catch { /* ignore */ }
  };

  const brokerStatus = useDataFetching('/api/broker/status', 10000, null);

  // Phase 16: market-hours theme — update body CSS var every 30s
  const [marketState, setMarketState] = useState(() => getMarketState());
  useEffect(() => {
    const apply = (state: string) => {
      document.body.dataset.marketState = state;
    };
    apply(marketState);
    const t = setInterval(() => {
      const s = getMarketState();
      setMarketState(s);
      apply(s);
    }, 30000);
    return () => clearInterval(t);
  }, [marketState]);

  useEffect(() => {
    fetchConfig();
    fetchHeaderData();
    const interval = setInterval(fetchHeaderData, 10000);
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/dev/ws`);
    ws.onmessage = (event) => {
      if (event.data === 'reload') window.location.reload();
    };

    return () => {
        clearInterval(interval);
        ws.close();
    }
  }, []);

  const fetchHeaderData = async () => {
    try {
        // /api/portfolio now returns the authoritative data for the active mode:
        //   IBKR_PAPER / IBKR_LIVE → IBKR NAV overrides internal, source = execution mode
        //   SIMULATION             → internal PaperPortfolio, source = "SIMULATION"
        // No need to cross-check /api/account here; use portfolio directly.
        const portRes = await fetch('/api/portfolio');
        const port = portRes.ok ? await portRes.json() : null;
        if (port && !port.error) {
            setPortfolio(port);
        }
    } catch (e) { }
  };

  // Fetch notional config on mount
  useEffect(() => {
    fetch('/api/config/notional')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.notional_account_size) { setNotional(d.notional_account_size); setNotionalInput(String(d.notional_account_size)); } })
      .catch(() => {});
  }, []);

  const isMarketHours = (() => {
    const now = new Date();
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const h = et.getHours(), m = et.getMinutes();
    const t = h * 60 + m;
    const wd = et.getDay();
    return wd >= 1 && wd <= 5 && t >= 570 && t < 960;
  })();

  const handleNotionalApply = async (newValue: number) => {
    if (newValue < 5000 || newValue > 250000) {
      setNotionalToast(`Invalid: must be $5,000–$250,000`);
      setTimeout(() => setNotionalToast(''), 4000);
      return;
    }
    const prevNotional = notional;
    const pctChange = Math.abs(newValue - prevNotional) / prevNotional * 100;
    if (pctChange > 50) {
      const confirmed = window.confirm(
        `Changing notional from $${prevNotional.toLocaleString()} to $${newValue.toLocaleString()} (${pctChange.toFixed(0)}% change). Confirm?`
      );
      if (!confirmed) return;
    }
    try {
      const r = await fetch('/api/config/notional', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notional_account_size: newValue }),
      });
      if (r.ok) {
        const d = await r.json();
        setNotional(d.notional_account_size);
        setNotionalInput(String(d.notional_account_size));
        setNotionalPendingRestart(d.pending_restart ?? false);
        setShowNotionalAdjust(false);
        setNotionalToast(`Notional updated to $${d.notional_account_size.toLocaleString()} — new signals will use this value`);
        setTimeout(() => setNotionalToast(''), 5000);
      }
    } catch { /* ignore */ }
  };

  const fetchConfig = async () => {
    try {
      const response = await fetch(API_BASE);
      if (response.ok) {
        setConfig(await response.json());
      }
    } catch (e) { }
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      await fetch(API_BASE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      });
      alert('Config Updated');
    } catch (e) { }
    setLoading(false);
  };

  const handleIntegrityChange = useCallback((isRed: boolean) => {
    setIntegrityRed(isRed);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowHealthModal(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  return (
    <div className="app-container">
      {/* Phase 16.1: Pause overlay — covers entire dashboard when publisher is paused */}
      <PauseOverlay onStatusChange={setPauseStatus} />

      {showSettings && <div className="drawer-overlay" onClick={() => setShowSettings(false)} />}
      {showHelp && <HelpPanel onClose={() => setShowHelp(false)} />}

      <div className={`settings-drawer ${showSettings ? 'open' : ''}`} role="complementary" aria-label="Settings">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
          <h2 style={{ margin: 0 }}>SETTINGS</h2>
          <Tooltip text="Close Settings">
            <button className="btn-icon" onClick={() => setShowSettings(false)} aria-label="Close settings panel"><Shield size={20}/></button>
          </Tooltip>
        </div>
        <section className="card">
          <h2>IBKR</h2>
          <div className="form-group">
            <label htmlFor="ibkr-host">Host</label>
            <input id="ibkr-host" type="text" value={config.ibkr.host} onChange={e => setConfig({...config, ibkr: {...config.ibkr, host: e.target.value}})} aria-label="IBKR host address" />
          </div>
          <div className="form-group">
            <label htmlFor="ibkr-username">Username</label>
            <input id="ibkr-username" type="text" value={config.ibkr.username} onChange={e => setConfig({...config, ibkr: {...config.ibkr, username: e.target.value}})} aria-label="IBKR username" />
          </div>
          <div className="form-group">
            <label htmlFor="ibkr-password">Password</label>
            <input id="ibkr-password" type="password" value={config.ibkr.password} onChange={e => setConfig({...config, ibkr: {...config.ibkr, password: e.target.value}})} aria-label="IBKR password" />
          </div>
          <Tooltip text="Save and apply the new IBKR connection settings.">
            <button className="btn" onClick={handleSave} disabled={loading} style={{ width: '100%' }} aria-label="Apply IBKR settings">APPLY</button>
          </Tooltip>
        </section>
      </div>

      {/* Phase 18: Slim nav bar — StatusBar in Dashboard owns P&L + regime + mode */}
      <header className="header app-nav-bar" role="banner">
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <h1 aria-label="TSLA Alpha Command Center" style={{ fontSize: '0.95rem' }}>TSLA ALPHA COMMAND</h1>
          {/* Notional sizing — kept in nav for quick access */}
          <div style={{display:'flex',alignItems:'center',gap:'0.4rem',borderLeft:'1px solid #30363d',paddingLeft:'0.8rem'}}>
            <Tooltip text="Position sizing derives from this value. Click to explain.">
              <div
                role="button"
                tabIndex={0}
                style={{cursor:'pointer',color:'#79c0ff',fontSize:'0.78rem'}}
                data-testid="notional-display"
                aria-label={`Sizing for: $${notional.toLocaleString()}`}
                onClick={() => setShowNotionalDrill(true)}
                onKeyDown={e => e.key === 'Enter' && setShowNotionalDrill(true)}
              >
                <span style={{color:'#8b949e'}}>Sizing:</span>{' '}
                <span style={{fontWeight:700}}>${notional.toLocaleString()}</span>
              </div>
            </Tooltip>
            {(() => {
              const ibkrNav = portfolio.nav ?? 0;
              const diverges = ibkrNav > 0 && (ibkrNav >= notional * 5 || ibkrNav <= notional * 0.5);
              return diverges ? (
                <Tooltip text="Sizing target diverges materially from IBKR account — check NOTIONAL_ACCOUNT_SIZE configuration.">
                  <span style={{backgroundColor:'#9e6a03',color:'white',fontSize:'10px',padding:'1px 6px',borderRadius:'4px',fontWeight:700}} data-testid="notional-divergence-badge">
                    ⚠ SIZING DIVERGES
                  </span>
                </Tooltip>
              ) : null;
            })()}
            <Tooltip text={isMarketHours && brokerStatus?.mode === 'IBKR_LIVE' ? 'Disabled during live market hours.' : 'Adjust notional account size used for position sizing.'}>
              <button
                style={{
                  background:'#21262d',border:'1px solid #30363d',color:'#c9d1d9',
                  borderRadius:'4px',padding:'1px 6px',cursor: (isMarketHours && brokerStatus?.mode === 'IBKR_LIVE') ? 'not-allowed' : 'pointer',
                  fontSize:'11px',opacity: (isMarketHours && brokerStatus?.mode === 'IBKR_LIVE') ? 0.5 : 1,
                }}
                disabled={isMarketHours && brokerStatus?.mode === 'IBKR_LIVE'}
                data-testid="notional-adjust-toggle"
                aria-label="Adjust notional account size"
                onClick={() => setShowNotionalAdjust(v => !v)}
              >
                ▲▼
              </button>
            </Tooltip>
          </div>

          {/* Notional adjust flyout */}
          {showNotionalAdjust && (
            <div style={{position:'absolute',top:'70px',left:'20%',backgroundColor:'#161b22',border:'1px solid #30363d',borderRadius:'8px',padding:'1rem',zIndex:1000,display:'flex',gap:'0.5rem',alignItems:'center',boxShadow:'0 4px 12px rgba(0,0,0,0.4)'}}>
              <span style={{color:'#8b949e',fontSize:'12px'}}>Notional:</span>
              <button style={{background:'#21262d',border:'1px solid #30363d',color:'#c9d1d9',borderRadius:'4px',padding:'2px 8px',cursor:'pointer'}}
                onClick={() => { const v = Math.round(notional * 0.9); setNotionalInput(String(v)); handleNotionalApply(v); }}
                data-testid="notional-decrease"
                aria-label="Decrease notional by 10%">
                −10%
              </button>
              <input
                type="number"
                value={notionalInput}
                onChange={e => setNotionalInput(e.target.value)}
                style={{width:'90px',background:'#0d1117',border:'1px solid #30363d',color:'#c9d1d9',borderRadius:'4px',padding:'2px 6px',fontSize:'12px'}}
                min={5000} max={250000} step={1000}
                data-testid="notional-input"
                aria-label="Notional account size value"
              />
              <button style={{background:'#21262d',border:'1px solid #30363d',color:'#c9d1d9',borderRadius:'4px',padding:'2px 8px',cursor:'pointer'}}
                onClick={() => { const v = Math.round(notional * 1.1); setNotionalInput(String(v)); handleNotionalApply(v); }}
                data-testid="notional-increase"
                aria-label="Increase notional by 10%">
                +10%
              </button>
              <button style={{background:'#1a7f37',border:'1px solid #3fb950',color:'white',borderRadius:'4px',padding:'2px 10px',cursor:'pointer',fontWeight:700}}
                onClick={() => handleNotionalApply(parseInt(notionalInput, 10) || notional)}
                data-testid="notional-apply"
                aria-label="Apply notional change">
                APPLY
              </button>
              <button style={{background:'none',border:'none',color:'#8b949e',cursor:'pointer',fontSize:'14px'}}
                onClick={() => setShowNotionalAdjust(false)}
                aria-label="Close notional adjust">✕</button>
            </div>
          )}

          {/* Notional toast */}
          {notionalToast && (
            <div style={{position:'fixed',bottom:'20px',right:'20px',backgroundColor:'#1a7f37',color:'white',padding:'0.75rem 1.2rem',borderRadius:'8px',fontSize:'13px',fontWeight:600,zIndex:9999,boxShadow:'0 2px 8px rgba(0,0,0,0.4)'}}
              data-testid="notional-toast" role="alert">
              {notionalToast}
            </div>
          )}

          {/* Notional pending restart banner */}
          {notionalPendingRestart && (
            <div style={{backgroundColor:'#9e6a03',color:'white',padding:'4px 12px',borderRadius:'4px',fontSize:'11px',fontWeight:600,marginTop:'4px'}}
              data-testid="notional-restart-banner">
              Restart services to apply new notional — or wait for publisher auto-reload
            </div>
          )}

          {/* Notional drill-down modal */}
          {showNotionalDrill && (
            <div className="modal-overlay" onClick={() => setShowNotionalDrill(false)} role="dialog" aria-modal="true" aria-label="Notional Sizing Explanation">
              <div className="modal-card nav-drill" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                  <span className="modal-title">📐 NOTIONAL SIZING EXPLAINED</span>
                  <button className="modal-close" onClick={() => setShowNotionalDrill(false)} aria-label="Close">✕</button>
                </div>
                <div className="fill-drill-body">
                  <div className="fill-row"><span>Sizing for (notional)</span><span style={{color:'#79c0ff',fontWeight:700}}>${notional.toLocaleString()}</span></div>
                  <div className="fill-row"><span>IBKR account NAV</span><span>${portfolio.nav?.toLocaleString(undefined, {minimumFractionDigits: 2}) ?? '—'}</span></div>
                  <div className="fill-section" style={{marginTop:'12px'}}>
                    <p style={{color:'#8b949e',fontSize:'13px',lineHeight:'1.6'}}>
                      Position sizing derives from <strong style={{color:'#c9d1d9'}}>NOTIONAL_ACCOUNT_SIZE</strong>, not from IBKR NAV.
                      Paper trading uses ${notional.toLocaleString()} to practice small-account discipline for live trading.
                      This means all risk budgets, contract quantities, and minimum-edge floors are calculated as if you
                      have ${notional.toLocaleString()} — regardless of how large the paper account balance grows.
                    </p>
                  </div>
                  <div className="fill-row"><span>Risk per directional trade</span><span>1.0–1.5% of notional = ${Math.round(notional * 0.01).toLocaleString()}–${Math.round(notional * 0.015).toLocaleString()}</span></div>
                  <div className="fill-row"><span>Min edge floor</span><span>0.25% of notional = ${Math.round(notional * 0.0025).toLocaleString()}</span></div>
                  <div className="fill-row"><span>Gross outstanding cap</span><span>6% of notional = ${Math.round(notional * 0.06).toLocaleString()}</span></div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Hidden always-on health poller — keeps badge updated without requiring the modal open */}
        <div style={{ display: 'none' }} aria-hidden="true">
          <SystemHealthPanel onHealthChange={setHealthSummary} />
        </div>

        {/* System Health modal drill-down — Phase 14.4 */}
        {showHealthModal && (
          <div
            className="modal-overlay"
            onClick={() => setShowHealthModal(false)}
            role="dialog"
            aria-modal="true"
            aria-label="System Health Details"
          >
            <div
              className="modal-card nav-drill"
              style={{ maxWidth: '680px', width: '90%', maxHeight: '80vh', overflowY: 'auto' }}
              onClick={e => e.stopPropagation()}
            >
              <div className="modal-header">
                <span className="modal-title">SYSTEM HEALTH — PER-COMPONENT DETAIL</span>
                <button className="modal-close" onClick={() => setShowHealthModal(false)} aria-label="Close">✕</button>
              </div>
              <SystemHealthPanel />
            </div>
          </div>
        )}

        {/* Nav links + IntegrityStatus (still shown in nav for quick access) */}
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {/* Pause countdown — kept in nav for visibility */}
            <PauseCountdown status={pauseStatus} onPause={handlePause} />
            {/* Phase 21: pause leak watchdog badge */}
            <PauseWatchdogBadge />
            {/* Integrity Status — three traffic lights */}
            <IntegrityStatus onStatusChange={handleIntegrityChange} />
            {/* Market state badge */}
            {(() => {
              const stateConfig = {
                open:       { label: 'OPEN',   color: '#3fb950', bg: 'rgba(63,185,80,0.1)',    dot: true  },
                premarket:  { label: 'PRE',    color: '#79c0ff', bg: 'rgba(121,192,255,0.1)',  dot: false },
                afterhours: { label: 'AFTER',  color: '#d29922', bg: 'rgba(210,153,34,0.1)',   dot: false },
                closed:     { label: 'CLOSED', color: '#6e7681', bg: 'rgba(110,118,129,0.06)', dot: false },
              }[marketState];
              return (
                <span
                  data-testid="market-state-badge"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.25rem',
                    padding: '0.15rem 0.45rem', borderRadius: '4px', fontSize: '0.65rem',
                    fontWeight: 700, color: stateConfig.color, background: stateConfig.bg,
                    border: `1px solid ${stateConfig.color}30`,
                  }}
                  aria-label={`Market state: ${marketState}`}
                >
                  {stateConfig.dot && (
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: stateConfig.color, flexShrink: 0 }} />
                  )}
                  {stateConfig.label}
                </span>
              );
            })()}
            <Link to="/" aria-label="Go to Dashboard">
              <Tooltip text="Dashboard — main trading view">
                <Home className="hamburger" aria-hidden="true" />
              </Tooltip>
            </Link>
            <Link to="/architecture" aria-label="Go to Architecture view">
              <Tooltip text="Architecture — system diagram">
                <Share2 className="hamburger" aria-hidden="true" />
              </Tooltip>
            </Link>
            <Link to="/gastown" aria-label="Go to Gastown status">
              <Tooltip text="Gastown Status — agent health">
                <Map className="hamburger" aria-hidden="true" />
              </Tooltip>
            </Link>
            <Tooltip text="What's on this screen? — panel guide and keyboard shortcuts">
              <button
                className="hamburger"
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '0.5rem' }}
                onClick={() => setShowHelp(true)}
                aria-label="Open help panel — what's on this screen?"
                data-testid="help-button"
              >
                <HelpCircle size={22} aria-hidden="true" />
              </button>
            </Tooltip>
            <Tooltip text="Open Settings — IBKR connection config">
                <Menu
                  className="hamburger"
                  onClick={() => setShowSettings(true)}
                  size={24}
                  aria-label="Open settings"
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => e.key === 'Enter' && setShowSettings(true)}
                />
            </Tooltip>
        </div>
      </header>

      <main className="main-content" role="main">
        <Routes>
          <Route path="/" element={
            <Dashboard
              brokerStatus={brokerStatus}
              integrityRed={integrityRed}
              healthSummary={healthSummary}
              pauseStatus={pauseStatus}
              onHealthClick={() => setShowHealthModal(true)}
              onPause={handlePause}
            />
          } />
          <Route path="/architecture" element={<Architecture />} />
          <Route path="/gastown" element={<Gastown />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
