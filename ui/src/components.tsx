import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { ArrowUpRight, Check, ChevronRight, Circle, FileCode2, GitBranch, LoaderCircle, ShieldCheck, X } from 'lucide-react';
import { demoFiles, demoTests, decisions, hasIdentity, isAligned, type DemoState, type Phase } from './demo';

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Button({ children, variant = 'secondary', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'quiet' }) {
  return <button className={`button button-${variant} ${className}`} {...props}>{children}</button>;
}

export function Logo({ compact = false }: { compact?: boolean }) {
  return <span className="brand"><svg viewBox="0 0 32 32" aria-hidden="true" className="brand-mark"><path d="M7 8h12l6 8-6 8H7l6-8Z" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round" /><path d="m7 8 12 16M19 8 7 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round" /></svg>{!compact && <span>synapse<span className="brand-period">.</span></span>}</span>;
}

type TraceEvidence = { trace_id: string; source: string; event_type: string; timestamp: string; run_id?: string | null; objective_id?: string | null; workstream_id?: string | null; agent_id?: string | null; payload?: Record<string, unknown> };
export type Inspector = { kind: 'identity' | 'decision' | 'contract' | 'tests' | 'scope' | 'guide'; id?: string } | { kind: 'trace'; event: TraceEvidence } | null;

type AgentRole = 'backend' | 'frontend' | 'telemetry';
const agentCopy: Record<AgentRole, { initials: string; name: string; task: string; intent: string[]; assigned: string[]; dependency: string }> = {
  backend: { initials: 'YOU', name: 'Your coding agent', task: 'OAuth API + session', intent: ['src/api/auth/oauth.ts', 'src/auth/session.ts'], assigned: ['src/api/auth/oauth.ts', 'src/auth/session.ts'], dependency: 'Publishes the session contract' },
  frontend: { initials: 'TA', name: 'Teammate A agent', task: 'Organization login UI', intent: ['src/components/login/OrganizationLogin.tsx', 'src/auth/session.ts'], assigned: ['src/components/login/OrganizationLogin.tsx'], dependency: 'Consumes the session contract' },
  telemetry: { initials: 'TB', name: 'Teammate B agent', task: 'Login telemetry', intent: ['src/lib/analytics/authEvents.ts', 'src/auth/session.ts'], assigned: ['src/lib/analytics/authEvents.ts'], dependency: 'Observes session events' },
};

export function AgentCard({ role, phase, inspect, ans }: { role: AgentRole; phase: Phase; inspect: (value: Inspector) => void; ans: boolean }) {
  const item = agentCopy[role];
  const known = hasIdentity(phase);
  const aligned = isAligned(phase);
  const blocked = ['conflict', 'coordinating'].includes(phase);
  const status = phase === 'complete' ? 'Complete' : phase === 'ready' ? 'Waiting' : blocked ? 'Blocked' : phase === 'verifying' ? 'Joining' : 'Active';
  return <article className={`agent-card ${blocked ? 'agent-blocked' : ''}`}>
    <div className="agent-top"><div className={`agent-avatar avatar-${role}`}><span>{item.initials}</span></div><div className="agent-name"><h3>{item.name}</h3><span>{item.task}</span></div><Badge tone={blocked ? 'amber' : phase === 'complete' ? 'green' : 'neutral'}><span className={`status-dot ${status.toLowerCase()}`} />{status}</Badge></div>
    <button className="identity-link" onClick={() => inspect({ kind: 'identity', id: role })}><ShieldCheck size={14} />{known ? 'Identity checked' : 'Identity pending'}<span className="source-tag">{ans ? 'ANS' : 'Mock'}</span><ChevronRight size={13} /></button>
    <div className="agent-details"><div><span className="field-label">{aligned ? 'Assigned files' : 'Planned touches'}</span><button className="code-link code-link-stack" onClick={() => inspect({ kind: 'scope', id: role })}>{(aligned ? item.assigned : item.intent).map(path => <span key={path} className={path === 'src/auth/session.ts' && !aligned ? 'path-collision' : ''}>{path}</span>)}<ArrowUpRight size={13} /></button></div>
    <div><span className="field-label">Shared interface</span><code className="endpoint"><span>POST</span> /api/oauth</code></div>
    <div><span className="field-label">Contract fields</span><div className="field-chips"><code>token</code><code>user</code>{aligned && <Check size={14} className="success-ink" />}</div></div></div>
    <footer className="agent-footer"><GitBranch size={13} /><span>{item.dependency}</span></footer>
  </article>;
}

export function FileList({ onSelect, selected, submitted = false }: { onSelect: (index: number) => void; selected?: number; submitted?: boolean }) {
  return <div className="file-list">{demoFiles.map((file, index) => <button key={file.path} className={`file-row ${selected === index ? 'selected' : ''}`} onClick={() => onSelect(index)}><FileCode2 size={17} /><span className="file-label"><strong>{file.path.split('/').at(-1)}</strong><span>{file.path.split('/').slice(0, -1).join('/')}</span></span><span className="file-owner">{file.owner}</span><Badge>{submitted ? 'Submitted' : 'Sample'}</Badge><ChevronRight size={14} /></button>)}</div>;
}

export function Diff({ index }: { index: number }) {
  const file = demoFiles[index];
  return <div className="diff-view"><div className="diff-header"><FileCode2 size={16} /><strong>{file.path}</strong></div><div className="diff-caption">Illustrative excerpt · Not a Git diff from your repository</div><pre className="diff-code" aria-label={`Illustrative changes for ${file.path}`}>{file.lines.map((line, index) => <span className={`diff-line diff-${line.kind}`} key={index}><span className="line-number">{index + 1}</span><span className="line-sign">{line.kind === 'add' ? '+' : line.kind === 'remove' ? '−' : ' '}</span><code>{line.text || ' '}</code></span>)}</pre><p className="diff-description">{file.description}</p></div>;
}

export function TestRows({ completed }: { completed: boolean }) {
  return <div className="test-list">{demoTests.map(test => <div className="test-row" key={test.name}>{completed ? <Check size={16} className="success-ink" /> : <Circle size={14} />}<span>{test.name}<small>{test.owner} · {completed ? 'Simulated agent report' : 'Not submitted'}</small></span><span className={completed ? 'success-ink' : 'muted'}>{completed ? 'Pass' : 'Pending'}</span></div>)}</div>;
}

/** Live ANS status, so the drawer never claims a fixture when verification is real. */
export type AnsSummary = { live: boolean; dpop: boolean; names: string };

export function InspectorDrawer({ value, onClose, demo, ans }: { value: Inspector; onClose: () => void; demo: DemoState; ans?: AnsSummary }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (value) dialog.current?.showModal();
    else dialog.current?.close();
  }, [value]);
  const inspectorId = value && value.kind !== 'trace' ? value.id : undefined;
  const decision = decisions.find(item => item.id === inspectorId) ?? decisions[0];
  const title = value?.kind === 'trace' ? value.event.event_type.replaceAll('_', ' ') : value?.kind === 'identity' ? 'Agent identity' : value?.kind === 'decision' ? decision.title : value?.kind === 'scope' ? 'Scoped assignment' : value?.kind === 'tests' ? 'Validation results' : value?.kind === 'guide' ? 'The three-minute walkthrough' : 'Interface contract';
  return <dialog ref={dialog} className="inspector" onClose={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }} aria-labelledby="inspector-title"><div className="drawer-body"><header className="drawer-header"><div><h2 id="inspector-title">{title}</h2></div><button className="icon-button" aria-label="Close evidence panel" onClick={onClose}><X size={20} /></button></header>
    <div className="drawer-notice">{value?.kind === 'trace' ? <><Badge tone="green">{value.event.source === 'databricks' ? 'Databricks trace' : 'Trace event'}</Badge><p>Persisted event evidence.</p></> : ans?.live && value?.kind === 'identity' ? <><Badge tone="green">Live ANS</Badge><p>Identity was verified against the GoDaddy transparency log. Memory and test results remain local fixtures.</p></> : <><Badge tone="amber">Demo evidence</Badge><p>These fixtures explain the flow. No live provider operation was performed.</p></>}</div>
    {value?.kind === 'trace' && <><dl className="detail-list"><div><dt>When</dt><dd>{new Date(value.event.timestamp).toLocaleString()}</dd></div><div><dt>Agent</dt><dd>{value.event.agent_id ?? 'Coordinator'}</dd></div><div><dt>Workstream</dt><dd>{value.event.workstream_id ?? 'Objective'}</dd></div><div><dt>Run</dt><dd>{value.event.run_id ?? 'Coordinator run'}</dd></div></dl><h3>Recorded evidence</h3><pre className="code-block">{JSON.stringify(value.event.payload ?? {}, null, 2)}</pre></>}
    {value?.kind === 'identity' && <><div className="evidence-icon"><ShieldCheck size={26} /></div><h3>{value.id && value.id in agentCopy ? agentCopy[value.id as AgentRole].name : 'Three participating agents'}</h3><dl className="detail-list"><div><dt>Verification state</dt><dd>{ans?.live ? 'Verified against the ANS transparency log' : hasIdentity(demo.phase) ? 'Checked by demo fixture' : 'Awaiting demo check'}</dd></div><div><dt>Identity source</dt><dd>{ans?.live ? 'GoDaddy ANS · badge tier' : 'Local allowlist'}</dd></div><div><dt>Provider</dt><dd>GoDaddy ANS</dd></div><div><dt>Live ANS evidence</dt><dd>{ans?.live ? ans.names : 'Not available'}</dd></div><div><dt>Payload signing</dt><dd>{ans?.live && ans.dpop ? 'ANS-6 Method B (DPoP) proof per request' : 'Not in mock mode'}</dd></div></dl><p className="body-note">{ans?.live ? 'Each privileged call carries a single-use proof signed by the agent’s ANS identity key. A revoked agent is refused at submission, not merely at join.' : 'The live integration will resolve the registered identity and retain the response with its verification timestamp. Unknown identities must be blocked from submitting a ChangeSet.'}</p></>}
    {value?.kind === 'decision' && <><div className="section-heading"><Badge>{decision.id}</Badge><span className="muted">{decision.component}</span></div><p>{decision.content}</p><pre className="code-block">{decision.code}</pre><dl className="detail-list"><div><dt>Current source</dt><dd>Seeded fixture</dd></div><div><dt>Delivery</dt><dd>Shared with both workstreams</dd></div></dl><p className="body-note">The coordinator attaches the most relevant decision to a conflict when it opens; see decision_id on conflict_opened events.</p></>}
    {value?.kind === 'contract' && <><code className="endpoint large"><span>POST</span> /api/oauth</code><p>The API agent publishes this interface. The UI and telemetry agents consume it without sharing implementation files.</p><div className="contract-detail"><h3>Shared response</h3><pre className="code-block">{'{\n  "token": "string",\n  "user": "object"\n}'}</pre><h3>Declared by all three plans</h3><pre className="code-block">{'API        provides\nUI         consumes\nTelemetry  consumes'}</pre></div><p className="body-note">The interface is compatible throughout this scenario. Synapse blocks the work because the planned file scopes overlap, then preserves this contract while it reorganizes ownership.</p></>}
    {value?.kind === 'scope' && (() => { const item = value.id && value.id in agentCopy ? agentCopy[value.id as AgentRole] : agentCopy.backend; return <><h3>{item.name} file plan</h3><dl className="detail-list"><div><dt>Objective</dt><dd>Organization-level OAuth login</dd></div><div><dt>Original intent</dt><dd>{item.intent.map(path => <code key={path}>{path}<br /></code>)}</dd></div><div><dt>Coordinated scope</dt><dd>{item.assigned.map(path => <code key={path}>{path}<br /></code>)}</dd></div><div><dt>Dependency</dt><dd>{item.dependency}</dd></div><div><dt>Relevant decision</dt><dd>ADR-002 · One owner per file</dd></div></dl><p className="body-note">The coordinator compares intent before work begins. It assigns the shared module to one agent and gives the other agents explicit integration boundaries.</p></>; })()}
    {value?.kind === 'tests' && <><p>Agent-reported test results attached to the three demo manifests. These tests were not executed by Synapse.</p><TestRows completed={demo.phase === 'complete'} /></>}
    {value?.kind === 'guide' && <><p>One repository. Three developers. Three coding agents that independently choose the same shared file.</p><ol className="guide-list"><li><strong>Connect the agents</strong><p>Each agent publishes identity, task, and planned file touches before editing.</p></li><li><strong>Show the predicted collision</strong><p>All three plans include <code>src/auth/session.ts</code>, so Synapse blocks implementation.</p></li><li><strong>Apply the coordination plan</strong><p>One agent owns the shared file; the other two receive separate modules and a stable contract.</p></li><li><strong>Review the combined result</strong><p>Submit three independent ChangeSets and show that no merge conflict was created.</p></li></ol><p className="body-note">The interactive demo works offline. Use Raw state to inspect the actual coordinator events.</p></>}
    <Button className="drawer-done" onClick={onClose}>Back to workspace</Button>
  </div></dialog>;
}

export function Busy({ children }: { children: ReactNode }) { return <span className="busy"><LoaderCircle size={15} className="spin" />{children}</span>; }
