import { useState } from 'react';
import { LiveDashboard } from './LiveDashboard';
import { SimulationWorkspace } from './SimulationWorkspace';

export function App() {
  const [hub, setHub] = useState(false);
  const [hasCurrentRun, setHasCurrentRun] = useState(false);
  return hub
    ? <SimulationWorkspace hasCurrentRun={hasCurrentRun} onLive={() => setHub(false)} />
    : <LiveDashboard onHub={hasRun => { setHasCurrentRun(hasRun); setHub(true); }} />;
}
