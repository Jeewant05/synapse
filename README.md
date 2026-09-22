# Synapse

A hackathon demo of pre-merge coordination between three coding agents. Each agent publishes its intended file touches before implementation; the coordinator detects overlap, applies exclusive ownership, and accepts only conflict-free ChangeSets.

## Quick start

Prerequisites: Git, Python 3.13, [uv](https://docs.astral.sh/uv/), and Node 22.12+ (22.x) with npm. `.python-version` and `.nvmrc` record the development runtimes.

```sh
git clone https://github.com/Jeewant05/VT-Hacks-14-Project.git
cd VT-Hacks-14-Project
cp .env.example .env
npm run setup
npm run dev
```

Open http://127.0.0.1:5173. The dashboard opens on the live coding workspace; **Open workspace hub** shows the coordinator, integrations, and project memory. API documentation is at http://127.0.0.1:8000/docs. Stop both services with Ctrl+C.

No credentials are required for the guided simulation. The default `IDENTITY_MODE=mock`
uses a local allowlist: mock identity results are fixtures, not ANS verification, and
seeded decisions are local fixtures.

## Deployments

| App | URL | Shows |
| --- | --- | --- |
| `synapse-vt` | https://synapse-vt.us | ANS-verified coordinator: badge-tier identity, DPoP proofs, per-agent cards. Live agents are not configured here yet |
| `synapse-vt-live` | https://synapse-vt-live.fly.dev | Latest dashboard, live three-agent runs on Gemini, Databricks trace persistence (mock identity) |

Reset on either site asks for the operator token. Deployment details, secrets and known issues: [docs/DEPLOY.md](docs/DEPLOY.md).

## Agent Name Service

`IDENTITY_MODE=ans` performs real Agent Name Service verification: a transparency-log
badge for identity and liveness, plus an ANS-6 Method B proof of possession on every
privileged call. It needs registered agents and credentials — see [docs/ANS.md](docs/ANS.md).
In that mode the dashboard is driven by the server-side runner, because a browser cannot
hold agent identity keys.


## Live coding demo (Virginia Tech ARC)

Mint an API key at [llm.arc.vt.edu](https://llm.arc.vt.edu), then add it to `.env`:

```sh
BACKEND_PROVIDER=arc
FRONTEND_PROVIDER=arc
QA_PROVIDER=arc
ARC_API_KEY_BACKEND=
ARC_API_KEY_FRONTEND=
ARC_API_KEY_QA=
```

ARC is an OpenAI-compatible chat-completions API at `llm-api.arc.vt.edu`, and each role picks its model through `ARC_MODEL_BACKEND`, `_FRONTEND`, and `_QA` (default `gpt-oss-120b`). Gemini and Hugging Face remain available through the `BACKEND_PROVIDER`, `FRONTEND_PROVIDER`, and `QA_PROVIDER` values. Provider-side limits can still apply, so transient 429 and 5xx responses are retried with bounded backoff.

First, all three agents generate an intention in parallel. The coordinator shares those intentions with every agent, then starts implementation: backend and frontend build concurrently, and integration reviews their staged output. Nothing is written until every proposal passes ownership, path, duplicate, and size validation.

Every proposed path is checked against its role (`backend/**`, `frontend/**`, or `integration/**`) before the coordinator writes it under `.local/live-runs/<run-id>`; generated code never edits the Synapse repository and is not executed automatically.

## Databricks trace layer

Set `TRACE_MODE=databricks` with `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_WAREHOUSE_ID`, `DATABRICKS_CATALOG`, and `DATABRICKS_SCHEMA` to persist coordinator and live-agent events. Create the destination from [the trace schema](docs/databricks-trace-schema.sql). `GET /api/traces` reads the newest records; `GET /api/traces?run_id=run-…` filters a live run. Incomplete settings safely retain the in-process cache trace store.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run setup` | Install locked Python/npm dependencies, export contracts, seed SQLite |
| `npm run dev` | Start API on 8000 and UI on 5173 |
| `npm run dev:server` / `npm run dev:ui` | Start one service |
| `npm run contracts` | Export Python schemas/OpenAPI and generate UI API types |
| `npm run seed` | Seed fixtures if no objective exists; preserve existing state |
| `npm run reset` | Replace local workspace state with initial fixtures |
| `npm run check` | Python lint, foundation smoke tests, TypeScript check, UI build |
| `npm run rehearse` | Check a running, seeded API; not the final product rehearsal |
| `npm run ans -- <step>` | Drive ANS registration (`generate`, `register`, `records`, `acme`, `dns`, `status`, `certs`) |
| `npm run ans:check` | Resolve the seeded ANSNames and report what a verifier would decide |

The database defaults to `.local/synapse.db`. Reset touches only local workspace state; it does not contact sponsor services. Do not use the local reset command against any future shared or production store.

## Layout and ownership

| Directory | Purpose | Owner |
| --- | --- | --- |
| `server/` | API, models, SQLite, coordinator and integration interfaces | P1; P3/P4 implement their adapters |
| `ui/` | React/TypeScript dashboard | P2 |
| `agents/` | Scripted coordinator clients and smoke checks | P2 with P1 |
| `.local/live-runs/` | Ignored, isolated output from live agent runs | Coordinator |
| `contracts/` | Generated schemas and sample state | P1 approves interface changes |
| `scripts/` | Setup support, contracts, seed/reset, smoke check | P1 |
| `docs/` | Scope, ownership, demo instructions | All |

Pydantic models in `server/app/models.py` are the source of truth. Run `npm run contracts` after model or endpoint changes, and commit all generated files. Do not edit `ui/src/api.generated.ts` manually. Exact dependency resolutions are committed in `uv.lock` and `package-lock.json`; use `uv sync --locked` and `npm ci` for repeatable installs.

The coordinator exposes health/state reads plus agent join, workstream claim, contract declaration, scope reassignment, ChangeSet submission, and local reset endpoints. The guided UI calls these endpoints through the Vite `/api` proxy. An MCP server (`uv run python -m server.mcp_server`, stdio) exposes the same six operations as tools, so any MCP-capable coding agent can join, declare, scope and submit through the coordinator. Deployment is described in [docs/DEPLOY.md](docs/DEPLOY.md).

The stricter orchestration API lives under `/api`. It registers three codebase demo agents, requires a structured Intention Document before execution, blocks deterministic file/symbol/contract/dependency/permission conflicts, validates submitted ChangeSets against their approved intention, and records the workflow through the configured trace sink. See [the three-agent workflow](agents/README.md#three-agent-orchestration-demo).

The checked-in [orchestration v1 contract](contracts/orchestration-api-v1.json) is frozen for frontend work. The verification suite rejects changes to its `/api` operations or response schemas. Additive API work belongs in a new versioned endpoint or a deliberate v2 contract update.

See [scope and team handoff](docs/SETUP.md) and [demo runbook](docs/DEMO.md). Keep secrets in ignored `.env`; never commit credentials, keys, or local database files.
