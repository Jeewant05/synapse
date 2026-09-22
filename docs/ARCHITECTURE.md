# Synapse architecture — what each part is for

Synapse is a coordinator that sits between coding agents and a shared codebase. Agents
declare intent before editing. The coordinator compares intents with fixed rules, blocks
collisions, attaches the team's past decision, and accepts code only inside assigned scope.

Git catches conflicts after code is written. Synapse catches them before.

## Layers

| Layer | Files | Purpose |
|---|---|---|
| **Coordinator** (the product) | `server/app/service.py`, `coordinator.py`, `routes.py`, `store.py` | State machine for agents and workstreams. Collision rules. Scope enforcement. Event log. |
| **Adapters** (sponsor seams) | `server/app/adapters.py`, `server/app/ans/identity.py`, `tracing.py` | Identity verification (GoDaddy ANS) and event delivery (Databricks), each with a local fallback selected by `.env`. Decision memory is a local fixture. |
| **Live agents** (demo scaffolding) | `server/app/live_agents.py`, `providers.py`, `live_routes.py` | Three model-driven agents that publish intentions, generate scoped project files, and stage them for validation. One provider and model per role; Virginia Tech ARC by default, Gemini or Hugging Face by `.env`. |
| **Dashboard** | `ui/` | Drives the coordinator scene by calling the HTTP API. Renders coordinator events. Separate live-agent panel. |
| **Scripted clients** | `agents/clients.py`, `scripts/scenario.sh` | Deterministic versions of the scene. Fail-safe when LLMs or wifi are unavailable. |

## The coordinator, step by step

1. **Join** `POST /agents/{id}/join` — identity adapter verifies the agent. Unverified agents get 403 and an `agent_rejected` event.
2. **Claim** `POST /workstreams/{id}/claim` — only a verified agent, only its own workstream. `pending → active`.
3. **Declare** `POST /workstreams/{id}/declare` — the agent states the API contract it provides or consumes. The coordinator then re-runs both collision rules across all workstreams:
   - *File rule*: two workstreams own overlapping paths (glob-aware).
   - *Contract rule*: same method + path, one provides, one consumes, consumer expects a field the provider does not return.
   Each new conflict gets the most relevant past decision attached from memory (`decision_id`, text in `recommendation`). Affected workstreams become `blocked`.
4. **Scope** `POST /workstreams/{id}/scope` — the coordinator (or a human) reassigns `owned_paths`. Conflicts that no longer fire are marked `resolved`; workstreams return to `active`.
5. **Submit** `POST /workstreams/{id}/submit` — a ChangeSet (files, contract, test results). Rejected with 409 if: an open conflict names this workstream; the contract differs from what was declared; any file is outside the workstream's scope; any file overlaps an already-accepted ChangeSet. Otherwise `complete`. When all workstreams are complete, the objective is.
6. **Reset** `POST /reset` — back to the seeded fixture.

Every transition: take a lock → read state → mutate → append `Event` → write state → hand the event to memory/tracing. State is one JSON document in SQLite. Adapter failures are logged and never propagate; the demo continues on the local path.

Event types (fixed; the UI and agents key off them): `agent_joined, agent_rejected, workstream_claimed, contract_declared, conflict_opened, conflict_resolved, scope_reassigned, changeset_submitted, changeset_rejected, workstream_completed, objective_completed`.

## Why fixed rules and not an LLM

Collision detection is deterministic on purpose. The same inputs produce the same conflicts on stage as in tests. An LLM proposes; the coordinator decides.

## Sponsor integrations

- **GoDaddy ANS** → `IdentityAdapter.verify(agent)`. Turn on with `IDENTITY_MODE=ans`. Proves who each agent is before it may act; a revoked agent is refused at join.
- **Databricks** → `TraceSink.log`. Turn on with `TRACE_MODE=databricks`. Every coordinator event is delivered to a Delta table and read back at `GET /api/traces`. `MemoryAdapter.search` is a local fixture in every mode; past decisions are read from the seeded fixture, not from Databricks.

Both default to local fixtures so a fresh clone runs with no credentials. Flipping one `.env` line switches modes; that is the fail-safe.

## Fail-safe chain, best to worst

1. Dashboard + live LLM agents + real ANS + real Databricks
2. Flip `IDENTITY_MODE` / `TRACE_MODE` back to local
3. Open the credential-free guided simulation
4. `bash scripts/scenario.sh` in a terminal, Swagger at `/docs`
5. Recorded video

Everything from 2 down works with no network. Tagged commits: `demo-v1` (two-agent contract scene), `demo-v2` (three-agent file scene + live agents).
