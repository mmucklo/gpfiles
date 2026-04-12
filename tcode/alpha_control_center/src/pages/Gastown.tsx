import { useState, useEffect, useRef, useCallback } from 'react';
import './Gastown.css';

// ============================================================
//  Types
// ============================================================
interface AgentInfo {
    name: string;
    address: string;
    session: string;
    role: string;
    running: boolean;
    acp: boolean;
    has_work: boolean;
    unread_mail: number;
    agent_alias: string;
    agent_info: string;
}

interface RigInfo {
    name: string;
    polecats: AgentInfo[] | null;
    polecat_count: number;
    crews: AgentInfo[] | null;
    crew_count: number;
    has_witness: boolean;
    has_refinery: boolean;
}

interface GTStatus {
    name: string;
    location: string;
    overseer: { name: string; username: string; unread_mail: number };
    dnd: { enabled: boolean; level: string };
    daemon: { running: boolean };
    dolt: { running: boolean; port: number; data_dir: string };
    tmux: { socket: string; socket_path: string; running: boolean; pid: number; session_count: number };
    agents: AgentInfo[];
    rigs: RigInfo[];
    summary: {
        rig_count: number;
        polecat_count: number;
        crew_count: number;
        witness_count: number;
        refinery_count: number;
        active_hooks: number;
    };
}

interface AgentDetail {
    hook: string;
    heartbeat: string;
    mail_count: number;
    last_active: string;
}

interface PatrolConfig {
    heartbeat?: { enabled: boolean; interval: string };
    patrols?: {
        [key: string]: { enabled: boolean; interval: string; agent: string; rigs?: string[] };
    };
}

interface EscalationConfig {
    routes?: { [level: string]: string[] };
    stale_threshold?: string;
    max_reescalations?: number;
}

interface ReadyIssue {
    id: string;
    title: string;
    description?: string;
    status: string;
    priority: number;
    assignee?: string;
    created_at?: string;
    updated_at?: string;
    source?: string;
}

interface ReadyData {
    sources?: { name: string; issues: ReadyIssue[] | null; error?: string }[];
    summary?: {
        total: number;
        by_source: { [k: string]: number };
        p0_count: number;
        p1_count: number;
    };
}

interface GastownData {
    status: GTStatus | null;
    log: string[];
    ready: ReadyData | null;
    agents_detail: { [name: string]: AgentDetail };
    patrols: PatrolConfig | null;
    tmux_sessions: string[];
    escalation: EscalationConfig | null;
    git_info: { branch: string; last_commit: string };
    refreshed_at: string;
}

// ============================================================
//  Helpers
// ============================================================
const timeAgo = (isoStr: string): string => {
    if (!isoStr) return 'N/A';
    try {
        const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
        if (diff < 60) return `${Math.floor(diff)}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    } catch { return 'N/A'; }
};

const priorityLabel = (p: number) => ['P0', 'P1', 'P2', 'P3', 'P4'][p] ?? `P${p}`;
const priorityClass = (p: number) => ['p0', 'p1', 'p2', 'p3', 'p4'][p] ?? 'p4';

const detectLogEvent = (line: string): { type: string; icon: string } => {
    const l = line.toLowerCase();
    if (l.includes('spawn')) return { type: 'spawn', icon: '🟢' };
    if (l.includes('wake')) return { type: 'wake', icon: '🔵' };
    if (l.includes('nudge')) return { type: 'nudge', icon: '🟡' };
    if (l.includes('handoff')) return { type: 'handoff', icon: '🔄' };
    if (l.includes('done') || l.includes('completed') || l.includes('closed')) return { type: 'done', icon: '✅' };
    if (l.includes('crash') || l.includes('error') || l.includes('failed')) return { type: 'crash', icon: '🔴' };
    if (l.includes('kill') || l.includes('stopped') || l.includes('halt')) return { type: 'kill', icon: '⛔' };
    return { type: 'info', icon: '▪' };
};

const getRoleDisplayName = (role: string): string => {
    const map: { [k: string]: string } = {
        coordinator: 'COORD',
        'health-check': 'HEALTH',
        witness: 'WITNESS',
        refinery: 'REFINERY',
        polecat: 'POLECAT',
        crew: 'CREW',
    };
    return map[role] ?? role.toUpperCase().slice(0, 8);
};

// ============================================================
//  Agent Detail Modal
// ============================================================
const AgentModal = ({
    agent, detail, onClose
}: {
    agent: AgentInfo;
    detail: AgentDetail | undefined;
    onClose: () => void;
}) => (
    <div className="agent-modal-overlay" onClick={onClose}>
        <div className="agent-modal" onClick={e => e.stopPropagation()}>
            <div className="agent-modal-header">
                <span className="agent-modal-title">{agent.name}</span>
                <button className="btn-close" onClick={onClose}>×</button>
            </div>

            <div className="modal-section">
                <div className="modal-section-title">Status</div>
                <div className="modal-field"><span className="modal-field-key">Running</span><span className={`modal-field-val ${agent.running ? 'active' : ''}`}>{agent.running ? 'Yes' : 'No'}</span></div>
                <div className="modal-field"><span className="modal-field-key">Role</span><span className="modal-field-val">{agent.role}</span></div>
                <div className="modal-field"><span className="modal-field-key">Model</span><span className="modal-field-val">{agent.agent_alias}</span></div>
                <div className="modal-field"><span className="modal-field-key">Session</span><span className="modal-field-val">{agent.session}</span></div>
                <div className="modal-field"><span className="modal-field-key">ACP</span><span className="modal-field-val">{agent.acp ? 'Active' : 'Inactive'}</span></div>
                <div className="modal-field"><span className="modal-field-key">Has Work</span><span className="modal-field-val">{agent.has_work ? 'Yes' : 'No'}</span></div>
                <div className="modal-field"><span className="modal-field-key">Unread Mail</span><span className="modal-field-val">{agent.unread_mail}</span></div>
            </div>

            {detail && (
                <div className="modal-section">
                    <div className="modal-section-title">Filesystem</div>
                    <div className="modal-field">
                        <span className="modal-field-key">Hook</span>
                        <span className={`modal-field-val ${!detail.hook ? 'empty' : ''}`}>{detail.hook || 'IDLE'}</span>
                    </div>
                    <div className="modal-field">
                        <span className="modal-field-key">Last Active</span>
                        <span className="modal-field-val">{detail.last_active ? timeAgo(detail.last_active) : 'N/A'}</span>
                    </div>
                </div>
            )}

            <div className="modal-section">
                <div className="modal-section-title">Address</div>
                <div className="modal-field"><span className="modal-field-key">Address</span><span className="modal-field-val">{agent.address}</span></div>
            </div>
        </div>
    </div>
);

// ============================================================
//  Zone 1 — Town Header
// ============================================================
const TownHeader = ({
    data, onRefresh, refreshing, simMode, onSimToggle
}: {
    data: GastownData;
    onRefresh: () => void;
    refreshing: boolean;
    simMode?: string;
    onSimToggle?: () => void;
}) => {
    const status = data.status;
    if (!status) return <div className="town-header"><span style={{ color: '#484f58' }}>Loading...</span></div>;

    const daemonUp = status.daemon?.running;
    const doltUp = status.dolt?.running;
    const tmuxUp = status.tmux?.running;

    const healthColor = (!daemonUp || !doltUp) ? 'red' : (!tmuxUp ? 'yellow' : 'green');

    const totalAgents = (status.agents?.length ?? 0) +
        (status.rigs?.reduce((a, r) => a + (r.polecat_count ?? 0) + (r.crew_count ?? 0), 0) ?? 0);

    const totalMail = (status.overseer?.unread_mail ?? 0) +
        (status.agents?.reduce((a, ag) => a + (ag.unread_mail ?? 0), 0) ?? 0);

    return (
        <div className="town-header">
            <div className="town-name-section">
                <div className={`health-dot-lg ${healthColor}`} title={`Health: ${healthColor}`} />
                <div>
                    <div className="town-name">⚙ {status.name ?? 'Gas Town'}</div>
                    <div className="town-location">{status.location ?? '/home/builder/gt'}</div>
                </div>
            </div>

            <div className="service-pills">
                <div className={`service-pill ${daemonUp ? 'up' : 'down'}`} title="Background patrol daemon">
                    <div className="service-pill-dot" />
                    DAEMON
                </div>
                <div className={`service-pill ${doltUp ? 'up' : 'down'}`} title="Dolt database server">
                    <div className="service-pill-dot" />
                    DOLT :{status.dolt?.port ?? 3307}
                </div>
                <div className={`service-pill ${tmuxUp ? 'up' : 'down'}`} title="tmux session manager">
                    <div className="service-pill-dot" />
                    TMUX ({status.tmux?.session_count ?? 0})
                </div>
            </div>

            <div className="town-stats">
                <div className="town-stat">
                    <span className="town-stat-label">Rigs</span>
                    <span className="town-stat-value highlight">{status.summary?.rig_count ?? 0}</span>
                </div>
                <div className="town-stat">
                    <span className="town-stat-label">Agents</span>
                    <span className="town-stat-value highlight">{totalAgents}</span>
                </div>
                <div className="town-stat">
                    <span className="town-stat-label">Hooks</span>
                    <span className="town-stat-value highlight">{status.summary?.active_hooks ?? 0}</span>
                </div>
                <div className="town-stat">
                    <span className="town-stat-label">Mail</span>
                    <span className={`town-stat-value ${totalMail > 0 ? 'warn' : ''}`}>{totalMail}</span>
                </div>
            </div>

            <div className="header-actions">
                {simMode !== undefined && onSimToggle && (
                    <button
                        className={`sim-mode-toggle ${simMode === 'paper' ? 'paper' : 'sim'}`}
                        onClick={onSimToggle}
                        title={`Currently ${simMode.toUpperCase()} — click to toggle`}
                    >
                        MODE: {simMode.toUpperCase()}
                    </button>
                )}
                <span className="refresh-time">
                    {data.refreshed_at ? `Updated ${timeAgo(data.refreshed_at)}` : ''}
                </span>
                <button
                    className={`btn-refresh ${refreshing ? 'spinning' : ''}`}
                    onClick={onRefresh}
                    title="Refresh now"
                >
                    ↻ Refresh
                </button>
            </div>
        </div>
    );
};

// ============================================================
//  Zone 2 — Agent Grid
// ============================================================
const AgentCard = ({
    agent, detail, tmuxSessions, onClick
}: {
    agent: AgentInfo;
    detail: AgentDetail | undefined;
    tmuxSessions: string[];
    onClick: () => void;
}) => {
    // Detect if agent is running in an alt session (e.g. "tsla-claude" for mayor)
    const sessionMatch = tmuxSessions.some(s =>
        s.includes(agent.name) ||
        (agent.name === 'mayor' && s.includes('tsla-claude')) ||
        (agent.name === 'deacon' && s.includes('tsla-claude'))
    );
    const isAltRunning = !agent.running && sessionMatch;

    const statusClass = agent.running && agent.has_work
        ? 'running-working'
        : agent.running
            ? 'running-idle'
            : isAltRunning
                ? 'alt-running'
                : 'stopped';

    const cardClass = agent.has_work ? 'has-work' : (agent.running || isAltRunning) ? 'running' : 'stopped';
    const statusTitle = agent.running
        ? (agent.has_work ? 'Running — has work' : 'Running — idle')
        : isAltRunning
            ? 'Running in alternate tmux session'
            : 'Stopped';

    return (
        <div className={`agent-card ${cardClass}`} onClick={onClick} title="Click to view details">
            <div className="agent-card-header">
                <div className={`agent-status-indicator ${statusClass}`} title={statusTitle} />
                <div className="agent-name-section">
                    <div className="agent-name">{agent.name}</div>
                    <div className="agent-badges">
                        <span className="badge badge-role" title="Agent role">{getRoleDisplayName(agent.role)}</span>
                        <span className={`badge badge-model ${agent.agent_alias === 'gemini' ? 'gemini' : ''}`}
                            title="AI model">
                            {agent.agent_alias ?? 'claude'}
                        </span>
                    </div>
                </div>
                {agent.unread_mail > 0 && (
                    <span className="mail-badge" title={`${agent.unread_mail} unread messages`}>
                        {agent.unread_mail}
                    </span>
                )}
            </div>
            <div className="agent-card-body">
                <div className="agent-field">
                    <span className="agent-field-label">Hook</span>
                    <span className={`agent-field-value ${detail?.hook ? 'bead-id' : 'idle'}`}>
                        {detail?.hook || (agent.has_work ? 'has_work' : 'IDLE')}
                    </span>
                </div>
                <div className="agent-field">
                    <span className="agent-field-label">Session</span>
                    <span className="agent-field-value" title={agent.session}>{agent.session}</span>
                </div>
                {isAltRunning && (
                    <div className="agent-field">
                        <span className="agent-field-label">Status</span>
                        <span className="agent-field-value active" style={{ color: '#d29922' }}>
                            RUNNING (alt session)
                        </span>
                    </div>
                )}
                <div className="agent-field">
                    <span className="agent-field-label">ACP</span>
                    <span title="Agent Control Protocol">
                        <span className={`acp-indicator ${agent.acp ? 'active' : ''}`} />
                        {' '}{agent.acp ? 'active' : 'off'}
                    </span>
                </div>
                {detail?.last_active && (
                    <div className="agent-field">
                        <span className="agent-field-label">Last Active</span>
                        <span className="agent-field-value">{timeAgo(detail.last_active)}</span>
                    </div>
                )}
            </div>
        </div>
    );
};

const AgentGrid = ({
    data, onSelectAgent
}: {
    data: GastownData;
    onSelectAgent: (agent: AgentInfo) => void;
}) => {
    const agents = data.status?.agents ?? [];
    const rigAgents: AgentInfo[] = [];
    for (const rig of (data.status?.rigs ?? [])) {
        for (const pc of (rig.polecats ?? [])) rigAgents.push(pc);
        for (const cr of (rig.crews ?? [])) rigAgents.push(cr);
    }
    const allAgents = [...agents, ...rigAgents];
    const tmuxSessions = data.tmux_sessions ?? [];

    if (allAgents.length === 0) {
        return (
            <div className="agent-grid-zone">
                <div className="empty-state">
                    <div className="empty-state-icon">🤖</div>
                    <div className="empty-state-text">No agents registered in this town.<br />Agents appear here when spawned via gt sling.</div>
                </div>
            </div>
        );
    }

    return (
        <div className="agent-grid-zone">
            <div className="agent-grid">
                {allAgents.map(agent => (
                    <AgentCard
                        key={agent.address}
                        agent={agent}
                        detail={data.agents_detail?.[agent.name]}
                        tmuxSessions={tmuxSessions}
                        onClick={() => onSelectAgent(agent)}
                    />
                ))}
            </div>
        </div>
    );
};

// ============================================================
//  Work Queue Column
// ============================================================
const WorkQueueColumn = ({ ready }: { ready: ReadyData | null }) => {
    const [activeTab, setActiveTab] = useState<'pending' | 'in_progress' | 'done'>('pending');

    const allIssues: ReadyIssue[] = [];
    for (const src of (ready?.sources ?? [])) {
        for (const issue of (src.issues ?? [])) {
            allIssues.push({ ...issue, source: src.name });
        }
    }

    const pending = allIssues.filter(i => i.status === 'open' || i.status === 'ready');
    const inProgress = allIssues.filter(i => i.status === 'in_progress');
    const done = allIssues.filter(i => i.status === 'done' || i.status === 'closed').slice(0, 20);

    const tabIssues = activeTab === 'pending' ? pending : activeTab === 'in_progress' ? inProgress : done;
    const total = ready?.summary?.total ?? 0;

    return (
        <>
            <div className="col-header">
                <div className="col-header-left">
                    <span className="col-title">Work Queue</span>
                    <span className={`count-badge ${total > 0 ? 'active' : ''}`}>{total}</span>
                </div>
            </div>
            <div className="col-content">
                {(ready?.sources ?? []).filter(s => s.error).map(s => (
                    <div key={s.name} className="ready-error-bar">
                        ⚠ {s.name}: {s.error}
                    </div>
                ))}
                <div className="work-tabs">
                    <button className={`work-tab ${activeTab === 'pending' ? 'active' : ''}`}
                        onClick={() => setActiveTab('pending')}>
                        Pending ({pending.length})
                    </button>
                    <button className={`work-tab ${activeTab === 'in_progress' ? 'active' : ''}`}
                        onClick={() => setActiveTab('in_progress')}>
                        Active ({inProgress.length})
                    </button>
                    <button className={`work-tab ${activeTab === 'done' ? 'active' : ''}`}
                        onClick={() => setActiveTab('done')}>
                        Done
                    </button>
                </div>

                {tabIssues.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">
                            {activeTab === 'pending' ? '💤' : activeTab === 'in_progress' ? '⚡' : '✅'}
                        </div>
                        <div className="empty-state-text">
                            {activeTab === 'pending'
                                ? 'No pending work.\nAll quiet — the queue is empty.'
                                : activeTab === 'in_progress'
                                    ? 'No work in progress.\nAgents are idle.'
                                    : 'No completed work recorded yet.'}
                        </div>
                    </div>
                ) : (
                    tabIssues.map(issue => (
                        <div key={issue.id} className="bead-card">
                            <div className="bead-card-header">
                                <span className="bead-id" title="Bead ID">{issue.id}</span>
                                <span className={`bead-priority ${priorityClass(issue.priority)}`}
                                    title="Priority">
                                    {priorityLabel(issue.priority)}
                                </span>
                            </div>
                            <div className="bead-title" title={issue.title}>{issue.title}</div>
                            <div className="bead-meta">
                                <span className={`bead-status-pill ${issue.status}`}>{issue.status}</span>
                                {issue.assignee && <span title="Assigned to">{issue.assignee}</span>}
                                {issue.source && <span title="Source rig">{issue.source}</span>}
                                {issue.created_at && (
                                    <span title={`Created: ${issue.created_at}`}>{timeAgo(issue.created_at)}</span>
                                )}
                            </div>
                        </div>
                    ))
                )}
            </div>
        </>
    );
};

// ============================================================
//  Activity Feed Column
// ============================================================
const ActivityFeedColumn = ({ logLines }: { logLines: string[] }) => {
    const [filter, setFilter] = useState('');
    const [autoScroll, setAutoScroll] = useState(true);
    const feedRef = useRef<HTMLDivElement>(null);

    const filtered = filter
        ? logLines.filter(l => l.toLowerCase().includes(filter.toLowerCase()))
        : logLines;

    useEffect(() => {
        if (autoScroll && feedRef.current) {
            feedRef.current.scrollTop = feedRef.current.scrollHeight;
        }
    }, [filtered, autoScroll]);

    return (
        <>
            <div className="col-header">
                <div className="col-header-left">
                    <span className="col-title">Activity Log</span>
                    <span className="count-badge">{filtered.length}</span>
                </div>
            </div>
            <div className="col-content">
                <div className="feed-controls">
                    <input
                        className="feed-filter"
                        placeholder="Filter by agent or event…"
                        value={filter}
                        onChange={e => setFilter(e.target.value)}
                    />
                    <button
                        className={`btn-autoscroll ${autoScroll ? 'active' : ''}`}
                        onClick={() => setAutoScroll(v => !v)}
                        title="Toggle auto-scroll to latest"
                    >
                        {autoScroll ? '⏬ Auto' : '⏸ Manual'}
                    </button>
                </div>
                <div className="activity-feed" ref={feedRef}>
                    {filtered.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">📋</div>
                            <div className="empty-state-text">No activity yet.<br />Events appear here as agents spawn, complete work, and hand off tasks.</div>
                        </div>
                    ) : (
                        filtered.map((line, i) => {
                            const { type, icon } = detectLogEvent(line);
                            return (
                                <div key={i} className={`log-entry ${type}`}>
                                    <span className="log-icon">{icon}</span>
                                    <span className="log-content">
                                        <span className="log-text">{line}</span>
                                    </span>
                                </div>
                            );
                        })
                    )}
                </div>
                <div className="line-count">{filtered.length} lines{filter ? ` (filtered from ${logLines.length})` : ''}</div>
            </div>
        </>
    );
};

// ============================================================
//  Right Column — Patrols + Vitals + Escalation
// ============================================================
const PatrolAndVitalsColumn = ({
    data
}: {
    data: GastownData;
}) => {
    const patrols = data.patrols;
    const status = data.status;
    const esc = data.escalation;

    const allPatrols: { name: string; enabled: boolean; interval: string }[] = [];
    if (patrols?.heartbeat) {
        allPatrols.push({ name: 'heartbeat', enabled: patrols.heartbeat.enabled, interval: patrols.heartbeat.interval });
    }
    if (patrols?.patrols) {
        for (const [name, cfg] of Object.entries(patrols.patrols)) {
            allPatrols.push({ name, enabled: cfg.enabled, interval: cfg.interval });
        }
    }

    const escRoutes = esc?.routes ? Object.entries(esc.routes) : [];

    return (
        <>
            <div className="col-header">
                <div className="col-header-left">
                    <span className="col-title">Patrols & Vitals</span>
                </div>
            </div>
            <div className="col-content">
                {/* Daemon warning */}
                {status?.daemon?.running === false && (
                    <div className="patrol-warning">
                        ⚠ Daemon stopped — scheduled patrols will not run automatically
                    </div>
                )}
                {/* Patrols */}
                <div className="right-col-section">
                    <div className="right-col-section-title">Patrol Schedule</div>
                    {allPatrols.length === 0 ? (
                        <div style={{ color: '#484f58', fontSize: '0.75rem', padding: '0.5rem 0' }}>
                            No patrol config found
                        </div>
                    ) : allPatrols.map(p => (
                        <div key={p.name} className="patrol-item">
                            <span className="patrol-name">{p.name}</span>
                            <div className="patrol-badge-status">
                                <span className={`patrol-dot ${p.enabled ? 'green' : 'gray'}`}
                                    title={p.enabled ? 'Enabled' : 'Disabled'} />
                                <span style={{ color: p.enabled ? '#3fb950' : '#484f58', fontSize: '0.7rem' }}>
                                    {p.enabled ? p.interval : 'disabled'}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>

                {/* Tmux Sessions */}
                {data.tmux_sessions && data.tmux_sessions.length > 0 && (
                    <div className="right-col-section" style={{ marginTop: '0.75rem' }}>
                        <div className="right-col-section-title">Tmux Sessions</div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.15rem', paddingTop: '0.25rem' }}>
                            {data.tmux_sessions.map(s => (
                                <span key={s} className="session-pill">{s}</span>
                            ))}
                        </div>
                    </div>
                )}
                {(!data.tmux_sessions || data.tmux_sessions.length === 0) && (
                    <div className="right-col-section" style={{ marginTop: '0.75rem' }}>
                        <div className="right-col-section-title">Tmux Sessions</div>
                        <div className="dim">none</div>
                    </div>
                )}

                <div className="col-divider" />

                {/* Town Vitals */}
                {status && (
                    <div className="right-col-section">
                        <div className="right-col-section-title">Town Vitals</div>
                        <div className="vitals-row">
                            <span className="vitals-key">Daemon</span>
                            <span className={`vitals-val ${status.daemon?.running ? 'up' : 'down'}`}>
                                {status.daemon?.running ? 'running' : 'stopped'}
                            </span>
                        </div>
                        <div className="vitals-row">
                            <span className="vitals-key">Dolt</span>
                            <span className={`vitals-val ${status.dolt?.running ? 'up' : 'down'}`}>
                                {status.dolt?.running ? `running :${status.dolt.port}` : `stopped :${status.dolt?.port ?? 3307}`}
                            </span>
                        </div>
                        <div className="vitals-row">
                            <span className="vitals-key">Dolt Data</span>
                            <span className="vitals-val" title={status.dolt?.data_dir}>
                                {status.dolt?.data_dir?.replace('/home/builder/', '~/') ?? 'N/A'}
                            </span>
                        </div>
                        <div className="vitals-row">
                            <span className="vitals-key">tmux</span>
                            <span className={`vitals-val ${status.tmux?.running ? 'up' : 'down'}`}>
                                {status.tmux?.running
                                    ? `${status.tmux.session_count} sessions`
                                    : 'stopped'}
                            </span>
                        </div>
                        <div className="vitals-row">
                            <span className="vitals-key">tmux socket</span>
                            <span className="vitals-val" title={status.tmux?.socket_path}>
                                -L {status.tmux?.socket ?? 'default'}
                            </span>
                        </div>
                        {data.tmux_sessions?.length > 0 && (
                            <div className="vitals-row">
                                <span className="vitals-key">Sessions</span>
                                <span className="vitals-val" title={data.tmux_sessions.join(', ')}>
                                    {data.tmux_sessions.slice(0, 3).join(', ')}
                                    {data.tmux_sessions.length > 3 ? `…+${data.tmux_sessions.length - 3}` : ''}
                                </span>
                            </div>
                        )}
                        <div className="vitals-row">
                            <span className="vitals-key">Overseer</span>
                            <span className="vitals-val">{status.overseer?.username ?? 'N/A'}</span>
                        </div>
                        {data.git_info?.branch && (
                            <>
                                <div className="vitals-row">
                                    <span className="vitals-key">Git Branch</span>
                                    <span className="vitals-val">{data.git_info.branch}</span>
                                </div>
                                <div className="vitals-row">
                                    <span className="vitals-key">Last Commit</span>
                                    <span className="vitals-val" title={data.git_info.last_commit}>
                                        {data.git_info.last_commit?.slice(0, 40) ?? 'N/A'}
                                    </span>
                                </div>
                            </>
                        )}
                    </div>
                )}

                {/* Escalation */}
                {escRoutes.length > 0 && (
                    <>
                        <div className="col-divider" />
                        <div className="right-col-section">
                            <div className="right-col-section-title">
                                Escalation
                                {esc?.stale_threshold && (
                                    <span style={{ fontWeight: 400, marginLeft: '0.5rem', color: '#8b949e' }}>
                                        (stale: {esc.stale_threshold})
                                    </span>
                                )}
                            </div>
                            {escRoutes.map(([level, channels]) => (
                                <div key={level} className="escalation-route">
                                    <span className={`escalation-level ${level}`}>{level}</span>
                                    <span className="escalation-channels">{channels.join(' → ')}</span>
                                </div>
                            ))}
                        </div>
                    </>
                )}
            </div>
        </>
    );
};

// ============================================================
//  Zone 4 — Rig Map
// ============================================================
const RigMap = ({ data }: { data: GastownData }) => {
    const rigs = data.status?.rigs ?? [];

    if (rigs.length === 0) {
        return (
            <div className="rig-map-zone">
                <div className="empty-state">
                    <div className="empty-state-icon">🏭</div>
                    <div className="empty-state-text">No rigs registered.<br />Add rigs with: gt rig add &lt;name&gt;</div>
                </div>
            </div>
        );
    }

    return (
        <div className="rig-map-zone">
            <div className="rig-grid">
                {rigs.map(rig => {
                    const isEmpty = rig.polecat_count === 0 && rig.crew_count === 0;
                    const allAgents = [...(rig.polecats ?? []), ...(rig.crews ?? [])];

                    return (
                        <div key={rig.name} className={`rig-card ${isEmpty ? 'empty' : 'healthy'}`}>
                            <div className="rig-card-header">
                                <span className="rig-name" title={`Rig: ${rig.name}`}>{rig.name}</span>
                                <span className={`rig-health-dot ${isEmpty ? 'gray' : 'green'}`}
                                    title={isEmpty ? 'Empty — no agents' : 'Active'} />
                            </div>

                            <div className="rig-stats">
                                <div className="rig-stat">
                                    <span className="rig-stat-label">Polecats:</span>
                                    <span className="rig-stat-value">{rig.polecat_count}</span>
                                </div>
                                <div className="rig-stat">
                                    <span className="rig-stat-label">Crew:</span>
                                    <span className="rig-stat-value">{rig.crew_count}</span>
                                </div>
                            </div>

                            <div className="rig-features">
                                <span className={`rig-feature-badge ${rig.has_witness ? 'has' : 'none'}`}
                                    title="Witness agent monitors work quality">
                                    {rig.has_witness ? '✓' : '✗'} Witness
                                </span>
                                <span className={`rig-feature-badge ${rig.has_refinery ? 'has' : 'none'}`}
                                    title="Refinery agent polishes completed work">
                                    {rig.has_refinery ? '✓' : '✗'} Refinery
                                </span>
                            </div>

                            {allAgents.length > 0 && (
                                <div className="rig-mini-agents">
                                    {allAgents.map(ag => (
                                        <span key={ag.address} className="rig-mini-agent" title={ag.role}>
                                            {ag.name}
                                        </span>
                                    ))}
                                </div>
                            )}

                            {isEmpty && (
                                <div style={{ marginTop: '0.5rem', fontSize: '0.7rem', color: '#484f58', fontStyle: 'italic' }}>
                                    No agents — use gt sling to assign work
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

// ============================================================
//  Zone 5 — Data Audit
// ============================================================
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

const statusDot = (ok: boolean | null | undefined, warn?: boolean) => {
    if (ok === null || ok === undefined) return <span className="audit-dot unknown" title="Unknown">●</span>;
    if (warn) return <span className="audit-dot warn" title="Warning">●</span>;
    return ok
        ? <span className="audit-dot ok" title="OK">●</span>
        : <span className="audit-dot error" title="Error">●</span>;
};

const DataAuditZone = ({
    audit,
    onVerify,
    verifying,
}: {
    audit: DataAudit | null;
    onVerify: () => void;
    verifying: boolean;
}) => {
    if (!audit) {
        return (
            <div className="audit-zone">
                <div className="audit-loading">Loading data audit…</div>
            </div>
        );
    }

    const sv = audit.spot_validation;
    const divWarn = sv.divergence_pct >= 2.0;
    const divError = sv.divergence_pct >= 5.0;
    const divClass = divError ? 'error' : divWarn ? 'warn' : 'ok';

    return (
        <div className="audit-zone">
            <div className="audit-header">
                <div className="audit-sources">
                    <div className="audit-source-row">
                        {statusDot(audit.ibkr_connected)}
                        <span className="audit-source-label">
                            IBKR
                            {audit.primary_source === 'ibkr' && (
                                <span className="audit-primary-badge">PRIMARY</span>
                            )}
                        </span>
                        <span className="audit-source-val">
                            {audit.ibkr_connected && audit.ibkr_spot > 0
                                ? `$${audit.ibkr_spot.toFixed(2)}`
                                : audit.ibkr_connected ? 'connected' : 'offline'}
                        </span>
                    </div>
                    <div className="audit-source-row">
                        {statusDot(audit.tv_feed_ok)}
                        <span className="audit-source-label">TradingView</span>
                        <span className="audit-source-val">
                            {sv.tv != null ? `$${sv.tv.toFixed(2)}` : '—'}
                        </span>
                    </div>
                    <div className="audit-source-row">
                        {statusDot(audit.yf_feed_ok)}
                        <span className="audit-source-label">yfinance</span>
                        <span className="audit-source-val">
                            {sv.yf != null ? `$${sv.yf.toFixed(2)}` : '—'}
                        </span>
                    </div>
                    <div className="audit-source-row">
                        {statusDot(true)}
                        <span className="audit-source-label">Options Chain</span>
                        <span className="audit-source-val">{audit.options_chain_source}</span>
                    </div>
                </div>

                <div className="audit-divergence">
                    <div className={`audit-div-pct ${divClass}`}>
                        {sv.divergence_pct.toFixed(3)}%
                    </div>
                    <div className="audit-div-label">DIVERGENCE</div>
                    <div className={`audit-ok-badge ${sv.ok ? 'ok' : 'error'}`}>
                        {sv.ok ? '✓ IN SYNC' : '✗ OUT OF SYNC'}
                    </div>
                </div>

                <div className="audit-meta">
                    <div className="audit-meta-row">
                        <span className="audit-meta-key">Last verified</span>
                        <span className="audit-meta-val">{timeAgo(sv.timestamp)}</span>
                    </div>
                    <div className="audit-meta-row">
                        <span className="audit-meta-key">Chain age</span>
                        <span className="audit-meta-val">{audit.chain_age_sec.toFixed(0)}s</span>
                    </div>
                    <button
                        className="btn-verify"
                        onClick={onVerify}
                        disabled={verifying}
                    >
                        {verifying ? 'Verifying…' : 'Verify Now'}
                    </button>
                </div>
            </div>

            {sv.warning && (
                <div className={`audit-warning ${divError ? 'error' : 'warn'}`}>
                    {sv.warning}
                </div>
            )}
        </div>
    );
};

// ============================================================
//  Zone 6 — History
// ============================================================
interface GitCommit {
    hash: string;
    date: string;
    message: string;
    source?: 'repo' | 'workspace';
}

interface BeadIssue {
    id: string;
    title: string;
    status: string;
    priority: number;
}

interface HistoryData {
    repo_log: GitCommit[];
    workspace_log: GitCommit[];
    beads: BeadIssue[];
    session_tail: string[];
    refreshed_at: string;
}

const statusIcon = (s: string) => ({ closed: '✓', open: '○', in_progress: '◐', blocked: '●', deferred: '❄' }[s] ?? '?');
const statusCls  = (s: string) => ({ closed: 'done', open: 'open', in_progress: 'wip', blocked: 'blocked', deferred: 'deferred' }[s] ?? 'open');

const HistoryZone = ({ history }: { history: HistoryData | null }) => {
    const [tab, setTab] = useState<'commits' | 'worklog' | 'session'>('commits');
    const sessionRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (tab === 'session' && sessionRef.current) {
            sessionRef.current.scrollTop = sessionRef.current.scrollHeight;
        }
    }, [tab, history]);

    if (!history) {
        return <div className="history-loading">Loading history…</div>;
    }

    // Interleave repo + workspace commits sorted by date desc
    const allCommits: GitCommit[] = [
        ...history.repo_log.map(c => ({ ...c, source: 'repo' as const })),
        ...history.workspace_log.map(c => ({ ...c, source: 'workspace' as const })),
    ].sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());

    // Group beads by status
    const beadGroups: Record<string, BeadIssue[]> = {};
    const groupOrder = ['open', 'in_progress', 'blocked', 'closed', 'deferred'];
    for (const issue of history.beads) {
        const g = issue.status || 'open';
        if (!beadGroups[g]) beadGroups[g] = [];
        beadGroups[g].push(issue);
    }

    return (
        <div className="history-zone">
            <div className="history-tabs">
                <button
                    className={`history-tab ${tab === 'commits' ? 'active' : ''}`}
                    onClick={() => setTab('commits')}
                >
                    COMMITS
                    <span className="tab-count">{allCommits.length}</span>
                </button>
                <button
                    className={`history-tab ${tab === 'worklog' ? 'active' : ''}`}
                    onClick={() => setTab('worklog')}
                >
                    WORK LOG
                    <span className="tab-count">{history.beads.length}</span>
                </button>
                <button
                    className={`history-tab ${tab === 'session' ? 'active' : ''}`}
                    onClick={() => setTab('session')}
                >
                    SESSION
                    <span className="tab-count">{history.session_tail.length}</span>
                </button>
            </div>

            {/* Tab 1: Commits */}
            {tab === 'commits' && (
                <div className="history-commits">
                    {allCommits.length === 0 ? (
                        <div className="history-empty">No commits found.</div>
                    ) : allCommits.map((c, i) => {
                        const d = new Date(c.date);
                        const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                        const timeStr = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
                        return (
                            <div key={`${c.hash}-${i}`} className={`commit-row ${c.source}`}>
                                <span className="commit-date">{dateStr} {timeStr}</span>
                                <span className={`commit-hash ${c.source}`}>{c.hash}</span>
                                <span className="commit-msg">{c.message}</span>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* Tab 2: Work Log */}
            {tab === 'worklog' && (
                <div className="history-worklog">
                    {history.beads.length === 0 ? (
                        <div className="history-empty">
                            No tasks recorded yet — create tasks with <code>bd create --title "..."</code>
                        </div>
                    ) : groupOrder.filter(g => beadGroups[g]?.length > 0).map(g => (
                        <div key={g} className="bead-group">
                            <div className="bead-group-header">{g.replace('_', ' ').toUpperCase()} ({beadGroups[g].length})</div>
                            {beadGroups[g].map(issue => (
                                <div key={issue.id} className={`bead-card ${statusCls(issue.status)}`}>
                                    <span className="bead-id">{issue.id}</span>
                                    <span className="bead-status-icon">{statusIcon(issue.status)}</span>
                                    <span className="bead-title">{issue.title}</span>
                                    <span className={`bead-priority p${issue.priority}`}>P{issue.priority}</span>
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            )}

            {/* Tab 3: Session tail */}
            {tab === 'session' && (
                <div className="history-session" ref={sessionRef}>
                    {history.session_tail.length === 0 ? (
                        <div className="history-empty">No session output captured (tsla-claude not running)</div>
                    ) : history.session_tail.map((line, i) => (
                        <div key={i} className={`session-line ${i % 2 === 0 ? 'even' : 'odd'}`}>
                            {line}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

// ============================================================
//  Collapsible Zone Wrapper
// ============================================================
const ZoneWrapper = ({
    id, label, children, defaultOpen = true
}: {
    id: string;
    label: string;
    children: React.ReactNode;
    defaultOpen?: boolean;
}) => {
    const [open, setOpen] = useState(() => {
        const stored = localStorage.getItem(`gt_zone_${id}`);
        return stored === null ? defaultOpen : stored === 'true';
    });

    const toggle = () => {
        const next = !open;
        setOpen(next);
        localStorage.setItem(`gt_zone_${id}`, String(next));
    };

    return (
        <div>
            <div className="zone-header" onClick={toggle}>
                <div className="zone-header-left">
                    <span className={`zone-chevron ${open ? 'open' : ''}`}>▶</span>
                    <h2>{label}</h2>
                </div>
            </div>
            {open && children}
        </div>
    );
};

// ============================================================
//  Main Gastown Component
// ============================================================
const Gastown = () => {
    const [data, setData] = useState<GastownData>({
        status: null,
        log: [],
        ready: null,
        agents_detail: {},
        patrols: null,
        tmux_sessions: [],
        escalation: null,
        git_info: { branch: '', last_commit: '' },
        refreshed_at: '',
    });
    const [logLines, setLogLines] = useState<string[]>([]);
    const [history, setHistory] = useState<HistoryData | null>(null);
    const [audit, setAudit] = useState<DataAudit | null>(null);
    const [auditVerifying, setAuditVerifying] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [selectedAgent, setSelectedAgent] = useState<AgentInfo | null>(null);
    const [simMode, setSimMode] = useState<string>('paper');
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const logIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const historyIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const auditIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const fetchStatus = useCallback(async () => {
        setRefreshing(true);
        try {
            const resp = await fetch('/api/gastown/status');
            if (resp.ok) {
                const d = await resp.json();
                setData(d);
            }
        } catch { /* ignore */ }
        setRefreshing(false);
    }, []);

    const fetchLog = useCallback(async () => {
        try {
            const resp = await fetch('/api/gastown/log');
            if (resp.ok) {
                const lines = await resp.json();
                setLogLines(Array.isArray(lines) ? lines : []);
            }
        } catch { /* ignore */ }
    }, []);

    const fetchHistory = useCallback(async () => {
        try {
            const resp = await fetch('/api/gastown/history');
            if (resp.ok) {
                const d = await resp.json();
                setHistory(d);
            }
        } catch { /* ignore */ }
    }, []);

    const fetchAudit = useCallback(async (refresh = false) => {
        try {
            const url = refresh ? '/api/data/audit?refresh=true' : '/api/data/audit';
            const resp = await fetch(url);
            if (resp.ok) {
                const d = await resp.json();
                setAudit(d);
            }
        } catch { /* ignore */ }
    }, []);

    const handleVerifyNow = async () => {
        setAuditVerifying(true);
        await fetchAudit(true);
        setAuditVerifying(false);
    };

    useEffect(() => {
        fetchStatus();
        fetchLog();
        fetchHistory();
        fetchAudit();
        intervalRef.current = setInterval(fetchStatus, 10000);
        logIntervalRef.current = setInterval(fetchLog, 5000);
        historyIntervalRef.current = setInterval(fetchHistory, 30000);
        auditIntervalRef.current = setInterval(() => fetchAudit(), 60000);
        return () => {
            if (intervalRef.current) clearInterval(intervalRef.current);
            if (logIntervalRef.current) clearInterval(logIntervalRef.current);
            if (historyIntervalRef.current) clearInterval(historyIntervalRef.current);
            if (auditIntervalRef.current) clearInterval(auditIntervalRef.current);
        };
    }, [fetchStatus, fetchLog, fetchHistory, fetchAudit]);

    const handleRefresh = () => {
        fetchStatus();
        fetchLog();
        fetchHistory();
    };

    const handleSimToggle = async () => {
        try {
            const r = await fetch('/api/sim/toggle');
            if (r.ok) {
                const d = await r.json() as { mode: string };
                setSimMode(d.mode);
            }
        } catch { /* ignore */ }
    };

    return (
        <div className="gastown-dashboard">
            {/* Modal */}
            {selectedAgent && (
                <AgentModal
                    agent={selectedAgent}
                    detail={data.agents_detail?.[selectedAgent.name]}
                    onClose={() => setSelectedAgent(null)}
                />
            )}

            {/* Zone 1 — Header (always visible) */}
            <TownHeader data={data} onRefresh={handleRefresh} refreshing={refreshing} simMode={simMode} onSimToggle={handleSimToggle} />

            {/* Zone 2 — Agent Grid */}
            <ZoneWrapper id="agents" label="AGENTS" defaultOpen={true}>
                <AgentGrid data={data} onSelectAgent={setSelectedAgent} />
            </ZoneWrapper>

            {/* Zone 3 — Three columns */}
            <ZoneWrapper id="worklog" label="WORK · ACTIVITY · PATROLS" defaultOpen={true}>
                <div className="three-column-zone">
                    <div className="col-panel">
                        <WorkQueueColumn ready={data.ready} />
                    </div>
                    <div className="col-panel">
                        <ActivityFeedColumn logLines={logLines} />
                    </div>
                    <div className="col-panel">
                        <PatrolAndVitalsColumn data={data} />
                    </div>
                </div>
            </ZoneWrapper>

            {/* Zone 4 — Rig Map */}
            <ZoneWrapper id="rigs" label="RIG MAP" defaultOpen={true}>
                <RigMap data={data} />
            </ZoneWrapper>

            {/* Zone 5 — Data Audit */}
            <ZoneWrapper id="data-audit" label="DATA AUDIT · SPOT CROSS-VALIDATION" defaultOpen={true}>
                <DataAuditZone audit={audit} onVerify={handleVerifyNow} verifying={auditVerifying} />
            </ZoneWrapper>

            {/* Zone 6 — History */}
            <ZoneWrapper id="history" label="HISTORY" defaultOpen={false}>
                <HistoryZone history={history} />
            </ZoneWrapper>
        </div>
    );
};

export default Gastown;
