import { describe, expect, it } from 'vitest';
import type { WorkspaceState } from './useCoordinator';
import { workspaceToDemo } from './useCoordinator';

const state = (overrides: Partial<WorkspaceState> = {}): WorkspaceState => ({
  phase: 'foundation',
  objective: { id: 'oauth', title: 'OAuth', description: 'Demo', acceptance_criteria: [], status: 'active' },
  agents: [
    { id: 'backend-agent', ans_name: 'backend.demo', role: 'backend', verified: false },
    { id: 'frontend-agent', ans_name: 'frontend.demo', role: 'frontend', verified: false },
    { id: 'telemetry-agent', ans_name: 'telemetry.demo', role: 'telemetry', verified: false },
  ],
  workstreams: [], conflicts: [], decisions: [], events: [], ...overrides,
});

describe('coordinator workspace presentation', () => {
  it('shows a ready workspace before coordinator events', () => {
    expect(workspaceToDemo(state()).phase).toBe('ready');
  });

  it('shows the server conflict while convergence is blocked', () => {
    const workspace = state({
      agents: state().agents?.map(agent => ({ ...agent, verified: true })),
      conflicts: [{ id: 'file:backend:frontend', type: 'file', workstream_ids: ['backend', 'frontend'], explanation: 'Overlap', conflicting_field: 'src/auth/session.ts', recommendation: 'Assign one owner', status: 'open' }],
      events: [{ event_id: 'evt-1', objective_id: 'oauth', event_type: 'conflict_opened', payload: { type: 'file' }, timestamp: '2026-09-19T14:00:00Z' }],
    });
    const demo = workspaceToDemo(workspace);
    expect(demo.phase).toBe('conflict');
    expect(demo.events[0]).toMatchObject({ title: 'File collision predicted', evidence: 'scope' });
  });

  it('stays in correction and submission states while API actions are pending', () => {
    const resolved = state({ events: [{ event_id: 'evt-2', objective_id: 'oauth', event_type: 'conflict_resolved', timestamp: '2026-09-19T14:01:00Z' }] });
    expect(workspaceToDemo(resolved, 'coordinate').phase).toBe('coordinating');
    expect(workspaceToDemo(resolved).phase).toBe('aligned');
    expect(workspaceToDemo(resolved, 'submit').phase).toBe('submitting');
  });

  it('uses objective completion as the final authority', () => {
    const complete = state({ objective: { ...state().objective!, status: 'complete' } });
    expect(workspaceToDemo(complete, 'submit').phase).toBe('complete');
  });
});
