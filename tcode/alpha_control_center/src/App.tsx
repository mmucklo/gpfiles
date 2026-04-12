import { useState, useEffect } from 'react';
import { Routes, Route, Link } from 'react-router-dom';
import './App.css';
import './components/Tooltip.css';
import Tooltip from './components/Tooltip';
import { Shield, Menu, Home, Share2, Map } from 'lucide-react';
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
        const [stateRes, acctRes, portRes] = await Promise.all([
            fetch('/api/system/state'),
            fetch('/api/account'),
            fetch('/api/portfolio'),
        ]);
        const state = stateRes.ok ? await stateRes.json() : null;
        const acct = acctRes.ok ? await acctRes.json() : null;
        const port = portRes.ok ? await portRes.json() : null;
        setPortfolio(port ?? { positions: {} });
        if (state?.mode === 'paper' && acct && !acct.error) {
            setPortfolio({ nav: acct.net_liquidation, cash: acct.cash_balance, realized_pnl: acct.realized_pnl, positions: {} });
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

  return (
    <div className="app-container">
      {showSettings && <div className="drawer-overlay" onClick={() => setShowSettings(false)} />}

      <div className={`settings-drawer ${showSettings ? 'open' : ''}`}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
          <h2 style={{ margin: 0 }}>SETTINGS</h2>
          <Tooltip text="Close Settings">
            <button className="btn-icon" onClick={() => setShowSettings(false)}><Shield size={20}/></button>
          </Tooltip>
        </div>
        <section className="card">
          <h2>IBKR</h2>
          <div className="form-group"><label>Host</label><input type="text" value={config.ibkr.host} onChange={e => setConfig({...config, ibkr: {...config.ibkr, host: e.target.value}})} /></div>
          <div className="form-group"><label>Username</label><input type="text" value={config.ibkr.username} onChange={e => setConfig({...config, ibkr: {...config.ibkr, username: e.target.value}})} /></div>
          <div className="form-group"><label>Password</label><input type="password" value={config.ibkr.password} onChange={e => setConfig({...config, ibkr: {...config.ibkr, password: e.target.value}})} /></div>
          <Tooltip text="Save and apply the new IBKR connection settings.">
            <button className="btn" onClick={handleSave} disabled={loading} style={{ width: '100%' }}>APPLY</button>
          </Tooltip>
        </section>
      </div>

      <header className="header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
          <h1>TSLA ALPHA COMMAND</h1>
          <div style={{ display: 'flex', gap: '1rem', backgroundColor: '#161b22', padding: '0.5rem 1rem', borderRadius: '8px', border: '1px solid #30363d' }}>
            <Tooltip text="Net Asset Value">
              <div><span style={{color:'#8b949e'}}>NAV:</span> ${portfolio.nav?.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
            </Tooltip>
            <Tooltip text="Available Cash">
              <div style={{color: '#238636'}}><span style={{color:'#8b949e'}}>CASH:</span> ${portfolio.cash?.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
            </Tooltip>
             <Tooltip text="Realized P&L">
              <div style={{color: portfolio.realized_pnl >= 0 ? '#238636' : '#da3633'}}><span style={{color:'#8b949e'}}>REALIZED:</span> ${portfolio.realized_pnl?.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
            </Tooltip>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            {brokerStatus && (
                <span style={{
                    padding: '0.25rem 0.75rem',
                    borderRadius: '4px',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    letterSpacing: '0.05em',
                    backgroundColor: brokerStatus.mode === 'live' ? '#da3633' : brokerStatus.mode === 'paper' ? '#1f6feb' : '#9e6a03',
                    color: 'white',
                    border: `1px solid ${brokerStatus.mode === 'live' ? '#f85149' : brokerStatus.mode === 'paper' ? '#388bfd' : '#d29922'}`
                }}>
                    {brokerStatus.mode === 'live' ? '⚠ LIVE' : brokerStatus.mode === 'paper' ? 'PAPER' : 'SIM MODE'}
                </span>
            )}
            <Link to="/"><Tooltip text="Dashboard"><Home className="hamburger" /></Tooltip></Link>
            <Link to="/architecture"><Tooltip text="Architecture"><Share2 className="hamburger" /></Tooltip></Link>
            <Link to="/gastown"><Tooltip text="Gastown Status"><Map className="hamburger" /></Tooltip></Link>
            <Tooltip text="Open Settings">
                <Menu className="hamburger" onClick={() => setShowSettings(true)} size={24} />
            </Tooltip>
        </div>
      </header>

      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard brokerStatus={brokerStatus} />} />
          <Route path="/architecture" element={<Architecture />} />
          <Route path="/gastown" element={<Gastown />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
