import React, { useState } from 'react';
import { useDataFetching } from '../hooks';
import './Architecture.css';
import { Cpu, MessageSquare, Layers } from 'lucide-react';

type StatusColor = 'green' | 'yellow' | 'red';

const StatusDot = ({ color }: { color: StatusColor }) => (
    <span className={`status-dot ${color}`} />
);

interface DrawerData {
    title: string;
    metrics: { label: string; value: string | number }[];
}

const DetailDrawer = ({ data, onClose }: { data: DrawerData | null; onClose: () => void }) => (
    <div className={`detail-drawer ${data ? 'open' : ''}`}>
        {data && (
            <>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
                    <h2 style={{ margin: 0, fontSize: '1rem' }}>{data.title}</h2>
                    <button className="btn" onClick={onClose}>×</button>
                </div>
                {data.metrics.map((m, i) => (
                    <div key={i} className="detail-metric">
                        <span>{m.label}</span>
                        <span>{m.value}</span>
                    </div>
                ))}
            </>
        )}
    </div>
);

const LayerCard = ({
    title, children, icon, status, onClick
}: {
    title: string;
    children: React.ReactNode;
    icon: React.ReactNode;
    status: StatusColor;
    onClick: () => void;
}) => (
    <div className="layer-card" style={{ cursor: 'pointer' }} onClick={onClick}>
        <div className="layer-header">
            {icon}
            <h2>{title}</h2>
            <StatusDot color={status} />
        </div>
        <div className="layer-content">
            {children}
        </div>
    </div>
);

const Architecture = () => {
    const signals = useDataFetching('/api/signals/all', 3000, []);
    const nats = useDataFetching('/api/metrics/nats', 3000, {});
    const vitals = useDataFetching('/api/metrics/vitals', 3000, {});
    const portfolio = useDataFetching('/api/portfolio', 5000, {});

    const [drawer, setDrawer] = useState<DrawerData | null>(null);

    // Status logic
    const alphaStatus: StatusColor = (() => {
        if (!signals || signals.length === 0) return 'red';
        const lastTs = signals[0]?.timestamp;
        if (!lastTs) return 'red';
        const ageSec = Date.now() / 1000 - lastTs;
        return ageSec < 300 ? 'green' : 'yellow';
    })();

    const natsStatus: StatusColor = nats.connected ? 'green' : 'red';

    const execStatus: StatusColor = (() => {
        const cpu = vitals.cpu_pct ?? 0;
        if (cpu >= 90) return 'red';
        if (cpu >= 70) return 'yellow';
        return 'green';
    })();

    const frontendStatus: StatusColor = 'green';

    // ... logic to parse signals and determine model activity ...
    const models: Record<string, { lastSignal: string; direction: string; confidence: number }> = {
        MACRO: { lastSignal: '...', direction: '...', confidence: 0 },
        MOMENTUM: { lastSignal: '...', direction: '...', confidence: 0 },
        MEAN_REVERT: { lastSignal: '...', direction: '...', confidence: 0 },
        VOLATILITY: { lastSignal: '...', direction: '...', confidence: 0 },
    };

    if (signals && signals.length > 0) {
        for (const model of Object.keys(models)) {
            const modelSignal = signals.find((s: any) => s.model_id === model);
            if (modelSignal) {
                models[model] = {
                    lastSignal: new Date(modelSignal.timestamp * 1000).toLocaleTimeString(),
                    direction: modelSignal.direction,
                    confidence: modelSignal.confidence,
                };
            }
        }
    }

    const openDrawer = (title: string, metrics: { label: string; value: string | number }[]) => {
        setDrawer({ title, metrics });
    };

    const formatUptime = (s: number) => {
        if (!s) return 'N/A';
        const d = Math.floor(s / 86400);
        const h = Math.floor((s % 86400) / 3600);
        const m = Math.floor((s % 3600) / 60);
        return `${d}d ${h}h ${m}m`;
    };

    return (
        <div className="architecture-page">
            {drawer && <div className="drawer-backdrop" onClick={() => setDrawer(null)} />}
            <DetailDrawer data={drawer} onClose={() => setDrawer(null)} />

            <h1>System Architecture</h1>
            <div className="architecture-grid">
                <LayerCard
                    title="ALPHA ENGINE (Python)"
                    icon={<Layers />}
                    status={alphaStatus}
                    onClick={() => openDrawer('Alpha Engine', [
                        { label: 'Status', value: alphaStatus.toUpperCase() },
                        { label: 'Total Signals', value: signals?.length ?? 0 },
                        { label: 'Last Signal', value: signals[0] ? new Date(signals[0].timestamp * 1000).toLocaleString() : 'N/A' },
                        { label: 'Last Direction', value: signals[0]?.direction ?? 'N/A' },
                        { label: 'Last Confidence', value: signals[0] ? `${(signals[0].confidence * 100).toFixed(1)}%` : 'N/A' },
                        { label: 'Last Strategy', value: signals[0]?.strategy_code ?? 'N/A' },
                    ])}
                >
                    {Object.entries(models).map(([name, data]) => (
                        <div key={name} className="component-card">
                            <strong>{name}</strong>
                            <div>Last Signal: {data.lastSignal}</div>
                            <div>Direction: {data.direction}</div>
                            <div>Confidence: {(data.confidence * 100).toFixed(1)}%</div>
                        </div>
                    ))}
                </LayerCard>

                <div className="arrow">↓</div>

                <LayerCard
                    title="NATS MESSAGE BUS"
                    icon={<MessageSquare />}
                    status={natsStatus}
                    onClick={() => openDrawer('NATS Message Bus', [
                        { label: 'Connected', value: nats.connected ? 'Yes' : 'No' },
                        { label: 'Server URL', value: nats.server_url || 'N/A' },
                        { label: 'Msgs In', value: nats.msgs_in?.toLocaleString() ?? 0 },
                        { label: 'Msgs Out', value: nats.msgs_out?.toLocaleString() ?? 0 },
                        { label: 'Bytes In', value: nats.bytes_in?.toLocaleString() ?? 0 },
                        { label: 'Bytes Out', value: nats.bytes_out?.toLocaleString() ?? 0 },
                        { label: 'Reconnects', value: nats.reconnects ?? 0 },
                    ])}
                >
                     <div className="component-card">
                        <strong>Topic: tsla.alpha.signals</strong>
                        <div>Msg/s: {nats.msgs_in ? (nats.msgs_in / 60).toFixed(2) : 0}</div>
                        <div>Total Msgs: {nats.msgs_in?.toLocaleString()}</div>
                    </div>
                </LayerCard>

                <div className="arrow">↓</div>

                <LayerCard
                    title="EXECUTION ENGINE (Go)"
                    icon={<Cpu />}
                    status={execStatus}
                    onClick={() => openDrawer('Execution Engine', [
                        { label: 'CPU', value: `${vitals.cpu_pct?.toFixed(1) ?? 0}%` },
                        { label: 'Memory', value: `${vitals.mem_mb ?? 0} MB` },
                        { label: 'Heap', value: `${vitals.heap_alloc ?? 0} MB` },
                        { label: 'GoRoutines', value: vitals.goroutines ?? 0 },
                        { label: 'Uptime', value: formatUptime(vitals.uptime_sec) },
                        { label: 'Total Requests', value: vitals.total_requests?.toLocaleString() ?? 0 },
                        { label: 'Total Signals', value: vitals.total_signals?.toLocaleString() ?? 0 },
                        { label: 'GC Pause', value: `${vitals.gc_pause_ms?.toFixed(3) ?? 0} ms` },
                        { label: 'Next GC', value: `${vitals.next_gc ?? 0} MB` },
                    ])}
                >
                    <div className="component-card">
                        <strong>API Server</strong>
                        <div>Uptime: {vitals.uptime_sec && new Date(vitals.uptime_sec * 1000).toISOString().substr(11, 8)}</div>
                        <div>CPU: {vitals.cpu_pct?.toFixed(1) ?? 0}%</div>
                        <div>GoRoutines: {vitals.goroutines}</div>
                    </div>
                </LayerCard>

                <div className="arrow">↓</div>

                <LayerCard
                    title="FRONTEND (React/Vite)"
                    icon={<Layers />}
                    status={frontendStatus}
                    onClick={() => openDrawer('Frontend', [
                        { label: 'Status', value: 'Connected' },
                        { label: 'NAV', value: portfolio.nav ? `$${portfolio.nav.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : 'N/A' },
                        { label: 'Cash', value: portfolio.cash ? `$${portfolio.cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : 'N/A' },
                        { label: 'Realized P&L', value: portfolio.realized_pnl !== undefined ? `$${portfolio.realized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : 'N/A' },
                    ])}
                >
                     <div className="component-card">
                        <strong>Alpha Control Center</strong>
                        <div>Status: <span style={{color: 'green'}}>Connected</span></div>
                    </div>
                </LayerCard>
            </div>
        </div>
    );
};

export default Architecture;
