/**
 * MergedPositionsTable — Phase 18 UI Overhaul
 *
 * Combines Pending Orders + Open Positions into a single table.
 *
 * Status column:
 *   PENDING  (amber dot) — order submitted, awaiting fill
 *   OPEN     (blue dot)  — filled, position active
 *   CLOSING  (red dot)   — close/stop triggered, awaiting confirmation
 *   CANCELLED (grey)     — cancelled by user or system
 *
 * Actions:
 *   PENDING  → Cancel button
 *   OPEN     → Close Now button
 *   CLOSING  → Closing… (disabled)
 *
 * Color law: #00C853 = profit ONLY, #FF1744 = loss ONLY.
 */

import { useState, useEffect, useCallback } from 'react';
import './MergedPositionsTable.css';

// ── Types ─────────────────────────────────────────────────────────────────────

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
}

interface PendingOrdersResponse {
  active: PendingOrder[];
  cancelled: PendingOrder[];
  source: string;
  error?: string;
}

interface ManagedPosition {
  trade_id: number;
  entry_price: number;
  entry_time: string;
  quantity: number;
  direction: 'LONG' | 'SHORT';
  strategy: string;
  current_stop: number;
  target: number | null;
  trailing_engaged: boolean;
  time_stop_at: string;
}

type RowStatus = 'PENDING' | 'OPEN' | 'CLOSING' | 'CANCELLED';

interface TableRow {
  key: string;
  rowType: 'order' | 'position';
  status: RowStatus;
  contract: string;
  direction: string;
  entry: number | null;
  current: number | null;
  pnl: number | null;
  stop: number | null;
  target: number | null;
  timeLeft: string | null;
  orderId?: number;
  tradeId?: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUSD(v: number | null): string {
  if (v == null) return '—';
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}

function fmtPnL(v: number | null): { text: string; cls: string } {
  if (v == null) return { text: '—', cls: '' };
  const sign = v >= 0 ? '+' : '';
  const cls  = v > 0 ? 'pnl-profit' : v < 0 ? 'pnl-loss' : '';
  return { text: `${sign}${fmtUSD(v)}`, cls };
}

function fmtCountdown(iso: string): string {
  const remaining = Math.max(0, Math.floor((new Date(iso).getTime() - Date.now()) / 1000));
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function orderContract(o: PendingOrder): string {
  const strike = o.strike > 0 ? `$${o.strike}` : '';
  const type   = o.option_type ? o.option_type[0] : '';
  const exp    = o.expiry || '';
  return `${o.symbol} ${strike}${type} ${exp}`.trim();
}

// ── StatusDot ─────────────────────────────────────────────────────────────────

const StatusDot: React.FC<{ status: RowStatus }> = ({ status }) => (
  <span className={`mpt-status-dot mpt-status-${status.toLowerCase()}`} aria-hidden="true" />
);

// ── MergedPositionsTable ──────────────────────────────────────────────────────

export interface MergedPositionsTableProps {
  /** Optional: callback when a position is closed (e.g. to refresh parent count) */
  onClose?: () => void;
}

const MergedPositionsTable: React.FC<MergedPositionsTableProps> = ({ onClose }) => {
  const [orders, setOrders] = useState<PendingOrdersResponse | null>(null);
  const [positions, setPositions] = useState<ManagedPosition[]>([]);
  const [currentPrice, setCurrentPrice] = useState<number>(0);
  const [closing, setClosing] = useState<Set<number>>(new Set());
  const [cancelling, setCancelling] = useState<Set<number>>(new Set());
  const [tick, setTick] = useState(0);

  // 1-second tick for live countdowns
  useEffect(() => {
    const t = setInterval(() => setTick(v => v + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const fetchOrders = useCallback(async () => {
    try {
      const r = await fetch('/api/orders/pending');
      if (r.ok) setOrders(await r.json() as PendingOrdersResponse);
    } catch { /* silent */ }
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      const r = await fetch('/api/positions/managed');
      if (r.ok) {
        const d = await r.json();
        setPositions(d.positions ?? []);
        // Use latest bar close for P&L
        const bars = d.bars ?? [];
        if (bars.length > 0) setCurrentPrice(bars[bars.length - 1].close ?? 0);
      }
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchOrders(); fetchPositions();
    const ot = setInterval(fetchOrders, 30_000);
    const pt = setInterval(fetchPositions, 15_000);
    return () => { clearInterval(ot); clearInterval(pt); };
  }, [fetchOrders, fetchPositions]);

  // Keep tick in scope to force countdown re-renders
  void tick;

  const handleCancelOrder = async (orderId: number) => {
    setCancelling(prev => new Set([...prev, orderId]));
    try {
      await fetch(`/api/orders/${orderId}/cancel`, { method: 'POST' });
      await fetchOrders();
    } finally {
      setCancelling(prev => { const n = new Set(prev); n.delete(orderId); return n; });
    }
  };

  const handleClosePosition = async (tradeId: number) => {
    setClosing(prev => new Set([...prev, tradeId]));
    try {
      await fetch(`/api/positions/managed/${tradeId}/close`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exit_price: 0 }),
      });
      await fetchPositions();
      onClose?.();
    } finally {
      setClosing(prev => { const n = new Set(prev); n.delete(tradeId); return n; });
    }
  };

  // ── Build unified row list ────────────────────────────────────────────────

  const rows: TableRow[] = [];

  // Pending / cancelled orders
  for (const o of (orders?.active ?? [])) {
    const isFilled = o.filled_qty > 0 && o.filled_qty >= o.qty;
    const status: RowStatus = isFilled ? 'OPEN' : 'PENDING';
    rows.push({
      key: `order-${o.orderId}`,
      rowType: 'order',
      status,
      contract: orderContract(o),
      direction: o.action,
      entry: o.limit_price,
      current: null,
      pnl: null,
      stop: null,
      target: null,
      timeLeft: null,
      orderId: o.orderId,
    });
  }
  for (const o of (orders?.cancelled ?? [])) {
    rows.push({
      key: `order-cancelled-${o.orderId}`,
      rowType: 'order',
      status: 'CANCELLED',
      contract: orderContract(o),
      direction: o.action,
      entry: o.limit_price,
      current: null,
      pnl: null,
      stop: null,
      target: null,
      timeLeft: null,
      orderId: o.orderId,
    });
  }

  // Managed positions
  for (const p of positions) {
    const pnlPerUnit = currentPrice > 0
      ? (p.direction === 'LONG' ? currentPrice - p.entry_price : p.entry_price - currentPrice)
      : null;
    const pnlDollar = pnlPerUnit != null ? pnlPerUnit * p.quantity * 100 : null;

    rows.push({
      key: `pos-${p.trade_id}`,
      rowType: 'position',
      status: closing.has(p.trade_id) ? 'CLOSING' : 'OPEN',
      contract: `TSLA ${p.strategy} ${p.direction}`,
      direction: p.direction,
      entry: p.entry_price,
      current: currentPrice > 0 ? currentPrice : null,
      pnl: pnlDollar,
      stop: p.current_stop,
      target: p.target,
      timeLeft: p.time_stop_at ? fmtCountdown(p.time_stop_at) : null,
      tradeId: p.trade_id,
    });
  }

  const isEmpty = rows.length === 0;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div
      className="mpt-container"
      data-testid="merged-positions-table"
      id="positions-table"
    >
      <div className="mpt-header">
        <span className="mpt-title">POSITIONS &amp; ORDERS</span>
        <div className="mpt-legend">
          <span className="mpt-legend-item"><StatusDot status="PENDING" /> Pending</span>
          <span className="mpt-legend-item"><StatusDot status="OPEN" /> Open</span>
          <span className="mpt-legend-item"><StatusDot status="CLOSING" /> Closing</span>
          <span className="mpt-legend-item"><StatusDot status="CANCELLED" /> Cancelled</span>
        </div>
      </div>

      {isEmpty ? (
        <div className="mpt-empty" data-testid="mpt-empty">
          No positions or pending orders
        </div>
      ) : (
        <div className="mpt-table-wrapper">
          <table className="mpt-table" role="grid" aria-label="Positions and pending orders">
            <thead>
              <tr>
                <th>Status</th>
                <th>Contract</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Current</th>
                <th>P&amp;L</th>
                <th>Stop</th>
                <th>Target</th>
                <th>Time Left</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const { text: pnlText, cls: pnlCls } = fmtPnL(row.pnl);
                return (
                  <tr
                    key={row.key}
                    className={`mpt-row mpt-row-${row.status.toLowerCase()}`}
                    data-testid={`mpt-row-${row.status.toLowerCase()}`}
                  >
                    <td data-testid="mpt-status-cell">
                      <StatusDot status={row.status} />
                      <span className={`mpt-status-label mpt-status-${row.status.toLowerCase()}`}>
                        {row.status}
                      </span>
                    </td>
                    <td className="mpt-contract">{row.contract}</td>
                    <td className={`mpt-direction ${row.direction.toLowerCase()}`}>
                      {row.direction}
                    </td>
                    <td>{row.entry != null ? fmtUSD(row.entry) : '—'}</td>
                    <td>{row.current != null ? fmtUSD(row.current) : '—'}</td>
                    <td className={pnlCls} data-testid="mpt-pnl-cell">{pnlText}</td>
                    <td className="mpt-stop">{row.stop != null ? fmtUSD(row.stop) : '—'}</td>
                    <td className="mpt-target">{row.target != null ? fmtUSD(row.target) : '—'}</td>
                    <td className="mpt-timeleft">{row.timeLeft ?? '—'}</td>
                    <td className="mpt-actions">
                      {row.status === 'PENDING' && row.orderId != null && (
                        <button
                          className="mpt-btn-cancel"
                          disabled={cancelling.has(row.orderId)}
                          onClick={() => handleCancelOrder(row.orderId!)}
                          data-testid={`cancel-btn-${row.orderId}`}
                          aria-label={`Cancel order ${row.orderId}`}
                        >
                          {cancelling.has(row.orderId) ? '…' : 'Cancel'}
                        </button>
                      )}
                      {row.status === 'OPEN' && row.tradeId != null && (
                        <button
                          className="mpt-btn-close"
                          disabled={closing.has(row.tradeId)}
                          onClick={() => handleClosePosition(row.tradeId!)}
                          data-testid={`close-pos-btn-${row.tradeId}`}
                          aria-label={`Close position ${row.tradeId}`}
                        >
                          {closing.has(row.tradeId) ? 'Closing…' : 'Close Now'}
                        </button>
                      )}
                      {row.status === 'CLOSING' && (
                        <span className="mpt-closing-label">Closing…</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default MergedPositionsTable;
