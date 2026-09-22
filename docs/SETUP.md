# Agreed scope and team handoff

## Product decisions

- Stack: Python 3.13, FastAPI, SQLite, official Python MCP SDK over HTTP, React/TypeScript/Vite.
- One objective, two workstreams, one page. Backend provides `{ token, user }`; frontend initially consumes `{ accessToken, profile }`.
- Approved correction changes the frontend contract. The final product must block completion until it redeclares and submits a compatible ChangeSet.
- Real ANS verification and real Databricks SQL reads/writes are P0. Local fixtures never satisfy sponsor acceptance.
- Three architectural decisions are sufficient. AI Search, payload signing, a revoked-agent scene, charts, real diffs, and a second live agent are stretch work.
- Manifests can demonstrate pre-merge coordination. No automatic merge or production authentication.
- The PRD and pasted notes were planning inputs; their conflicting stack/scenario details were resolved by these choices. Their provider API and event-deadline claims still need verification.

## Module boundaries

`server/app/models.py` owns shared wire models. JSON uses snake_case. Contracts identify method, path, provides/consumes role, and maps of field names to types. Exported files in `contracts/` include models, OpenAPI, and the initial state fixture.

`IdentityAdapter.verify(agent) -> VerificationResult` is asynchronous. Results include source, evidence, and check time. The mock allowlist recognizes only `backend-agent` and `frontend-agent`; it does not update seeded identity badges or establish real identity.

`MemoryAdapter.search(query) -> list[Decision]` and `MemoryAdapter.log(event) -> WriteReceipt` are asynchronous. The local adapter keeps events by event ID in memory and returns `pending`, never claiming Databricks delivery. Durable queued delivery belongs to the next milestone.

The SQLite workspace snapshot is scaffolding. P1 owns extending persistence for coordinator transitions and the event outbox. The foundation deliberately has no join, claim, submit, correction, or remote reset endpoints.

## Ownership and sequence

| Owner | Branch suggestion | Next deliverable |
| --- | --- | --- |
| P1 | `feat/coordinator` | State transitions, HTTP API, collision rules, persistence, shared HTTP MCP |
| P2 | `feat/dashboard-demo` | Functional dashboard, deterministic agent clients, demo app, review |
| P3 | `feat/databricks` | Three real decisions, SQL lookup, event writes with visible delivery state |
| P4 | `feat/ans` | Documented ANS call, identity gate, evidence, integration QA |

Start feature branches after the foundation commit. Coordinate model changes through P1 and regenerate contracts before merging. P3/P4 should implement separate modules behind the existing protocols to reduce shared-file conflicts.

Setup acceptance: a fresh clone starts in mock/cache mode; health works; fixtures persist; the UI reports connectivity; generated types compile; lockfiles and setup commands are committed. Subsequent milestones follow core flow, sponsor integrations, agent connection, then rehearsal and freeze.
