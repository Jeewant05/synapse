import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Copy, ExternalLink, FileCode2, GitBranch, GitCommitHorizontal, GitCompareArrows, History, LoaderCircle, Search, UploadCloud } from 'lucide-react';
import {
  getLiveConfig, getLiveRun, getLiveRuns, getLiveTraces, startLiveRun, subscribeLiveRun,
  type LiveArtifact, type LiveConfig, type LiveEvent, type LiveSnapshot, type LiveTrace,
} from './liveAgents';

const roleIds = ['backend', 'frontend', 'integration'] as const;
const initialObjective = 'Build a small task manager with a FastAPI backend, React frontend, and contract tests.';

export function LiveDashboard({ onHub, repositoryMode = false }: { onHub?: (hasRun: boolean) => void; repositoryMode?: boolean }) {
  const [objective, setObjective] = useState(initialObjective);
  const [config, setConfig] = useState<LiveConfig | null>(null);
  const [run, setRun] = useState<LiveSnapshot | null>(null);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [traces, setTraces] = useState<LiveTrace[]>([]);
  const [selectedPath, setSelectedPath] = useState('shared/api-contract.json');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [repositoryTab, setRepositoryTab] = useState<'code' | 'changes' | 'activity'>('code');
  const [repositoryQuery, setRepositoryQuery] = useState('');
  const [repositoryBranch, setRepositoryBranch] = useState('main');
  const [copied, setCopied] = useState(false);
  const [previewStatus, setPreviewStatus] = useState('');
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    void getLiveConfig().then(setConfig).catch(cause => {
      setError(cause instanceof Error ? cause.message : 'Could not load agent configuration.');
    });
    if (repositoryMode) {
      void getLiveRuns().then(runs => {
        const latest = runs[0];
        if (!latest) return;
        setRun(latest);
        setSelectedPath(latest.artifacts[0]?.path ?? 'shared/api-contract.json');
        setRepositoryBranch(`run/${latest.run_id.replace('run-', '')}`);
        void getLiveTraces(latest.run_id).then(setTraces).catch(() => undefined);
      }).catch(cause => setError(cause instanceof Error ? cause.message : 'Could not load recent runs.'));
    }
    return () => sourceRef.current?.close();
  }, [repositoryMode]);

  const artifacts = run?.artifacts ?? [];
  const visibleArtifacts = artifacts.filter(artifact => artifact.path.toLowerCase().includes(repositoryQuery.toLowerCase()));
  const selected = artifacts.find(item => item.path === selectedPath) ?? artifacts[0];
  const roleStatus = useMemo(() => Object.fromEntries(roleIds.map(role => {
    const roleEvents = events.filter(event => event.agent_id === role);
    const latest = roleEvents.at(-1)?.event_type;
    if (latest === 'file_committed') return [role, 'Committed'];
    if (latest === 'proposal_staged') return [role, 'Ready to commit'];
    if (latest === 'intention_ready') return [role, 'Intention ready'];
    if (latest === 'intention_started') return [role, 'Planning'];
    if (latest === 'agent_started' || latest === 'proposal_received') return [role, 'Building'];
    return [role, 'Waiting'];
  })), [events]);

  async function refresh(runId: string) {
    const snapshot = await getLiveRun(runId);
    setRun(snapshot);
    void getLiveTraces(runId).then(setTraces).catch(() => undefined);
    if (!selectedPath && snapshot.artifacts.length) setSelectedPath(snapshot.artifacts[0].path);
    return snapshot;
  }

  async function start() {
    if (!objective.trim()) { setError('Describe the change you want the team to make.'); return; }
    setPreviewStatus('The interactive preview will appear below when the agents finish.');
    setBusy(true); setError(''); setEvents([]); setTraces([]); setRun(null); setSelectedPath('shared/api-contract.json');
    sourceRef.current?.close();
    try {
      const result = await startLiveRun(objective);
      await refresh(result.run_id);
      const source = subscribeLiveRun(result.run_id, event => {
        setEvents(current => [...current, event]);
        void refresh(result.run_id).catch(() => undefined);
        if (event.event_type === 'run_complete') {
          source.close();
          void refresh(result.run_id).then(snapshot => {
            if (!snapshot.preview_url) return;
            setPreviewStatus('Interactive app is ready below.');
          }).catch(() => setPreviewStatus('The run finished, but the preview could not be loaded.'));
        }
        if (event.event_type === 'run_failed') {
          source.close();
          setPreviewStatus('The agents did not produce a valid interactive app.');
        }
      });
      source.onerror = () => {
        source.close();
        void refresh(result.run_id).then(snapshot => {
          if (snapshot.status === 'complete' && snapshot.preview_url) {
            setPreviewStatus('Interactive app is ready below.');
          }
        }).catch(() => undefined);
      };
      sourceRef.current = source;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start the coding agents.');
    } finally { setBusy(false); }
  }

  function reset() {
    sourceRef.current?.close(); setRun(null); setEvents([]); setTraces([]); setError('');
    setPreviewStatus('');
    setSelectedPath('shared/api-contract.json');
  }

  function copyWorkspace() {
    if (!run?.workspace) return;
    void navigator.clipboard?.writeText(run.workspace);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  return <div className="live-shell">
    <header className="live-header">
      <div><p className="eyebrow">SYNAPSE · {repositoryMode ? 'REPOSITORY' : 'COORDINATED CODE WORKSPACE'}</p><h1>{repositoryMode ? 'Generated project repository' : 'See independent agents coordinate one change.'}</h1><p>{repositoryMode ? 'Browse the real files created by the latest coordinated run. Each entry is owned by one agent and is ready for continued development.' : 'Agents publish intent before writing. Synapse validates ownership, records the decision trail, and writes the approved result into a real local Git workspace.'}</p></div>{onHub && <button className="secondary" onClick={() => onHub(Boolean(run))}>Open workspace hub</button>}
    </header>

    {!repositoryMode && <section className="prompt-card">
      <div className="prompt-heading"><div><label htmlFor="objective">Project objective</label><small>{config ? `3 role providers · ${config.configured ? 'ready' : 'setup required'}` : 'Checking configuration…'}</small></div>{run && <span className={`run-status status-${run.status}`}>{run.status}</span>}</div>
      <textarea id="objective" value={objective} placeholder="Describe the change this team should implement…" maxLength={2000} onChange={event => setObjective(event.target.value)} disabled={busy || !!run} />
      <div className="prompt-actions"><button className="primary" onClick={start} disabled={busy || !!run || !config?.configured || !objective.trim()}>{busy ? 'Starting…' : 'Run coordinated change'}</button>{run && <button className="secondary" onClick={reset} disabled={run.status === 'planning' || run.status === 'building'}>New run</button>}{run?.preview_url && <button className="secondary" onClick={() => openFinishedApp(run.preview_url!)}>Open interactive app <ExternalLink size={14} /></button>}<span>{previewStatus || (run?.git_repository ? `Git workspace: ${run.workspace}` : 'Choose an objective to create a Git-backed workspace.')}</span></div>
      {config && !config.configured && <div className="setup-note"><strong>Three agent APIs needed.</strong> Set <code>BACKEND_PROVIDER</code>, <code>FRONTEND_PROVIDER</code>, and <code>QA_PROVIDER</code>, plus the matching API keys in <code>.env</code>, then restart the API.</div>}
    </section>}

    {error && <div className="error-card"><strong>Live run error</strong><pre>{error}</pre></div>}

    {repositoryMode && <><div className="repository-controls"><label><GitBranch size={15} /><span className="sr-only">Branch</span><select value={repositoryBranch} onChange={event => setRepositoryBranch(event.target.value)}><option value={repositoryBranch}>{repositoryBranch}</option><option value="main">main</option></select></label><div className="repository-search"><Search size={15} /><input aria-label="Find a generated file" placeholder="Find a file" value={repositoryQuery} onChange={event => setRepositoryQuery(event.target.value)} /></div><button onClick={copyWorkspace} disabled={!run?.workspace}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? 'Copied path' : 'Copy workspace path'}</button><a href="https://github.com/Jeewant05/VT-Hacks-14-Project" target="_blank" rel="noreferrer">GitHub <ExternalLink size={14} /></a></div><div className="repository-tabs" role="tablist"><button role="tab" aria-selected={repositoryTab === 'code'} onClick={() => setRepositoryTab('code')}><FileCode2 size={15} />Code <span>{artifacts.length}</span></button><button role="tab" aria-selected={repositoryTab === 'changes'} onClick={() => setRepositoryTab('changes')}><GitCompareArrows size={15} />Changes <span>{artifacts.length}</span></button><button role="tab" aria-selected={repositoryTab === 'activity'} onClick={() => setRepositoryTab('activity')}><History size={15} />Activity <span>{traces.length}</span></button></div></>}

    {!repositoryMode && <section className="live-agent-grid">{roleIds.map(agent => {
      const details = config?.roles.find(role => role.id === agent);
      const count = artifacts.filter(item => item.agent_id === agent).length;
      return <article className={`agent-card-live state-${roleStatus[agent].toLowerCase().replaceAll(' ', '-')}`} key={agent}><div className="agent-dot" /><div><h2>{details?.title ?? agent}<span className={details?.configured ? 'api-ready' : 'api-missing'}>{details?.configured ? 'API ready' : 'API missing'}</span></h2><p>{details?.responsibility}</p>{details?.configured && <p className="agent-provider">{details.provider} · {details.model}</p>}{run?.intentions[agent] && <blockquote className="agent-intention"><b>Intention</b>{run.intentions[agent]}</blockquote>}<small>{roleStatus[agent]}{count ? ` · ${count} file${count === 1 ? '' : 's'}` : ''}</small></div></article>;
    })}</section>}

    {!repositoryMode && run && <section className="panel live-app-preview">
      <div className="live-preview-heading"><div><p className="eyebrow">GENERATED APPLICATION</p><h2>Interactive preview</h2><span>{previewStatus}</span></div>{run.preview_url && <button className="secondary" onClick={() => openFinishedApp(run.preview_url!)}>Expand <ExternalLink size={14} /></button>}</div>
      {run.preview_url
        ? <iframe title="Generated interactive application" src={run.preview_url} sandbox="allow-scripts" referrerPolicy="no-referrer" />
        : <div className={`live-preview-waiting ${run.status === 'failed' ? 'failed' : ''}`}>{run.status === 'failed' ? <><strong>Preview unavailable</strong><p>{run.failure_title ?? 'The generated application did not pass validation.'}</p></> : <><LoaderCircle size={28} className="spin" /><strong>Building your application…</strong><p>The preview will load here automatically after all three agents finish.</p></>}</div>}
    </section>}

    <section className={`live-workspace ${repositoryMode ? 'repository-workspace' : ''}`}>
      {(!repositoryMode || repositoryTab === 'code') && <div className="panel artifact-panel"><div className="panel-title"><h2>{repositoryMode ? 'Files changed in this run' : 'Generated project'}</h2><span>{visibleArtifacts.length} files</span></div>{repositoryMode && <div className="repo-status-bar"><span><GitBranch size={14} /> {repositoryBranch}</span><span><GitCompareArrows size={14} /> {artifacts.length} added files</span><span><UploadCloud size={14} /> Remote push not configured</span></div>}<div className="artifact-browser"><nav>{visibleArtifacts.map(artifact => <ArtifactButton key={artifact.path} artifact={artifact} active={artifact.path === selected?.path} onClick={() => setSelectedPath(artifact.path)} />)}{!visibleArtifacts.length && <p className="empty">{artifacts.length ? 'No files match your search.' : 'Run the agents first; the latest generated files will appear here.'}</p>}</nav><div className="artifact-preview"><div><b>{selected?.path ?? 'No file selected'}</b>{selected && <span>{selected.agent_id}</span>}</div>{repositoryMode && selected ? <RepositoryDiff artifact={selected} /> : <pre>{selected?.content ?? 'Start a run to generate a project.'}</pre>}</div></div></div>}
      {repositoryMode && repositoryTab === 'changes' && <div className="panel repository-full-panel"><RepositoryChanges artifacts={visibleArtifacts} onSelect={path => { setSelectedPath(path); setRepositoryTab('code'); }} /></div>}
      {repositoryMode && repositoryTab === 'activity' && <div className="panel repository-full-panel"><RepositoryActivity traces={traces} /></div>}
      {!repositoryMode && <div className="panel activity-panel-live"><RepositoryActivity traces={traces} waiting={Boolean(run)} /></div>}
    </section>
  </div>;
}

function openFinishedApp(previewUrl: string) {
  window.open(previewUrl, '_blank', 'noopener,noreferrer');
}

function RepositoryActivity({ traces, waiting = false }: { traces: LiveTrace[]; waiting?: boolean }) {
  return <><div className="panel-title"><h2>Persisted activity</h2><span>{traces.length} events</span></div><ol className="event-list">{traces.map(trace => <li key={trace.trace_id}><b>{trace.agent_id ?? 'coordinator'}</b><span>{trace.event_type.replaceAll('_', ' ')}</span><p>{typeof trace.payload.message === 'string' ? trace.payload.message : 'Trace event recorded.'}</p><small>{trace.run_id ?? trace.source} · {new Date(trace.timestamp).toLocaleTimeString()}</small></li>)}{!traces.length && <li className="empty">{waiting ? <><LoaderCircle size={15} className="spin" /> Waiting for persisted activity…</> : 'No activity has been recorded for this run.'}</li>}</ol></>;
}

function RepositoryChanges({ artifacts, onSelect }: { artifacts: LiveArtifact[]; onSelect: (path: string) => void }) {
  return <><div className="panel-title"><h2>Change set</h2><span>{artifacts.length} additions</span></div><div className="repo-commit-note"><GitCommitHorizontal size={16} /><div><strong>Ready for continued development</strong><span>Generated files are in the local Git workspace. Add a remote when you are ready to commit and push.</span></div></div><ol className="event-list">{artifacts.map(artifact => <li key={artifact.path}><FileCode2 size={15} /><b>A</b><button className="repo-file-link" onClick={() => onSelect(artifact.path)}>{artifact.path}</button><small>{artifact.agent_id}</small></li>)}{!artifacts.length && <li className="empty">No generated changes yet.</li>}</ol></>;
}

function RepositoryDiff({ artifact }: { artifact: LiveArtifact }) {
  const lines = artifact.content.split('\n');
  return <div className="repo-diff"><div className="repo-diff-caption"><span>New file</span><span>{lines.length - 1} lines</span></div><pre>{lines.map((line, index) => <span key={index} className="repo-diff-line"><i>{index + 1}</i><b>+</b><code>{line || ' '}</code></span>)}</pre></div>;
}

function ArtifactButton({ artifact, active, onClick }: { artifact: LiveArtifact; active: boolean; onClick: () => void }) {
  return <button className={active ? 'active' : ''} onClick={onClick}><span>{artifact.path}</span><small>{artifact.agent_id}</small></button>;
}
