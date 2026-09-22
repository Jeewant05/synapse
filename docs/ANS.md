# Agent Name Service

Synapse's premise is that only a verified agent may claim file scope and land a
ChangeSet. Before ANS, that rested on a hardcoded allowlist in
`server/app/adapters.py`: `agent_id` arrived as a bare string in a JSON body, so
anyone who could reach port 8000 could act as `backend-agent`. ANS replaces the
allowlist with a domain-anchored identity the coordinator can actually check.

## What the coordinator verifies

ANS-6 requires three proofs on every privileged call. Any two without the third
is exploitable: without possession you accept replayed public artifacts, without
liveness you accept revoked agents.

| Proof | Question | How |
| --- | --- | --- |
| Identity | Is this certificate sealed in the transparency log for this agent? | `_ans-badge.<host>` TXT → badge → `identityCerts[]` fingerprint match |
| Liveness | Is that registration valid *right now*? | Badge `status` must be `ACTIVE`, `WARNING` or `DEPRECATED` |
| Possession | Does the caller hold the private key *for this request*? | ANS-6 Method B `DPoP` proof, verified per §7.4 |

Plus one layer the spec asks for and we keep independent: the identity
certificate must chain to the RA's issuing CA (`server/app/ans/trust.py`). The
badge alone would block a forged certificate, but the two checks fail
differently, so an attacker has to defeat both.

The proven ANSName comes from the certificate's URI SAN, never from the request
body. `Coordinator._bind` refuses the call when it does not match the claimed
`agent_id` — that is what makes `agent_id` unforgeable.

### Tier, stated honestly

This is **badge tier**, not SCITT tier: liveness is a live log query rather than
a locally verified COSE receipt. ANS-6 permits it, and revocation shows up
immediately instead of after a status-token TTL, at the cost of a network call on
the verification path.

**No DANE/TLSA.** TLSA only adds assurance under DNSSEC and the demo zone is
unsigned, so server certificates are validated by PKIX alone — Bronze on the
ANS v2 tier scale. Do not describe this as DANE-validated.

**The live Gemini path is not gated.** `server/app/live_agents.py` uses its own
role namespace (`backend`/`frontend`/`qa`), never calls `join`, and never appears
in `state.agents`. Bringing it behind the gate is follow-on work.

## Layout

| Where | What |
| --- | --- |
| `server/app/ans/names.py` | ANSName parse/build: `ans://v<major.minor.patch>.<host>` |
| `server/app/ans/resolver.py` | `_ans` / `_ans-badge` TXT discovery, version-selected |
| `server/app/ans/badge.py` | Badge fetch + the four ANS-6 §6.2 equalities |
| `server/app/ans/dpop.py` | Method B verification, §7.4 order |
| `server/app/ans/trust.py` | Identity-certificate trust anchor |
| `server/app/ans/identity.py` | `AnsIdentity`, behind the existing `IdentityAdapter` protocol |
| `server/app/ans/signer.py` | Agent-side proof construction |
| `scripts/ans_register.py` | Registration runbook driver |
| `scripts/ans_check.py` | Live resolve + badge diagnostic |

## Registration runbook

Registration happens through `ans-cli`, not through application code. The script
is a thin wrapper that records each outcome in `.local/ans/<agent>/agent.json` so
the seed fixture cannot drift from the registry.

### Prerequisites

```sh
brew install agentnameservice/ans/ans-cli
```

`.env` needs `ANS_API_KEY`, `ANS_API_SECRET` and `ANS_DOMAIN`. The CLI wants the
combined `key:secret` form and defaults to the OTE environment, so
`scripts/ans_register.py` composes the credential and forces
`ANS_BASE_URL=https://api.godaddy.com` for you. Nothing here is committed —
`.env`, `.local/`, `*.pem` and `*.key` are all gitignored.

### Names

One ANSName per host+version, so each agent needs its own host. The coordinator
sits on the apex.

| Agent | Host | ANSName |
| --- | --- | --- |
| `backend-agent` | `backend.<domain>` | `ans://v1.0.0.backend.<domain>` |
| `frontend-agent` | `frontend.<domain>` | `ans://v1.0.0.frontend.<domain>` |
| `telemetry-agent` | `telemetry.<domain>` | `ans://v1.0.0.telemetry.<domain>` |
| coordinator | `<domain>` | `ans://v1.0.0.<domain>` |

### Steps

```sh
uv run python -m scripts.ans_register generate   # keys + CSRs into .local/ans/
uv run python -m scripts.ans_register register   # -> agentId + ACME challenge
uv run python -m scripts.ans_register records    # what to publish
# publish _acme-challenge.<host> TXT in the DNS zone, then:
uv run python -m scripts.ans_register acme
# publish _ans.<host> and _ans-badge.<host> TXT (both required), then:
uv run python -m scripts.ans_register dns
uv run python -m scripts.ans_register status     # expect ACTIVE
uv run python -m scripts.ans_register certs
```

Add `--agent backend-agent` to work one agent at a time.

Record shapes (ANS-3):

```
_ans.<host>        v=ans1; version=v1.0.0; p=mcp; mode=direct; url=https://<host>/mcp
_ans-badge.<host>  v=ans-badge1; version=v1.0.0; url=<transparency log badge URL>
```

### DNS lives somewhere else

The registry is GoDaddy's but the domain is registered at **Porkbun**, so the
GoDaddy API key cannot write these records. Publish them in the Porkbun zone, by
hand or through Porkbun's API. This is why the DNS steps are manual: the script
prints what to publish and waits rather than pretending it can write the zone.

A freshly registered domain has no TLD delegation for a while. ACME does a public
recursive lookup, so confirm the zone resolves publicly before running `acme`:

```sh
dig @1.1.1.1 +short NS <domain>     # must list the registrar's nameservers
```

`NXDOMAIN` here means the delegation has not propagated yet and ACME will fail.

### After certificates are issued

`certs` saves the RA response to `.local/ans/<agent>/identity-certs.json`. Write
the leaf PEM to `.local/ans/<agent>/identity.crt` — that path plus the existing
`identity.key` is what `AgentClient.with_ans_identity()` loads to sign proofs.

Point `ANS_IDENTITY_CA_BUNDLE` at the issuing chain to turn on the trust-anchor
layer. Without it the badge is the only anchor, and the server logs that it is.

## Running in ANS mode

```sh
IDENTITY_MODE=ans npm run dev
npm run agent-test
```

`/health` reports `identity_mode`, `identity_tier` and `dpop_required`.

### The dashboard cannot drive agents in ANS mode

`ui/src/useCoordinator.ts` drives the whole flow from the browser — it posts
`join`, `claim` and `declare` for all three agents. A browser cannot hold agent
identity private keys, and it must not, so in ANS mode those buttons get a 401
explaining to use the scripted agents instead. The dashboard stays fully useful
as an observer: `/state` and `/health` are unauthenticated reads.

Mock mode is unchanged. The guided demo works exactly as before.

## Testing

```sh
uv run pytest server/tests/test_ans.py             # unit: names, DNS, DPoP, badge
uv run pytest server/tests/test_ans_adversarial.py # attack battery
uv run pytest server/tests/test_ans_gate.py        # the gate over real HTTP
```

All of it is offline — a self-signed EC P-256 certificate stands in for the
issued identity certificate, and DNS and badge responses are supplied directly.
No credentials, no network.

`scripts/ans_check.py <ansname>` is the live counterpart: it resolves the TXT
records and fetches the badge, printing the decision and its evidence. Cross-check
against `ans-cli resolve <host> --version "^1.0.0"` and `ans-cli badge <agentId>`.

### The attack battery

`test_ans_adversarial.py` mirrors the attack classes an external ANS fraud-test
agent runs against suppliers. Payment-specific rows (mandate amount, quote
binding, on-chain settlement replay) have no analogue here — Synapse authorizes
file scope, not money — so they appear as their structural equivalents: binding a
proof to one caller, one endpoint and one body.

| Attack | Defence |
| --- | --- |
| Replay a spent proof | `ReplayCache`, recorded only after every other check passes |
| Proof from a key other than the certificate's | `jwk` compared to the cert key *before* signature verification |
| Corrupt JWS | Typed rejection, never an unhandled exception (a 500 is not a denial) |
| Superseded / stripped format | Header must carry exactly `typ`/`alg`/`jwk`/`x5c` |
| Unknown key, self-signed cert | Badge fingerprint match, plus the CA trust anchor |
| Wrong audience | `htu` compared against configured authority, never the `Host` header |
| Wrong scope | `htu` binds the exact path |
| Canonicalization drift | Non-integer `iat` refused; duplicate JSON members refused |
| Tampered body | `ans_content_digest` over the bytes actually received |

Two findings came out of building that battery, both now fixed:

- **Duplicate JSON members were accepted.** `json.loads` keeps the last
  occurrence, so a proof with two `htu` members passed here while another
  implementation might read the first — one value signed, a different one
  enforced. Now rejected outright (`_reject_duplicate_keys`).
- **No certificate trust anchor.** Only the badge stood between a self-signed
  certificate bearing someone else's ANSName and acceptance. Chain validation is
  now an independent layer.

## Revocation demo

The sharpest thing to show: revoke an agent mid-run and watch its ChangeSet get
refused. `Coordinator.submit` re-checks liveness rather than trusting join time,
because landing a ChangeSet is the irreversible step.

```sh
ans-cli revoke <telemetryAgentId> --reason CESSATION_OF_OPERATION
```

Badges are cached for `ANS_BADGE_TTL_SECONDS` (default 60), so allow a minute.

## Deployment

The API lives under `/api` in every environment and FastAPI serves the built
dashboard at `/` (`server/app/web.py`). The dev proxy no longer rewrites the
prefix, so a DPoP proof signed in development binds the same path it will bind in
production — `htu` is exact, and a mismatch is a rejection.

`Dockerfile` and `fly.toml` deploy one container answering on the apex and the
three agent subdomains. `/.well-known/agent-card.json` is routed by `Host`, so
each registered agent serves its own card rather than the coordinator's.

### The dashboard in ANS mode

A browser must never hold an agent identity key, so `POST /api/demo/run`
(`server/app/demo_runner.py`) runs the three scripted agents server-side. The
proofs are real: each agent signs against `ANS_PUBLIC_BASE_URL` while the request
travels over loopback, which verifies because an ANS-6 verifier compares `htu`
against configured authority and never reads the `Host` header.

The runner is open — the dashboard presses it with no prompt. It never resets, so
replaying it cannot lose state. Only Reset asks for `DEMO_TOKEN`, and only when
`/api/health` reports `reset_requires_token`; the UI keeps the token in
`sessionStorage` after the server accepts it, and forgets it if the server does not.

### Fail closed by construction

`require_demo_token_for_public()` refuses to build the app when
`ANS_PUBLIC_BASE_URL` is a public host and `DEMO_TOKEN` is unset — `/api/reset`
would otherwise let anyone who finds the URL wipe the workspace. The failure is at
startup, before the first visitor. Local
development is unaffected: with no token configured and a loopback URL, the guard
allows through.

### Registering against a platform-managed certificate

`scripts/ans_register.py register` submits the identity CSR only. Fly issues and
rotates the TLS certificate actually served, so sealing a `serverCerts[]`
fingerprint we never present would make every callee verification fail — worse
than publishing none. Pass `--with-server-cert` only where we terminate TLS
ourselves and can serve exactly the registered certificate.

### DNS records at Porkbun

The app is `synapse-vt` on Fly (`synapse-vt.fly.dev`). Four hostnames point at it.
Fly issues the certificates once these resolve.

| Type | Host (Porkbun "Host" field) | Answer |
| --- | --- | --- |
| A | *(blank — the apex)* | `66.241.124.238` |
| AAAA | *(blank)* | `2a09:8280:1::194:74e1:0` |
| A | `backend` | `66.241.124.238` |
| AAAA | `backend` | `2a09:8280:1::194:74e1:0` |
| A | `frontend` | `66.241.124.238` |
| AAAA | `frontend` | `2a09:8280:1::194:74e1:0` |
| A | `telemetry` | `66.241.124.238` |
| AAAA | `telemetry` | `2a09:8280:1::194:74e1:0` |

**Delete Porkbun's parking records first.** A fresh domain ships with apex `A`
records pointing at `207.207.210.x` and a wildcard `CNAME` to
`pixie.porkbun.com`. The apex records must go or they will answer instead of
Fly's. The wildcard can stay — explicit subdomain records win on specificity —
but removing it avoids surprises for hosts we have not defined.

The IPv4 is Fly-shared, which is fine: routing is by SNI and the app forces
HTTPS.

Then:

```sh
fly certs check synapse-vt.us        # repeat per hostname until Ready
fly certs list --app synapse-vt
```

The ANS `_acme-challenge`, `_ans` and `_ans-badge` TXT records land in this same
zone later, during registration.
