export type Phase = 'ready' | 'verifying' | 'context' | 'conflict' | 'coordinating' | 'aligned' | 'submitting' | 'complete';
export type DemoEvent = { id: string; title: string; detail: string; time: string; tone: 'neutral' | 'success' | 'warning'; evidence?: 'identity' | 'decision' | 'contract' | 'scope' | 'tests' };
export type DemoState = { phase: Phase; events: DemoEvent[] };

const initialEvent: DemoEvent = { id: 'objective', title: 'Objective created', detail: 'Three coding agents are ready to publish their planned file touches.', time: '00:00', tone: 'neutral' };
export const initialDemo: DemoState = { phase: 'ready', events: [initialEvent] };

export const hasIdentity = (phase: Phase) => !['ready', 'verifying'].includes(phase);
export const isAligned = (phase: Phase) => ['aligned', 'submitting', 'complete'].includes(phase);

export const decisions = [
  { id: 'ADR-001', title: 'Authentication response contract', component: 'Authentication', summary: 'All authentication responses use token and user.', content: 'POST /api/oauth returns a token string and a user object. Consumers must use these field names to keep the login experience consistent.', code: '{\n  "token": "string",\n  "user": { "id": "string", "name": "string" }\n}', relevant: true },
  { id: 'ADR-002', title: 'One owner per file', component: 'Coordination', summary: 'Shared session logic has one editor and two consumers.', content: 'The API agent owns session.ts. The UI and telemetry agents keep their work in separate modules and consume the published authentication contract.', code: 'API        src/auth/session.ts\nUI         src/components/login/**\nTelemetry  src/lib/analytics/**', relevant: true },
  { id: 'ADR-003', title: 'Review before convergence', component: 'Review', summary: 'Resolve incompatible declarations before final review.', content: 'Both workstreams must submit compatible ChangeSets before the objective can be marked complete. Convergence produces a review summary; it does not merge code.', code: 'open conflicts = 0\ncompatible manifests = 2', relevant: false },
];

export type DemoFile = { path: string; owner: 'You · API' | 'Teammate A · UI' | 'Teammate B · Telemetry'; added: number; removed: number; description: string; lines: { kind: 'context' | 'add' | 'remove'; text: string }[] };
export const demoFiles: DemoFile[] = [
  { path: 'src/api/auth/oauth.ts', owner: 'You · API', added: 8, removed: 2, description: 'Provide the approved OAuth response.', lines: [
    { kind: 'context', text: 'export async function oauth(request: Request) {' },
    { kind: 'add', text: '  const organization = await resolveOrganization(request);' },
    { kind: 'add', text: '  const session = await authenticate(request, organization);' },
    { kind: 'remove', text: '  return Response.json({ session });' },
    { kind: 'add', text: '  return Response.json({' },
    { kind: 'add', text: '    token: session.token,' },
    { kind: 'add', text: '    user: session.user,' },
    { kind: 'add', text: '  });' },
    { kind: 'context', text: '}' },
  ] },
  { path: 'src/auth/session.ts', owner: 'You · API', added: 6, removed: 1, description: 'The shared file stays with one owner after Synapse reorganizes the work.', lines: [
    { kind: 'context', text: 'export function establishSession(payload: OAuthResponse) {' },
    { kind: 'remove', text: '  return saveSession(payload);' },
    { kind: 'add', text: '  const session = normalizeSession(payload);' },
    { kind: 'add', text: '  emitSessionReady(session.id);' },
    { kind: 'add', text: '  return saveSession(session);' },
    { kind: 'context', text: '}' },
  ] },
  { path: 'src/api/auth/oauth.test.ts', owner: 'You · API', added: 12, removed: 0, description: 'Describe the expected response contract.', lines: [
    { kind: 'add', text: 'it("returns the approved auth contract", async () => {' },
    { kind: 'add', text: '  const result = await requestOAuth(demoOrganization);' },
    { kind: 'add', text: '  expect(result).toHaveProperty("token");' },
    { kind: 'add', text: '  expect(result).toHaveProperty("user");' },
    { kind: 'add', text: '});' },
  ] },
  { path: 'src/components/login/OrganizationLogin.tsx', owner: 'Teammate A · UI', added: 5, removed: 3, description: 'Consume the session API without editing the shared session module.', lines: [
    { kind: 'context', text: 'async function handleLogin() {' },
    { kind: 'context', text: '  const response = await fetch("/api/oauth", { method: "POST" });' },
    { kind: 'remove', text: '  const { accessToken, profile } = await response.json();' },
    { kind: 'remove', text: '  setSession(accessToken, profile);' },
    { kind: 'add', text: '  const { token, user } = await response.json();' },
    { kind: 'add', text: '  setSession(token, user);' },
    { kind: 'context', text: '}' },
  ] },
  { path: 'src/components/login/OrganizationLogin.test.tsx', owner: 'Teammate A · UI', added: 10, removed: 0, description: 'Describe the coordinated consumer behavior.', lines: [
    { kind: 'add', text: 'it("opens a session from the approved response", async () => {' },
    { kind: 'add', text: '  mockOAuth({ token: "demo-token", user: demoUser });' },
    { kind: 'add', text: '  await clickOrganizationLogin();' },
    { kind: 'add', text: '  expect(session.user).toEqual(demoUser);' },
    { kind: 'add', text: '});' },
  ] },
  { path: 'src/lib/analytics/authEvents.ts', owner: 'Teammate B · Telemetry', added: 9, removed: 0, description: 'Observe session events from a dedicated module.', lines: [
    { kind: 'add', text: 'export function trackLoginCompleted(session: Session) {' },
    { kind: 'add', text: '  analytics.capture("login_completed", {' },
    { kind: 'add', text: '    organizationId: session.organizationId,' },
    { kind: 'add', text: '    userId: session.user.id,' },
    { kind: 'add', text: '  });' },
    { kind: 'add', text: '}' },
  ] },
];

export const demoTests = [
  { name: 'OAuth returns token and user', owner: 'You · API' },
  { name: 'Organization context is required', owner: 'You · API' },
  { name: 'Login consumes the approved response', owner: 'Teammate A · UI' },
  { name: 'Authenticated user is displayed', owner: 'Teammate A · UI' },
  { name: 'Login completion emits once', owner: 'Teammate B · Telemetry' },
];
