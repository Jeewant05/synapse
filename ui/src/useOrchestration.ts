import { useCallback, useEffect, useState } from 'react';
import type { components } from './api.generated';

export type OrchestrationState = components['schemas']['OrchestrationState'];
const objectiveId = 'objective-oauth';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (response.ok) return response.json() as Promise<T>;
  const body = await response.json().catch(() => ({})) as { detail?: string };
  throw new Error(body.detail || `Orchestration returned ${response.status}.`);
}

export function useOrchestration() {
  const [state, setState] = useState<OrchestrationState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const next = await request<OrchestrationState>(`/api/objectives/${objectiveId}/status`);
      setState(next); setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Orchestration is unavailable.');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 2500);
    return () => clearInterval(timer);
  }, [refresh]);

  const create = useCallback(async () => {
    setLoading(true);
    try {
      const next = await request<OrchestrationState>('/api/objectives', { method: 'POST' });
      setState(next); setError(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not create objective.'); }
    finally { setLoading(false); }
  }, []);

  return { state, error, loading, refresh, create };
}
