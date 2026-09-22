# Demo runbook

## Live three-agent open-model demo

1. Copy `.env.example` to `.env`, add your ARC API key to the `ARC_API_KEY_BACKEND`, `ARC_API_KEY_FRONTEND`, and `ARC_API_KEY_QA` slots, then run `npm run setup` and `npm run dev`.
2. Open the live dashboard, enter a small full-stack objective, and start the agents.
3. Watch all three agents publish an intention. Explain that these plans are injected into every implementation prompt before any file is written.
4. Point out that backend and frontend then build concurrently using separate APIs, exclusive directory ownership, and one shared contract.
5. An application preview box appears inside the dashboard while the integration agent reviews the staged output; after validation, all files are committed to the run sandbox together.
6. The preview box automatically switches to the finished interactive application. Use **Expand** only when you want a larger separate view.

Be precise in the presentation: the displayed files and `frontend/preview.html` are genuinely returned by the configured provider (Virginia Tech ARC by default), written to an isolated local run directory, and committed to that run's Git repository. Synapse validates boundaries and serves the generated app in an opaque-origin browser sandbox. Inline JavaScript is allowed so games and workflows are interactive, while network access, parent-page access, external assets, navigation, and form submission remain blocked. Use the guided simulation when a token or network connection is unavailable.

## Foundation smoke check (available now)

1. Copy `.env.example` to `.env` and run `npm run setup`.
2. Start `npm run dev` and open http://127.0.0.1:5173.
3. Confirm coordinator connectivity, the OAuth objective, three pending workstreams, and the local-fixture notice.
4. In another terminal run `npm run rehearse` and `npm run check`.
5. Run `npm run reset` to restore the local fixtures; the dashboard polls once per second.

## Guided three-agent rehearsal

Reset the workspace → connect your coding agent and two teammate agents → publish planned file touches → show that all three selected `src/auth/session.ts` → inspect the three server-side file conflicts → apply the ownership plan → show exclusive scopes → submit three ChangeSets → open the combined review.

The key proof happens before implementation: the coordinator blocks the three plans while their intended files overlap. The approved plan leaves `src/auth/session.ts` with the API agent, moves UI work to `OrganizationLogin.tsx`, and moves telemetry work to `authEvents.ts`. The final review shows three manifests with one owner per file.

Target: under three minutes, five clean runs, no manual database edits. Report the guided clients honestly. In the default `IDENTITY_MODE=mock`, the identity adapter is a local fixture; do not present it as a live ANS operation. Under `IDENTITY_MODE=ans` the verification is real and may be presented as such, with the boundaries in [ANS.md](ANS.md) stated: badge tier rather than SCITT, no DANE, and the live Gemini path not gated. The memory adapter is a local fixture in every mode; never present it as a Databricks operation. Distinguish agent-reported tests from executed tests. Record a successful backup and tag the tested commit `demo-stable` only after the complete flow works.

The pasted deadline is provisional: confirm this year's submission time and video requirements. Reserve relocation and sleep time. Submit ahead of the deadline; do not make code changes after the last clean rehearsal.

## Live agents (Virginia Tech ARC)

Set in `.env`: `BACKEND_PROVIDER=arc`, `FRONTEND_PROVIDER=arc`, `QA_PROVIDER=arc`, and an ARC key in `ARC_API_KEY_BACKEND`, `ARC_API_KEY_FRONTEND`, and `ARC_API_KEY_QA`. The model defaults to `gpt-oss-120b`; role-specific `ARC_MODEL_*` overrides are optional. Restart `npm run dev:server`.

In the dashboard start the three coding agents. They publish intentions first, backend and frontend build in parallel, integration reviews the staged files, and the coordinator commits the validated run artifacts. The frontend agent also generates a standalone interactive application that loads inside the dashboard preview after validation. Each card shows its actual provider and model.

From a terminal: `curl -s -X POST localhost:8000/api/live/runs -H 'Content-Type: application/json' -d '{"objective":"Add organization OAuth login"}'` then `curl -s -N localhost:8000/api/live/runs/<run_id>/events`.

## Coordinator scene from a terminal

With `npm run dev:server` running: `bash scripts/scenario.sh`. Expect three file collisions on `src/auth/session.ts` with decision `scope-boundaries`, two intentional 409s, and `objective_completed` last.
