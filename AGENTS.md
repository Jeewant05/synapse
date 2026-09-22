# Synapse (VT Hacks 14)

Pre-merge coordination for coding agents. Agents publish which files they intend
to touch before writing code; the coordinator detects overlap, assigns exclusive
ownership, and accepts only conflict-free ChangeSets. Built for VT Hacks 14
(Sep 2026) against the GoDaddy ANS and Databricks sponsor tracks. Hackathon
prototype in maintenance mode: cleanup, docs, and small fixes only unless told
otherwise.

## What is in the repo

- `server/app/` - FastAPI API (`main.py`), coordinator (`service.py`), live
  three-agent runs (`live_agents.py`), LLM providers (`providers.py`), ANS
  identity verification (`ans/`), Databricks/cache tracing (`tracing.py`), and
  the production web surface that serves the built dashboard (`web.py`).
- `server/mcp_server.py` - MCP server over **stdio** exposing the coordinator's
  six operations as tools. It has no port; it calls the HTTP API at
  `SYNAPSE_BASE_URL` (see `.mcp.json`).
- `ui/` - React + TypeScript dashboard (Vite). `ui/src/api.generated.ts` is
  generated from `contracts/openapi.json`; never edit it by hand.
- `agents/` - scripted coordinator clients and smoke checks.
- `scripts/` - setup, contract export, seed/reset, ANS registration, rehearsal.
- `contracts/` - generated schemas. `orchestration-api-v1.json` is frozen.
- `docs/` - `ARCHITECTURE.md`, `SETUP.md`, `DEMO.md`, `DEPLOY.md`, `ANS.md`.
- `Dockerfile` - one image: Node builds the UI, Python 3.13 + uv serves API and
  UI on port 8080. `git` is installed because live runs `git init` and commit
  their generated workspace.
- `fly.toml` - Fly app `synapse-vt` (synapse-vt.us): ANS identity mode.
- `fly.live.toml` - Fly app `synapse-vt-live` (synapse-vt-live.fly.dev): mock
  identity, live agents on Gemini, Databricks tracing. Both files hold only
  non-secret config; secrets are Fly secrets.

## Run locally

    cp .env.example .env      # add provider keys; see README
    npm run setup             # uv sync, npm ci, export contracts, seed SQLite
    npm run dev               # API on 127.0.0.1:8000, UI on 127.0.0.1:5173
    npm run check             # ruff, pytest, tsc, vite build - must pass before a PR

Runtimes: Python 3.13 (`.python-version`), Node 22 (`.nvmrc`), uv, npm.
Use `uv sync --locked` and `npm ci`; commit `uv.lock` and `package-lock.json`.

## Hard rules

- Never commit `.env`, `.local/`, `*.pem`, `*.key`, tokens, or `.codex/`.
- Pydantic models in `server/app/models.py` are the source of truth. After any
  model or endpoint change run `npm run contracts` and commit the generated files.
- Do not change `/api` operations or response schemas covered by
  `contracts/orchestration-api-v1.json`; add a versioned endpoint instead.
- Do not change the Dockerfile base image or either Fly config without asking.
- All changes go through a PR to `main`. Never push to `main` directly.
- `npm run check` must be green before requesting review.

## Deployments

| App | URL | Identity | Live agents | Traces |
| --- | --- | --- | --- | --- |
| `synapse-vt` | https://synapse-vt.us | ANS (badge tier, DPoP) | not configured | cache |
| `synapse-vt-live` | https://synapse-vt-live.fly.dev | mock | Gemini | Databricks |

Deploy with `fly deploy --remote-only` (uses `fly.toml`) or
`fly deploy -c fly.live.toml -a synapse-vt-live --remote-only`. Details, secrets
and known issues: `docs/DEPLOY.md`.

## Known gotchas

- The runtime image must have `git`; without it every live run stalls in
  `planning` with no error (the `git init` subprocess fails outside the run's
  try block).
- Free LLM tiers (Groq, Cerebras) failed this workload on JSON discipline or
  rate limits. Gemini and Virginia Tech ARC are the tested paths. Start one
  live run at a time.
- In ANS mode the browser cannot drive agents (it holds no identity keys); use
  the server-side demo runner. See `docs/ANS.md`.
- Databricks `CAST(timestamp AS STRING)` returns no timezone; `tracing.py`
  normalizes it to UTC ISO so the dashboard shows local time.
