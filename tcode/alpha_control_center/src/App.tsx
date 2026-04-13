import { useState, useEffect, useCallback } from 'react';
import { Routes, Route, Link } from 'react-router-dom';
import './App.css';
import './components/Tooltip.css';
import Tooltip from './components/Tooltip';
import IntegrityStatus from './components/IntegrityStatus';
import HelpPanel from './components/HelpPanel';
import { Shield, Menu, Home, Share2, Map, HelpCircle } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import Architecture from './pages/Architecture';
import Gastown from './pages/Gastown';
import { useDataFetching } from './hooks';

const API_BASE = '/api/config';

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

  const brokerStatus = useDataFetching('/api/broker/status', 10000, null);

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

  return (
    <div className="app-container">
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

      <header className="header" role="banner">
        <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
          <h1 aria-label="TSLA Alpha Command Center">TSLA ALPHA COMMAND</h1>
          <nav aria-label="Portfolio summary" style={{ display: 'flex', gap: '1rem', backgroundColor: '#161b22', padding: '0.5rem 1rem', borderRadius: '8px', border: '1px solid #30363d' }}>
            <Tooltip text="Net Asset Value — total portfolio value including open positions. Click Dashboard for drill-down.">
              <div aria-label={`NAV: ${portfolio.nav?.toLocaleString(undefined, {minimumFractionDigits: 2}) ?? '—'}`} role="status">
                <span style={{color:'#8b949e'}}>NAV:</span> ${portfolio.nav?.toLocaleString(undefined, {minimumFractionDigits: 2})}
              </div>
            </Tooltip>
            <Tooltip text="Available Cash — spendable balance. See Trading Floor for breakdown.">
              <div style={{color: '#238636'}} aria-label={`Cash: ${portfolio.cash?.toLocaleString(undefined, {minimumFractionDigits: 2}) ?? '—'}`} role="status">
                <span style={{color:'#8b949e'}}>CASH:</span> ${portfolio.cash?.toLocaleString(undefined, {minimumFractionDigits: 2})}
              </div>
            </Tooltip>
            <Tooltip text="Realized P&L — locked-in profit/loss from closed trades. See Execution Log for per-trade breakdown.">
              <div style={{color: portfolio.realized_pnl >= 0 ? '#238636' : '#da3633'}} aria-label={`Realized P&L: ${portfolio.realized_pnl?.toLocaleString(undefined, {minimumFractionDigits: 2}) ?? '—'}`} role="status">
                <span style={{color:'#8b949e'}}>REALIZED:</span> ${portfolio.realized_pnl?.toLocaleString(undefined, {minimumFractionDigits: 2})}
              </div>
            </Tooltip>
          </nav>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
            {/* Integrity Status — three traffic lights */}
            <IntegrityStatus onStatusChange={handleIntegrityChange} />

            {/* Execution mode banner — always visible, never hidden.
                Maps the EXECUTION_MODE enum (IBKR_PAPER / IBKR_LIVE / SIMULATION)
                to colour-coded labels with a disconnected warning overlay. */}
            {(() => {
              const mode = brokerStatus?.mode ?? 'LOADING';
              const isLive = mode === 'IBKR_LIVE';
              const isSim = mode === 'SIMULATION';
              const isLoading = !brokerStatus;
              const disconnected = brokerStatus && !brokerStatus.connected && !isSim;
              const bg = isLive ? '#da3633' : isSim ? '#9e6a03' : isLoading ? '#30363d' : '#1a7f37';
              const border = isLive ? '#f85149' : isSim ? '#d29922' : isLoading ? '#484f58' : '#3fb950';
              const label = isLive ? '⚠ MODE: IBKR LIVE' : isSim ? 'MODE: SIMULATION' : isLoading ? 'MODE: …' : 'MODE: IBKR PAPER';
              const tooltip = isLive
                ? 'LIVE trading mode — real money. All orders execute against your live IBKR account.'
                : isSim
                ? 'Simulation mode — internal paper portfolio only. No broker connected.'
                : isLoading
                ? 'Loading execution mode…'
                : 'IBKR paper trading mode — orders route to your IBKR paper account.';
              return (
                <Tooltip text={disconnected ? `${tooltip} ⚠ IBKR connection lost — trade submission blocked.` : tooltip}>
                  <span
                    data-testid="broker-mode-badge"
                    aria-label={`Execution mode: ${mode}${disconnected ? ' — IBKR disconnected' : ''}`}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '0.4rem',
                      padding: '0.3rem 0.9rem',
                      borderRadius: '6px',
                      fontSize: '0.8rem',
                      fontWeight: 800,
                      letterSpacing: '0.07em',
                      backgroundColor: bg,
                      color: 'white',
                      border: `2px solid ${border}`,
                      boxShadow: isLive ? `0 0 8px ${border}66` : 'none',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {label}
                    {disconnected && <span style={{ color: '#ffa657', fontWeight: 900, fontSize: '1em' }} aria-hidden="true">⚠</span>}
                  </span>
                </Tooltip>
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
          <Route path="/" element={<Dashboard brokerStatus={brokerStatus} integrityRed={integrityRed} />} />
          <Route path="/architecture" element={<Architecture />} />
          <Route path="/gastown" element={<Gastown />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
