import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { components } from './api.generated';
import { initialDemo, type DemoEvent, type DemoState, type Phase } from './demo';

export type WorkspaceState = components['schemas']['WorkspaceState'];
export type Health = components['schemas']['Health'];
export type TraceEvent = components['schemas']['TraceEvent'];
type ApiContract = components['schemas']['ApiContract'];
type ChangeSet = components['schemas']['ChangeSet'];
type PendingAction = 'start' | 'coordinate' | 'submit' | 'reset' | null;

const approvedFields = { token: 'string', user: 'object' };

const contract = (role: 'provides' | 'consumes', fields: Record<string, string>): ApiContract => ({
  method: 'POST', path: '/api/oauth', role, request_fields: {}, response_fields: fields,
});

function eventPresentation(event: NonNullable<WorkspaceState['events']>[number], index: number): DemoEvent {
  const source = typeof event.payload?.source === 'string' ? event.payload.source : 'coordinator';
  const definitions: Record<string, Omit<DemoEvent, 'id' | 'time'>> = {
    agent_joined: { title: 'Agent identity checked', detail: `${event.agent_id ?? 'Agent'} accepted by ${source}.`, tone: 'success', evidence: 'identity' },
    agent_rejected: { title: 'Agent rejected', detail: `${event.agent_id ?? 'Unknown agent'} could not join.`, tone: 'warning', evidence: 'identity' },
    workstream_claimed: { title: 'Workstream claimed', detail: `${event.workstream_id ?? 'Workstream'} scope assigned.`, tone: 'neutral' },
    contract_declared: { title: 'Contract declared', detail: `${event.workstream_id ?? 'Workstream'} declared POST /api/oauth.`, tone: 'neutral', evidence: 'contract' },
    conflict_opened: { title: event.payload?.type === 'file' ? 'File collision predicted' : 'Contract mismatch detected', detail: event.payload?.type === 'file' ? 'Two coding agents planned to edit the same file. Work paused before edits began.' : 'Convergence blocked; project decision attached.', tone: 'warning', evidence: event.payload?.type === 'file' ? 'scope' : 'contract' },
    scope_reassigned: { title: 'Ownership reassigned', detail: `${event.workstream_id ?? 'Workstream'} received a non-overlapping file scope.`, tone: 'neutral', evidence: 'scope' },
    conflict_resolved: { title: 'Planned collision cleared', detail: 'The proposed file scopes no longer overlap.', tone: 'success', evidence: 'scope' },
    changeset_rejected: { title: 'ChangeSet rejected', detail: 'An open conflict prevented submission.', tone: 'warning', evidence: 'contract' },
    changeset_submitted: { title: 'ChangeSet submitted', detail: `${event.workstream_id ?? 'Workstream'} manifest and test report accepted.`, tone: 'success', evidence: 'tests' },
    workstream_completed: { title: 'Workstream complete', detail: `${event.workstream_id ?? 'Workstream'} is ready for review.`, tone: 'success' },
    objective_completed: { title: 'Ready for Convergence review', detail: 'Three independent ChangeSets. No open conflicts.', tone: 'success', evidence: 'tests' },
  };
  const fallback = { title: event.event_type.replaceAll('_', ' '), detail: 'Coordinator event recorded.', tone: 'neutral' as const };
  const rendered = definitions[event.event_type] ?? fallback;
  const time = new Date(event.timestamp).toLocaleTimeString([], { minute: '2-digit', second: '2-digit' });
  return { id: event.event_id || `${event.event_type}-${index}`, time, ...rendered };
}

export function workspaceToDemo(state: WorkspaceState | null, pending: PendingAction = null): DemoState {
  if (!state) return initialDemo;
  const events = state.events ?? [];
  const openConflict = (state.conflicts ?? []).some(item => item.status === 'open');
  const verifiedCount = (state.agents ?? []).filter(agent => agent.verified).length;
  const resolved = events.some(event => event.event_type === 'conflict_resolved');
  let phase: Phase = 'ready';

  if (state.objective?.status === 'complete') phase = 'complete';
  else if (pending === 'submit') phase = 'submitting';
  else if (pending === 'coordinate') phase = 'coordinating';
  else if (openConflict) phase = 'conflict';
  else if (resolved) phase = 'aligned';
  else if (pending === 'start' && verifiedCount === 3) phase = 'context';
  else if (pending === 'start') phase = 'verifying';
  else if (verifiedCount > 0) phase = 'context';

  const presented = events.map(eventPresentation);
  return {
    phase,
    events: presented.length ? presented : [{
      id: 'objective', title: 'Objective created', detail: 'Three coding agents are ready to declare planned file touches.',
      time: 'Ready', tone: 'neutral',
    }],
  };
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let detail = `Coordinator returned ${response.status}.`;
  try {
    const body = await response.json() as { detail?: string | { msg?: string }[] };
    if (typeof body.detail === 'string') detail = body.detail;
    else if (Array.isArray(body.detail)) detail = body.detail.map(item => item.msg).filter(Boolean).join(', ') || detail;
  } catch { /* The HTTP status remains the useful recovery message. */ }
  throw new Error(detail);
}

export function useCoordinator() {
  const [workspace, setWorkspace] = useState<WorkspaceState | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const pendingRef = useRef<PendingAction>(null);

  const setAction = (action: PendingAction) => { pendingRef.current = action; setPending(action); };

  const request = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const headers: Record<string, string> = {};
    if (init?.body) headers['Content-Type'] = 'application/json';
    const response = await fetch(`/api${path}`, { ...init, headers });
    return parseResponse<T>(response);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [nextHealth, nextWorkspace] = await Promise.all([
        request<Health>('/health'), request<WorkspaceState>('/state'),
      ]);
      const nextTraces = await request<TraceEvent[]>('/traces?limit=12').catch(() => []);
      if (nextHealth.status !== 'ok') throw new Error('Coordinator health response was invalid.');
      if (!pendingRef.current) setWorkspace(nextWorkspace);
      setTraceEvents(nextTraces);
      setHealth(nextHealth); setError(null); setUpdatedAt(new Date());
      return true;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The local coordinator is unavailable.');
      return false;
    }
  }, [request]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { if (!pendingRef.current) void refresh(); }, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const post = useCallback(async (path: string, body?: unknown) => {
    const next = await request<WorkspaceState>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
    setWorkspace(next); setUpdatedAt(new Date());
    return next;
  }, [request]);

  const perform = useCallback(async (action: Exclude<PendingAction, null>, task: () => Promise<void>) => {
    setAction(action); setError(null);
    try { await task(); return true; }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Coordinator action failed.'); return false; }
    finally { setAction(null); }
  }, []);

  const start = useCallback(() => perform('start', async () => {
    // In ANS mode every privileged call needs a proof signed by an agent's
    // identity key, and a browser must never hold those. The server runs the
    // scripted agents instead; the dashboard polls /state to follow along.
    if (health?.dpop_required) {
      await request<{ status: string; detail?: string }>('/demo/run', { method: 'POST' })
        .then(result => { if (result.status !== 'complete') throw new Error(result.detail ?? 'Demo run failed.'); });
      await refresh();
      return;
    }
    await post('/reset');
    await post('/agents/backend-agent/join');
    await post('/agents/frontend-agent/join');
    await post('/agents/telemetry-agent/join');
    await post('/workstreams/backend/claim', { agent_id: 'backend-agent' });
    await post('/workstreams/frontend/claim', { agent_id: 'frontend-agent' });
    await post('/workstreams/telemetry/claim', { agent_id: 'telemetry-agent' });
    await post('/workstreams/backend/declare', { agent_id: 'backend-agent', contract: contract('provides', approvedFields) });
    await post('/workstreams/frontend/declare', { agent_id: 'frontend-agent', contract: contract('consumes', approvedFields) });
    await post('/workstreams/telemetry/declare', { agent_id: 'telemetry-agent', contract: contract('consumes', approvedFields) });
    // A collision is evidence for the operator, never a manual gate. The
    // coordinator records the conflicting intentions, assigns compatible
    // scopes, and submits the approved ChangeSets as one autonomous run.
    await post('/workstreams/backend/scope', { agent_id: 'backend-agent', owned_paths: ['src/api/auth/oauth.ts', 'src/auth/session.ts', 'src/api/auth/oauth.test.ts'] });
    await post('/workstreams/frontend/scope', { agent_id: 'frontend-agent', owned_paths: ['src/components/login/OrganizationLogin.tsx', 'src/components/login/OrganizationLogin.test.tsx'] });
    await post('/workstreams/telemetry/scope', { agent_id: 'telemetry-agent', owned_paths: ['src/lib/analytics/authEvents.ts'] });
    const backend: ChangeSet = {
      id: 'cs-backend-demo', workstream_id: 'backend', agent_id: 'backend-agent',
      files: ['src/api/auth/oauth.ts', 'src/auth/session.ts', 'src/api/auth/oauth.test.ts'], contract: contract('provides', approvedFields),
      tests: [{ name: 'OAuth returns token and user', status: 'passed', source: 'agent_reported' }, { name: 'Organization context is required', status: 'passed', source: 'agent_reported' }],
    };
    const frontend: ChangeSet = {
      id: 'cs-frontend-demo', workstream_id: 'frontend', agent_id: 'frontend-agent',
      files: ['src/components/login/OrganizationLogin.tsx', 'src/components/login/OrganizationLogin.test.tsx'], contract: contract('consumes', approvedFields),
      tests: [{ name: 'Login consumes the approved response', status: 'passed', source: 'agent_reported' }, { name: 'Authenticated user is displayed', status: 'passed', source: 'agent_reported' }],
    };
    const telemetry: ChangeSet = {
      id: 'cs-telemetry-demo', workstream_id: 'telemetry', agent_id: 'telemetry-agent',
      files: ['src/lib/analytics/authEvents.ts'], contract: contract('consumes', approvedFields),
      tests: [{ name: 'Login completion emits once', status: 'passed', source: 'agent_reported' }],
    };
    await post('/workstreams/backend/submit', backend);
    await post('/workstreams/frontend/submit', frontend);
    await post('/workstreams/telemetry/submit', telemetry);
  }), [perform, post, health, request, refresh]);

  const applyPlan = useCallback(() => perform('coordinate', async () => {
    await post('/workstreams/backend/scope', { agent_id: 'backend-agent', owned_paths: ['src/api/auth/oauth.ts', 'src/auth/session.ts', 'src/api/auth/oauth.test.ts'] });
    await post('/workstreams/frontend/scope', { agent_id: 'frontend-agent', owned_paths: ['src/components/login/OrganizationLogin.tsx', 'src/components/login/OrganizationLogin.test.tsx'] });
    await post('/workstreams/telemetry/scope', { agent_id: 'telemetry-agent', owned_paths: ['src/lib/analytics/authEvents.ts'] });
  }), [perform, post]);

  const submit = useCallback(() => perform('submit', async () => {
    const backend: ChangeSet = {
      id: 'cs-backend-demo', workstream_id: 'backend', agent_id: 'backend-agent',
      files: ['src/api/auth/oauth.ts', 'src/auth/session.ts', 'src/api/auth/oauth.test.ts'],
      contract: contract('provides', approvedFields),
      tests: [
        { name: 'OAuth returns token and user', status: 'passed', source: 'agent_reported' },
        { name: 'Organization context is required', status: 'passed', source: 'agent_reported' },
      ],
    };
    const frontend: ChangeSet = {
      id: 'cs-frontend-demo', workstream_id: 'frontend', agent_id: 'frontend-agent',
      files: ['src/components/login/OrganizationLogin.tsx', 'src/components/login/OrganizationLogin.test.tsx'],
      contract: contract('consumes', approvedFields),
      tests: [
        { name: 'Login consumes the approved response', status: 'passed', source: 'agent_reported' },
        { name: 'Authenticated user is displayed', status: 'passed', source: 'agent_reported' },
      ],
    };
    const telemetry: ChangeSet = {
      id: 'cs-telemetry-demo', workstream_id: 'telemetry', agent_id: 'telemetry-agent',
      files: ['src/lib/analytics/authEvents.ts'],
      contract: contract('consumes', approvedFields),
      tests: [
        { name: 'Login completion emits once', status: 'passed', source: 'agent_reported' },
      ],
    };
    await post('/workstreams/backend/submit', backend);
    await post('/workstreams/frontend/submit', frontend);
    await post('/workstreams/telemetry/submit', telemetry);
  }), [perform, post]);

  const reset = useCallback(() => perform('reset', async () => { await post('/reset'); }), [perform, post]);
  const demo = useMemo(() => workspaceToDemo(workspace, pending), [workspace, pending]);

  return {
    workspace, health, traceEvents, demo, error, pending, updatedAt,
    connected: Boolean(health), busy: pending !== null,
    refresh, start, applyPlan, submit, reset,
  };
}
