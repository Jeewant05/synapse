# Synapse

Pre-merge coordination for AI coding agents working on the same repository.

When two or more coding agents (Claude Code, Codex, Copilot, or scripted workers) touch one codebase, Git only notices the collision at merge time. Synapse moves that check earlier: every agent declares the files and contracts it intends to touch before it writes anything, a coordinator enforces exclusive ownership, and only ChangeSets that match an approved declaration are accepted. Every decision is recorded so you can see why a change was allowed or blocked.

**Status:** early and actively changing. This started as a team project at VT Hacks 14 (Sep 2026) and is now being continued as a single-developer tool. The code here is the hackathon snapshot; the [Roadmap](#roadmap) says where it is going.

## How it works

1. An agent joins the coordinator and claims a workstream.
2. It publishes an intention: the files, symbols, and API contracts it plans to change.
3. The coordinator checks that intention against every other active declaration and rejects overlaps, out-of-scope paths, and contract conflicts.
4. The agent implements and submits a ChangeSet. The coordinator validates it against the approved intention before anything is written.
5. Every join, declaration, block, and commit is written to a trace so the decision trail survives the run.

Agents talk to the coordinator over HTTP or over MCP, so any MCP-capable coding agent can participate without custom glue.

## Quick start

Prerequisites: Git, Python 3.13, [uv](https://docs.astral.sh/uv/), and Node 22.12+ with npm. `.python-version` and `.nvmrc` record the development runtimes.

```sh
git clone https://github.com/Jeewant05/synapse.git
cd synapse
cp .env.example .env
npm run setup
npm run dev
```

Open http://127.0.0.1:5173 for the local dashboard. API docs are at http://127.0.0.1:8000/docs. Stop both with Ctrl+C.

No credentials are needed for the guided simulation. The default `IDENTITY_MODE=mock` uses a local allowlist, and the seeded decisions are local fixtures.

## Using it from a coding agent (MCP)

The coordinator ships as a stdio MCP server exposing six tools: `join`, `claim`, `declare`, `scope`, `submit`, and `get_state`.

```sh
uv run python -m server.mcp_server
```

`.mcp.json` in the repo root is a working config for Claude Code; other MCP clients point at the same command.

## Live agents

Synapse can drive three LLM-backed agents (backend, frontend, integration) through a full run. Each role picks a provider and model via `.env`:

```sh
BACKEND_PROVIDER=arc
FRONTEND_PROVIDER=arc
QA_PROVIDER=arc
ARC_API_KEY_BACKEND=
ARC_API_KEY_FRONTEND=
ARC_API_KEY_QA=
```

`arc` is Virginia Tech's OpenAI-compatible endpoint at `llm-api.arc.vt.edu` (key from [llm.arc.vt.edu](https://llm.arc.vt.edu)); `gemini` and `huggingface` are also supported. Models are chosen per role with `ARC_MODEL_BACKEND`, `_FRONTEND`, `_QA` (default `gpt-oss-120b`). Transient 429/5xx responses are retried with bounded backoff.

A run has two phases. All three agents generate intentions in parallel; the coordinator shares them, then backend and frontend implement concurrently while integration reviews their staged output. Every proposed path is checked against its role (`backend/**`, `frontend/**`, `integration/**`) and written under `.local/live-runs/<run-id>`. Generated code never touches the Synapse repo itself and is never executed automatically.

## Configuration

| Variable | Values | Notes |
| --- | --- | --- |
| `IDENTITY_MODE` | `mock` (default), `ans` | `ans` performs real GoDaddy Agent Name Service verification and needs registered agents and credentials; see [docs/ANS.md](docs/ANS.md). Kept from the hackathon; not the direction of the project. |
| `TRACE_MODE` | `cache` (default), `databricks` | `databricks` persists traces to a Delta table; needs `DATABRICKS_HOST`, `_TOKEN`, `_WAREHOUSE_ID`, `_CATALOG`, `_SCHEMA` and [the trace schema](docs/databricks-trace-schema.sql). Incomplete settings fall back to the in-process store. |
| `DATABASE_PATH` | path | SQLite workspace state, default `.local/synapse.db` |

`GET /api/traces` reads the newest trace records; `?run_id=run-…` filters a single live run.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run setup` | Install locked Python/npm dependencies, export contracts, seed SQLite |
| `npm run dev` | Start API on 8000 and UI on 5173 |
| `npm run dev:server` / `npm run dev:ui` | Start one service |
| `npm run contracts` | Export Python schemas/OpenAPI and generate UI API types |
| `npm run seed` | Seed fixtures if no objective exists; preserve existing state |
| `npm run reset` | Replace local workspace state with initial fixtures |
| `npm run check` | Python lint, tests, TypeScript check, UI build |
| `npm run rehearse` | Smoke-check a running, seeded API |
| `npm run ans -- <step>` | ANS registration steps (`generate`, `register`, `records`, `acme`, `dns`, `status`, `certs`) |
| `npm run ans:check` | Resolve seeded ANSNames and report what a verifier would decide |

Use `uv sync --locked` and `npm ci` for repeatable installs; exact resolutions are committed in `uv.lock` and `package-lock.json`.

## Layout

| Directory | Purpose |
| --- | --- |
| `server/` | FastAPI app, models, SQLite, coordinator, MCP server, provider adapters |
| `ui/` | React/TypeScript local dashboard |
| `agents/` | Scripted coordinator clients and smoke checks |
| `contracts/` | Generated schemas and sample state |
| `scripts/` | Setup, contracts, seed/reset, smoke check |
| `docs/` | Architecture, setup, ANS, Databricks schema |
| `.local/` | Ignored: SQLite state and live-run output |

Pydantic models in `server/app/models.py` are the source of truth. Run `npm run contracts` after model or endpoint changes and commit the generated files; do not edit `ui/src/api.generated.ts` by hand. The [orchestration v1 contract](contracts/orchestration-api-v1.json) is frozen and the test suite rejects changes to its `/api` operations; additive work goes in a versioned endpoint.

## Roadmap

Synapse is being built for one developer running several agents on their own repositories. In rough order:

- Coordinate two real agents on a real repo end to end, with no scripted scenario.
- Make the MCP server and a CLI the primary interface; the web UI becomes a local viewer.
- Replace GoDaddy ANS with a local per-agent keypair for signing ChangeSets (`IDENTITY_MODE=local`).
- Remove hosting-only code: deploy configs, operator tokens, CORS settings, remote reset.
- Keep Databricks tracing optional; SQLite traces are the default.

## Origin

Started at VT Hacks 14 (September 2026) with Amanjeet Sahagal, Hoai Vo, and Aiden Mathews. The hackathon submission is preserved at [Jeewant05/VT-Hacks-14-Project](https://github.com/Jeewant05/VT-Hacks-14-Project). Everything from this repository's first commit onward is a separate, single-developer continuation.

## License

MIT. See [LICENSE](LICENSE).
