# Synapse

Pre-merge coordination for AI coding agents. Agents declare which files and
contracts they intend to touch before writing code; the coordinator detects
overlap, assigns exclusive ownership, and accepts only conflict-free ChangeSets.
Single-developer project, run locally only. Started at VT Hacks 14 (Sep 2026);
see README.md for the roadmap.

## What is in the repo

- `server/app/` - FastAPI API (`main.py`), coordinator (`service.py`), live
  three-agent runs (`live_agents.py`), LLM providers (`providers.py`), ANS
  identity verification (`ans/`, being replaced by local keypairs),
  Databricks/cache tracing (`tracing.py`), and the web surface that serves the
  built dashboard (`web.py`).
- `server/mcp_server.py` - MCP server over **stdio** exposing the coordinator's
  six operations as tools. It has no port; it calls the HTTP API at
  `SYNAPSE_BASE_URL` (see `.mcp.json`).
- `ui/` - React + TypeScript dashboard (Vite). `ui/src/api.generated.ts` is
  generated from `contracts/openapi.json`; never edit it by hand.
- `agents/` - scripted coordinator clients and smoke checks.
- `scripts/` - setup, contract export, seed/reset, ANS registration, rehearsal.
- `contracts/` - generated schemas. `orchestration-api-v1.json` is frozen.
- `docs/` - `ARCHITECTURE.md`, `SETUP.md`, `DEMO.md`, `ANS.md`.

## Run locally

    cp .env.example .env      # add provider keys; see README
    npm run setup             # uv sync, npm ci, export contracts, seed SQLite
    npm run dev               # API on 127.0.0.1:8000, UI on 127.0.0.1:5173
    npm run check             # ruff, pytest, tsc, vite build - must pass before a commit

Runtimes: Python 3.13 (`.python-version`), Node 22 (`.nvmrc`), uv, npm.
Use `uv sync --locked` and `npm ci`; commit `uv.lock` and `package-lock.json`.

## Hard rules

- Never commit `.env`, `.local/`, `*.pem`, `*.key`, tokens, or `.codex/`.
- Pydantic models in `server/app/models.py` are the source of truth. After any
  model or endpoint change run `npm run contracts` and commit the generated files.
- Do not change `/api` operations or response schemas covered by
  `contracts/orchestration-api-v1.json`; add a versioned endpoint instead.
- The API binds to loopback only. There is no hosted deployment and no
  operator token; do not add auth or deploy config without being asked.
- `npm run check` must be green before a commit to `main`.

## Known gotchas

- Live runs `git init` and commit their generated workspace, so `git` must be
  on PATH; without it every run stalls in `planning` with no error.
- Free LLM tiers (Groq, Cerebras) failed this workload on JSON discipline or
  rate limits. Virginia Tech ARC and Gemini are the tested paths. Start one
  live run at a time.
- In ANS mode the browser cannot drive agents (it holds no identity keys); use
  the server-side demo runner. See `docs/ANS.md`.
- Databricks `CAST(timestamp AS STRING)` returns no timezone; `tracing.py`
  normalizes it to UTC ISO so the dashboard shows local time.
