# Deployment reference

Two Fly.io apps run the same Docker image. One is the ANS-verified coordinator on
the project domain; the other runs the live coding agents. Both serve API and built
dashboard from one origin: `/api`, static UI at `/`, agent cards at `/.well-known/`.

| App | URL | Config | Identity | Live agents | Traces | Fly account |
| --- | --- | --- | --- | --- | --- | --- |
| `synapse-vt` | https://synapse-vt.us | `fly.toml` | ANS badge tier, DPoP | **not configured** (see Known issues) | cache | original team org |
| `synapse-vt-live` | https://synapse-vt-live.fly.dev | `fly.live.toml` | mock | Gemini `gemini-3.6-flash` | Databricks | Jeewant's personal org |

Each app: one `shared-cpu-1x` machine in `iad`, a 1 GB volume `synapse_data`
mounted at `/data` for SQLite and live-run artifacts. Neither config file holds a
secret; secrets are set with `fly secrets set`.

## Deploy

    fly deploy --remote-only                                   # synapse-vt
    fly deploy -c fly.live.toml -a synapse-vt-live --remote-only

`--remote-only` builds on Fly's builder, so no local Docker is needed. Record the
current image before deploying (`fly releases -a <app> --image | head -3`); roll
back with `fly deploy -a <app> --image <ref>`. There is no health check in either
config, so an image that crashes on boot takes the site down until rolled back.

## Configuration

Plain config lives in the `[env]` block of each Fly file and is versioned with the
repo. The shared values:

| Variable | Why |
| --- | --- |
| `ANS_PUBLIC_BASE_URL` | DPoP `htu` is compared to this, never to the `Host` header |
| `CORS_ORIGINS` | the app's own origin |
| `DATABASE_PATH` | `/data/synapse.db`, on the volume so it survives a redeploy |
| `LOCAL_BASE_URL` | `http://127.0.0.1:8080`, where the demo runner posts while signing for the public origin |
| `*_PROVIDER` / `*_MODEL` | live-agent vendor and model per role |

`synapse-vt` additionally sets `IDENTITY_MODE=ans`, `ANS_DOMAIN`, `ANS_BASE_URL`
and `ANS_TRUSTED_TL_HOSTS`. `synapse-vt-live` sets `IDENTITY_MODE=mock` and
`TRACE_MODE=databricks`.

### Secrets

| Secret | App | What it is |
| --- | --- | --- |
| `DEMO_TOKEN` | both | Operator secret for `/api/reset`, the only endpoint that destroys state. The app refuses to start on a public URL without it |
| `ANS_API_KEY` / `ANS_API_SECRET` | synapse-vt | GoDaddy ANS credentials, composed to `key:secret` on use |
| `ANS_AGENT_IDENTITIES` | synapse-vt | JSON of base64 PEM cert+key per agent, for the server-side demo runner |
| `ACME_CHALLENGES` | synapse-vt | HTTP-01 responses; only while registering an agent |
| `GEMINI_API_KEY` (or `GEMINI_API_KEY_BACKEND/_FRONTEND/_QA`) | both | Live-agent key. Per-role keys avoid sharing one rate limit |
| `DATABRICKS_HOST`, `_TOKEN`, `_WAREHOUSE_ID`, `_CATALOG`, `_SCHEMA` | synapse-vt-live | Trace sink. Incomplete settings silently fall back to the cache store |

## What is open, and the cost

Only `/api/reset` needs the token. Live runs and the demo runner are open so the
dashboard works without a prompt; the cost is provider quota, not data. Anyone
with the URL can start a run on the configured key. Rotate the key if the URL
travels further than intended.

Free tiers: start **one live run at a time**. Backend and frontend agents call
the model concurrently and a second run in the same minute trips per-minute
token caps.

## Known issues

- **`synapse-vt.us` runs a pre-Sep-20 image.** It predates the `git` install in
  the Dockerfile and has no provider key, so `/api/live/config` reports
  `configured: false` and live runs cannot work there. Fix: set a
  `GEMINI_API_KEY` secret and `fly deploy --remote-only` from `main`. Requires
  access to the original Fly org.
- Live runs `git init` their workspace. An image without `git` makes every run
  stall in `planning` with no error, because the subprocess fails before the
  run's `try` block.
- Providers tested on this workload: Gemini and Virginia Tech ARC (ARC only from
  campus; it did not respond from Fly). Cerebras' free tier returned 402 on every
  model; Groq's free tier hit token caps and returned malformed JSON. GitHub
  Models and OpenAI are supported but untested.
- The app refuses to start when `ANS_PUBLIC_BASE_URL` is public and `DEMO_TOKEN`
  is unset (`require_demo_token_for_public()`); the failure is at startup.

## Registering another ANS agent or version

`force_https` must be **false** during ANS domain validation: HTTP-01 fetches over
plain HTTP and the RA does not follow the 301. Set it false, deploy, validate,
set it back, deploy. DNS lives at Porkbun, not GoDaddy. See docs/ANS.md.

## Unused here

`HUGGINGFACE_*`, `CEREBRAS_*`, `GROQ_*`, `GITHUB_*`, `OPENROUTER_*`, `OPENAI_*`
and `ARC_*` are supported vendors but not selected in either config.
`PORKBUN_*` is local tooling and must not be deployed.
