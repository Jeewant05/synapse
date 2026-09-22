export type LiveEvent = {
  agent_id: string;
  event_type: string;
  message: string;
  timestamp: string;
  path?: string;
};

export type LiveArtifact = { path: string; agent_id: string; content: string };
export type LiveSnapshot = {
  run_id: string;
  objective: string;
  status: 'queued' | 'planning' | 'building' | 'complete' | 'failed';
  workspace: string;
  git_repository: boolean;
  artifacts: LiveArtifact[];
  intentions: Record<string, string>;
  reports: Record<string, string>;
  preview_url: string | null;
  // Why a failed run failed, from the server; null while running or on success.
  error?: string | null;
  failure_title?: string | null;
};
export type LiveConfig = {
  configured: boolean;
  model: string;
  roles: {
    id: string;
    title: string;
    responsibility: string;
    configured: boolean;
    provider: string | null;
    model: string | null;
  }[];
};
export type LiveTrace = {
  trace_id: string;
  source: string;
  event_type: string;
  timestamp: string;
  run_id: string | null;
  agent_id: string | null;
  payload: { message?: unknown };
};

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export async function getLiveConfig(): Promise<LiveConfig> {
  return json(await fetch('/api/live/config'));
}

export async function startLiveRun(objective: string): Promise<{ run_id: string; status: string }> {
  return json(await fetch('/api/live/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ objective }),
  }));
}

export async function getLiveRun(runId: string): Promise<LiveSnapshot> {
  return json(await fetch(`/api/live/runs/${runId}`));
}

export async function getLiveRuns(): Promise<LiveSnapshot[]> {
  return json(await fetch('/api/live/runs'));
}

export async function getLiveTraces(runId?: string): Promise<LiveTrace[]> {
  const suffix = runId ? `&run_id=${encodeURIComponent(runId)}` : '';
  return json(await fetch(`/api/traces?limit=30${suffix}`));
}

export function subscribeLiveRun(runId: string, onEvent: (event: LiveEvent) => void): EventSource {
  const source = new EventSource(`/api/live/runs/${runId}/events`);
  source.onmessage = event => onEvent(JSON.parse(event.data) as LiveEvent);
  return source;
}
