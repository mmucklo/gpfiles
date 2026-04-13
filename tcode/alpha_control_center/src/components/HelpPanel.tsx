import './HelpPanel.css';

interface HelpPanelProps {
  onClose: () => void;
}

const PANEL_DOCS = [
  {
    icon: '💼',
    name: 'Portfolio Command Bar',
    location: 'Top — sticky row',
    description: 'Real-time NAV, cash, buying power, unrealized and realized P&L from the active broker account. Click MODE to toggle between paper trading (IBKR) and simulation.',
    interactive: 'Click any pill for a detailed drill-down. Click MODE to toggle paper/simulation.',
  },
  {
    icon: '🔗',
    name: 'Data Sources (Provenance)',
    location: 'Below portfolio bar',
    description: 'Shows live TradingView vs yfinance spot prices side-by-side with divergence %. If divergence exceeds 0.5%, trading is halted.',
    interactive: 'Hover each badge for age info. Open a signal modal to refresh live spot audit.',
  },
  {
    icon: '⚡',
    name: 'Signal Command',
    location: 'Left column',
    description: 'Live high-conviction trade signals from the Alpha Engine. Each card shows direction, contract, confidence %, limit/exit/stop prices, and Kelly criterion sizing.',
    interactive: 'Click any signal card to open a full breakdown with chain data and data provenance.',
  },
  {
    icon: '🏛',
    name: 'Trading Floor',
    location: 'Center column',
    description: 'Open positions — either from the live IBKR paper account or the simulation portfolio. Each position card shows entry, current price, and unrealized P&L.',
    interactive: 'Click any position card to see a detailed P&L ledger: qty, avg cost, current, market value, Greeks.',
  },
  {
    icon: '📋',
    name: 'Execution Log',
    location: 'Right column',
    description: 'All executed orders with realized P&L. Losing trades have an ANALYZE button for root-cause tagging. Win rate and total P&L are summarized at the bottom.',
    interactive: 'Click any trade row for fill details. Click ANALYZE on losses to tag exit reasons.',
  },
  {
    icon: '🔭',
    name: 'Market Intelligence',
    location: 'Bottom strip',
    description: 'VIX + SPY market pulse, TSLA news sentiment (NLP score), options flow P/C ratio, and earnings proximity watch. All influence signal confidence.',
    interactive: 'Hover each card title for a full explanation. Collapses to save space.',
  },
  {
    icon: '📊',
    name: 'Model Scorecard',
    location: 'Bottom strip',
    description: 'Per-model performance statistics: trade count, win rate, avg P&L, total P&L, Sharpe ratio, high-confidence win rate, and top loss tag.',
    interactive: 'Hover column headers for definitions. Collapses to save space.',
  },
  {
    icon: '📈',
    name: 'TSLA Chart',
    location: 'Bottom strip',
    description: 'Embedded TradingView chart for live TSLA price action. Use for visual confirmation of signal entry/exit levels.',
    interactive: 'Fully interactive TradingView widget. Collapses to save space.',
  },
  {
    icon: '🖥',
    name: 'System Monitor',
    location: 'Bottom strip',
    description: 'Alpha Engine vitals: uptime, CPU, memory, goroutines, NATS message bus stats, and build info. Shows backend health at a glance.',
    interactive: 'Click goroutine count to dump the full goroutine stack. Collapses to save space.',
  },
  {
    icon: '🛡',
    name: 'Integrity Status',
    location: 'Header bar (right)',
    description: 'Three independent integrity indicators: PRICE (multi-source spot divergence), CHAIN (options data freshness), EXEC (broker connection + fill audit). Any RED indicator blocks new trades.',
    interactive: 'Click any indicator to open a detailed breakdown with exact values and rule explanations.',
  },
];

const HelpPanel = ({ onClose }: HelpPanelProps) => (
  <div className="help-panel-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Dashboard Help">
    <div className="help-panel" onClick={e => e.stopPropagation()}>
      <div className="help-panel-header">
        <span>WHAT'S ON THIS SCREEN?</span>
        <button onClick={onClose} aria-label="Close help panel">×</button>
      </div>
      <p className="help-panel-intro">
        TSLA Alpha Command Center — autonomous options trading dashboard. All values trace to live API data.
        Click any panel name below for usage notes.
      </p>
      <div className="help-panel-list">
        {PANEL_DOCS.map(p => (
          <div key={p.name} className="help-panel-item">
            <div className="help-panel-item-header">
              <span className="help-panel-icon" aria-hidden="true">{p.icon}</span>
              <div>
                <div className="help-panel-item-name">{p.name}</div>
                <div className="help-panel-item-location">{p.location}</div>
              </div>
            </div>
            <p className="help-panel-item-desc">{p.description}</p>
            <p className="help-panel-item-interactive">
              <span className="help-interactive-label">Interactive:</span> {p.interactive}
            </p>
          </div>
        ))}
      </div>
      <div className="help-panel-footer">
        <span>All numeric values are clickable — they expand to show source, computation, timestamp, and raw inputs.</span>
      </div>
    </div>
  </div>
);

export default HelpPanel;
