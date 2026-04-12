import { useState, useEffect, useRef, useMemo } from 'react';
import { ChevronDown, ChevronRight, Server, Zap, FileText, HeartPulse, GitCommit } from 'lucide-react';
import './SystemMonitor.css';
import Tooltip from './Tooltip';

import { useDataFetching } from '../hooks';

// Collapsible Panel Component
const Panel = ({ title, icon, storageKey, children }: { title: string, icon: React.ReactNode, storageKey: string, children: React.ReactNode }) => {
    const [isOpen, setIsOpen] = useState(() => {
        const stored = localStorage.getItem('panel_' + storageKey);
        return stored === null ? true : stored === 'true';
    });

    const toggle = () => {
        const next = !isOpen;
        setIsOpen(next);
        localStorage.setItem('panel_' + storageKey, String(next));
    };

    return (
        <div className="monitor-panel">
            <div className="panel-header" onClick={toggle}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {icon}
                    <h3>{title}</h3>
                </div>
                {isOpen ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
            </div>
            {isOpen && <div className="panel-content">{children}</div>}
        </div>
    );
};

// Goroutine Modal
const GoroutineModal = ({ dump, onClose }: { dump: string, onClose: () => void }) => {
    const blocks = dump.split(/\n\n+/).filter(b => b.trim().length > 0);

    const getBlockClass = (block: string) => {
        if (block.includes('running')) return 'running';
        if (block.includes('IO wait') || block.includes('syscall')) return 'io';
        return 'waiting';
    };

    return (
        <div className="goroutine-modal" onClick={onClose}>
            <div className="goroutine-modal-content" onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                    <h3 style={{ margin: 0 }}>Goroutine Dump</h3>
                    <button className="btn" onClick={onClose}>× Close</button>
                </div>
                <div style={{ overflowY: 'auto', maxHeight: 'calc(80vh - 4rem)', fontFamily: 'monospace', fontSize: '0.78rem' }}>
                    {blocks.map((block, i) => (
                        <div key={i} className={`goroutine-block ${getBlockClass(block)}`}>
                            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{block}</pre>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

// Vitals Panel
const VitalsPanel = () => {
    const vitals = useDataFetching('/api/metrics/vitals', 3000, {});
    const nats = useDataFetching('/api/metrics/nats', 5000, {});
    const build = useDataFetching('/api/metrics/buildinfo', 30000, {});
    const [goroutineDump, setGoroutineDump] = useState<string | null>(null);

    const formatUptime = (seconds: number) => {
        if (!seconds) return '...';
        const d = Math.floor(seconds / (3600 * 24));
        const h = Math.floor(seconds % (3600 * 24) / 3600);
        const m = Math.floor(seconds % 3600 / 60);
        return `${d}d ${h}h ${m}m`;
    };

    const fetchGoroutines = async () => {
        try {
            const resp = await fetch('/api/metrics/goroutines');
            if (resp.ok) {
                setGoroutineDump(await resp.text());
            }
        } catch { /* ignore */ }
    };

    return (
        <>
            {goroutineDump && <GoroutineModal dump={goroutineDump} onClose={() => setGoroutineDump(null)} />}
            <Panel title="Vitals" icon={<HeartPulse size={16} />} storageKey="vitals">
                <div className="vitals-grid">
                    <VitalsCard label="Uptime" value={formatUptime(vitals.uptime_sec)} tooltip="How long the server has been running continuously" />
                    <VitalsCard label="CPU" value={`${vitals.cpu_pct?.toFixed(1) || 0}%`} tooltip="CPU usage %. Healthy: <70%. Warn: >70%, Alert: >90%" />
                    <VitalsCard
                        label="Memory"
                        value={vitals.mem_mb < 2 && vitals.heap_objects
                            ? `${vitals.mem_mb || 0} MB (~${Math.round((vitals.heap_objects || 0) / 1000)}k obj)`
                            : `${vitals.mem_mb || 0} MB`}
                        tooltip="Total Go heap memory in use (MB). Low values are normal for idle systems."
                    />
                    <VitalsCard label="Heap" value={`${vitals.heap_alloc || 0} MB`} tooltip="Live heap objects in MB. Rises before GC, drops after. Healthy: <500MB" />
                    <VitalsCard label="GoRoutines" value={vitals.goroutines || 0} tooltip="Number of active goroutines. Healthy: <500. High values indicate goroutine leaks" onClick={fetchGoroutines} />
                    <VitalsCard label="GC Pause" value={`${vitals.gc_pause_ms?.toFixed(3) || 0} ms`} tooltip="Cumulative GC pause time (ms). Low is good; high values indicate GC pressure" />
                    <VitalsCard label="Next GC" value={`@ ${vitals.next_gc || 0} MB`} tooltip="GC will trigger when heap reaches this size (MB)" />
                </div>
            </Panel>
            <Panel title="NATS" icon={<Zap size={16} />} storageKey="nats">
                <div className="vitals-grid">
                    <VitalsCard
                        label="NATS Status"
                        value={nats.connected ? "Connected" : "SIM MODE"}
                        tooltip={nats.connected
                            ? "Connected to NATS message bus"
                            : "NATS not running — system operating in offline simulation mode. Expected in development."}
                        valueClass={nats.connected ? undefined : 'warn-amber'}
                    />
                    <VitalsCard label="Server" value={nats.server_url || 'N/A'} />
                    <VitalsCard label="Msgs In" value={nats.msgs_in?.toLocaleString() || 0} tooltip="Messages received/sent over the NATS connection lifetime" />
                    <VitalsCard label="Msgs Out" value={nats.msgs_out?.toLocaleString() || 0} tooltip="Messages received/sent over the NATS connection lifetime" />
                    <VitalsCard label="Reconnects" value={nats.reconnects || 0} tooltip="Number of times the NATS connection was dropped and re-established. Healthy: 0" />
                </div>
            </Panel>
            <Panel title="Build" icon={<GitCommit size={16} />} storageKey="build">
                 <div className="vitals-grid">
                    <VitalsCard label="Go Version" value={build.go_version} />
                    <VitalsCard label="Build Time" value={build.build_time} />
                    <VitalsCard label="Git Commit" value={build.git_commit} />
                </div>
            </Panel>
        </>
    );
};

const VitalsCard = ({ label, value, tooltip, onClick, valueClass }: { label: string, value: string | number, tooltip?: string, onClick?: () => void, valueClass?: string }) => (
    <div className={`vitals-card${onClick ? ' clickable' : ''}`} onClick={onClick} title={onClick ? 'Click to inspect' : undefined}>
        <span className="vitals-label">
            {tooltip ? <Tooltip text={tooltip}>{label}</Tooltip> : label}
        </span>
        <span className={`vitals-value${valueClass ? ' ' + valueClass : ''}`}>{value}</span>
    </div>
);

// Request Rate Panel
const RequestRatePanel = () => {
    const requests = useDataFetching('/api/metrics/requests', 5000, []);
    const latency = useDataFetching('/api/metrics/latency', 5000, {});
    const vitals = useDataFetching('/api/metrics/vitals', 5000, {});
    const maxVal = Math.max(...requests, 1);

    const avg = requests.length > 0 ? (requests.reduce((a: number, b: number) => a + b, 0) / requests.length).toFixed(1) : '0';
    const peak = requests.length > 0 ? Math.max(...requests) : 0;

    // x-axis: requests array is 60 entries (newest first). Display labels every 10 bars.
    const xLabels: { index: number; label: string }[] = [];
    const totalBars = requests.length;
    for (let i = 0; i < totalBars; i++) {
        const secsAgo = totalBars - 1 - i; // index 0 = newest = 0s ago
        if (secsAgo === 0) {
            xLabels.push({ index: i, label: 'now' });
        } else if (secsAgo % 10 === 0) {
            xLabels.push({ index: i, label: `${secsAgo}s` });
        }
    }

    return (
        <Panel title="Request Rate" icon={<Server size={16} />} storageKey="requestrate">
            <div className="sparkline-axes-wrapper">
                <div className="sparkline-y-axis">
                    <span>{maxVal}</span>
                    <span>0</span>
                </div>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                    <div className="sparkline-container">
                        {requests.map((value: number, index: number) => (
                            <Tooltip key={index} text={`${value} req/s`}>
                                <div className="sparkline-bar" style={{ height: `${(value / maxVal) * 100}%` }}/>
                            </Tooltip>
                        ))}
                    </div>
                    <div className="sparkline-x-labels">
                        {xLabels.map(({ index, label }) => (
                            <span key={index} style={{ position: 'absolute', left: `${(index / (totalBars - 1)) * 100}%`, transform: 'translateX(-50%)' }}>{label}</span>
                        ))}
                    </div>
                </div>
            </div>
            <div className="sparkline-summary">
                Avg: {avg} req/s | Peak: {peak} req/s | Total: {vitals.total_requests?.toLocaleString() || 0}
            </div>
            <div className="latency-stats vitals-grid">
                <VitalsCard label="Total Reqs" value={vitals.total_requests?.toLocaleString() || 0} />
                <VitalsCard label="p50" value={`${latency.p50?.toFixed(2) || 0}ms`} />
                <VitalsCard label="p95" value={`${latency.p95?.toFixed(2) || 0}ms`} />
                <VitalsCard label="p99" value={`${latency.p99?.toFixed(2) || 0}ms`} />
            </div>
        </Panel>
    );
};

// Signal Panel
const SignalPanel = () => {
    const signals = useDataFetching('/api/metrics/signals', 10000, []);
    const breakdown = useDataFetching('/api/metrics/signals/breakdown', 10000, {});
    const vitals = useDataFetching('/api/metrics/vitals', 10000, {});
    const recentSignals = useDataFetching('/api/signals/all', 10000, []);
    const [tableView, setTableView] = useState(false);
    const maxVal = Math.max(...signals, 1);

    return (
        <Panel title="Signal Throughput" icon={<Zap size={16} />} storageKey="signals">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                <p style={{ margin: 0 }}>Total: {vitals.total_signals?.toLocaleString() || 0}</p>
                <button
                    className={`btn${tableView ? ' active' : ''}`}
                    style={{ fontSize: '0.7rem', padding: '2px 8px' }}
                    onClick={() => setTableView(v => !v)}
                >
                    {tableView ? 'Graph' : 'Table'}
                </button>
            </div>
            {tableView ? (
                <div className="signal-table-wrap">
                    <table className="signal-table">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Direction</th>
                                <th>Conf</th>
                                <th>Strike</th>
                                <th>Type</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(Array.isArray(recentSignals) ? recentSignals : []).slice(0, 20).map((s: Record<string, unknown>, i: number) => {
                                const ts = s.timestamp as number;
                                const conf = s.confidence as number;
                                const dir = s.direction as string;
                                const strike = s.recommended_strike as number;
                                const optType = s.option_type as string;
                                const isIdle = (s.strategy_code as string) === 'IDLE_SCAN';
                                return (
                                    <tr key={i} className={isIdle ? 'signal-row-idle' : dir === 'BULLISH' ? 'signal-row-bull' : 'signal-row-bear'}>
                                        <td>{ts ? new Date(ts * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '—'}</td>
                                        <td className={dir === 'BULLISH' ? 'sc-green' : dir === 'BEARISH' ? 'sc-red' : ''}>{dir || '—'}</td>
                                        <td>{conf !== undefined ? `${(conf * 100).toFixed(0)}%` : '—'}</td>
                                        <td>{strike ? `$${strike.toFixed(0)}` : '—'}</td>
                                        <td>{optType || '—'}</td>
                                        <td className={isIdle ? 'signal-status-idle' : 'signal-status-pub'}>
                                            {isIdle ? 'SCAN' : 'PUB'}
                                        </td>
                                    </tr>
                                );
                            })}
                            {(!Array.isArray(recentSignals) || recentSignals.length === 0) && (
                                <tr><td colSpan={6} style={{ textAlign: 'center', color: '#484f58', padding: '12px' }}>No signals yet</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            ) : (
                <>
                    <div className="sparkline-container">
                        {signals.map((value: number, index: number) => (
                            <Tooltip key={index} text={`${value} sig/min`}>
                                <div className="sparkline-bar signal-bar" style={{ height: `${(value / maxVal) * 100}%` }}/>
                            </Tooltip>
                        ))}
                    </div>
                    <div className="vitals-grid" style={{marginTop: '1rem'}}>
                        {Object.entries(breakdown).map(([strategy, count]) => (
                            <VitalsCard key={strategy} label={strategy} value={count as number} />
                        ))}
                    </div>
                </>
            )}
        </Panel>
    );
};

// Log Panel
const LogPanel = () => {
    const logs = useDataFetching('/api/logs', 3000, '');
    const [filter, setFilter] = useState('');
    const [autoScroll, setAutoScroll] = useState(true);
    const viewerRef = useRef<HTMLDivElement>(null);

    const getLogLevelClass = (line: string) => {
        if (line.includes('ERROR')) return 'level-error';
        if (line.includes('WARN')) return 'level-warn';
        if (line.includes('INFO')) return 'level-info';
        if (line.includes('DEBUG')) return 'level-debug';
        return '';
    };

    const filteredLogs = useMemo(() => {
        if (!filter) return logs;
        try {
            const regex = new RegExp(filter, 'i');
            return logs.split(`
`).filter((line:string) => regex.test(line)).join(`
`);
        } catch (e) {
            return 'Invalid Regex';
        }
    }, [logs, filter]);

    useEffect(() => {
        if (autoScroll && viewerRef.current) {
            viewerRef.current.scrollTop = viewerRef.current.scrollHeight;
        }
    }, [filteredLogs, autoScroll]);

    return (
        <Panel title="Live Log" icon={<FileText size={16} />} storageKey="livelog">
            <div className="log-controls">
                <input
                    type="text"
                    placeholder="Filter logs (regex)..."
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                />
                <button onClick={() => setAutoScroll(!autoScroll)} className={`btn ${autoScroll ? 'active' : ''}`}>
                    Auto-Scroll
                </button>
            </div>
            <div ref={viewerRef} className="log-viewer">
                {filteredLogs.split(`
`).map((line:string, i:number) => (
                    <div key={i} className={`log-line ${getLogLevelClass(line)}`}>{line}</div>
                ))}
            </div>
        </Panel>
    );
};


const SystemMonitor = () => {
    const [isExpanded, setIsExpanded] = useState(() => {
        return localStorage.getItem('systemMonitorExpanded') === 'true';
    });

    const vitals = useDataFetching('/api/metrics/vitals', 3000, {});
    const signals = useDataFetching('/api/metrics/signals', 10000, []);
    const requests = useDataFetching('/api/metrics/requests', 5000, []);

    const toggleExpand = () => {
        const newState = !isExpanded;
        setIsExpanded(newState);
        localStorage.setItem('systemMonitorExpanded', JSON.stringify(newState));
    };

    const formatUptime = (seconds: number) => {
        if (!seconds) return '...';
        const d = Math.floor(seconds / (3600 * 24));
        const h = Math.floor(seconds % (3600 * 24) / 3600);
        const m = Math.floor(seconds % 3600 / 60);
        return `${d}d ${h}h ${m}m`;
    };

    // Determine health status
    const healthStatus = useMemo(() => {
        if (!vitals.cpu_pct) return 'yellow'; // Not ready
        if (vitals.cpu_pct > 90 || vitals.mem_mb > 1000) return 'red';
        if (vitals.cpu_pct > 70 || vitals.mem_mb > 500) return 'yellow';
        return 'green';
    }, [vitals]);

    if (!isExpanded) {
        return (
            <div className="system-monitor-compact" onClick={toggleExpand}>
                <Tooltip text={`System Health: ${healthStatus}`}>
                    <div className={`health-dot ${healthStatus}`} />
                </Tooltip>
                <CompactStat label="Uptime" value={formatUptime(vitals.uptime_sec)} />
                <CompactStat label="GoRoutines" value={vitals.goroutines} />
                <CompactStat label="Mem" value={`${vitals.mem_mb}MB`} />
                <CompactStat label="CPU" value={`${vitals.cpu_pct?.toFixed(1)}%`} />
                <CompactStat label="Req/s" value={requests[0] || 0} />
                <CompactStat label="Sig/min" value={signals[0] || 0} />
            </div>
        );
    }

    return (
        <div className="monitor-expanded">
            <div className="system-monitor-compact" onClick={toggleExpand}>
                 <Tooltip text={`System Health: ${healthStatus}`}>
                    <div className={`health-dot ${healthStatus}`} />
                </Tooltip>
                <h2 style={{margin: 0, fontSize: '1rem'}}>SYSTEM MONITOR</h2>
            </div>
            <div style={{marginTop: '1rem'}}>
                <VitalsPanel />
                <RequestRatePanel />
                <SignalPanel />
                <LogPanel />
            </div>
        </div>
    );
};

const CompactStat = ({ label, value }: { label: string, value: any }) => (
    <div className="compact-stat">
        <span className="label">{label}</span>
        <span className="value">{value || '...'}</span>
    </div>
);


export default SystemMonitor;
