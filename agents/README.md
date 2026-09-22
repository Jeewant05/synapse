# Gemini live-agent mode

The repository now includes a server-side Gemini foundation. It is intentionally
opt-in and does not expose the API key to the browser.

## Configure locally

Copy `.env.example` to `.env`, then set:

```dotenv
AGENT_PROVIDER=gemini
GEMINI_API_KEY=your_key_from_aistudio
GEMINI_MODEL=gemini-2.5-flash
```

Do not commit `.env` or the API key. Gemini API usage may have quotas or charges
separate from a Google One/Pro subscription.

The provider is in `server/app/gemini.py`, and the coordinated three-role runner
is in `server/app/live_agents.py`. The runner uses one shared
`shared/api-contract.json` file and records versioned events rather than allowing
agents to overwrite one another. The next UI integration should stream those
`AgentEvent` objects over Server-Sent Events or WebSockets.

The existing `npm run agent-test` remains the offline coordinator smoke test. It
does not call Gemini and does not incur API usage.

## Three-agent orchestration demo

`agents/demo_agents.py` defines the three registered contributors used by the
plan-before-write orchestration API. Start the app, then use this sequence:

```sh
curl -X POST http://127.0.0.1:8000/api/objectives
curl -X POST http://127.0.0.1:8000/api/agents/demo-agent-1/plan
curl -X POST http://127.0.0.1:8000/api/agents/demo-agent-2/plan
curl -X POST http://127.0.0.1:8000/api/agents/demo-agent-3/plan
curl http://127.0.0.1:8000/api/objectives/objective-oauth/status
```

The default plans intentionally contain the OAuth contract mismatch: Agent 1
provides `token` and `user`, while Agent 2 initially consumes `accessToken` and
`profile`. The kernel blocks execution before a write. Use the returned
contract-conflict ID with `/api/conflicts/{id}/resolve`, then approve the
proposal at `/api/conflicts/{id}/approve`. Only a workstream in `APPROVED` may
call `/api/agents/{agent_id}/execute`; submitted ChangeSets are checked against
the approved intention version, paths, symbols, contracts, database changes,
and tests.
