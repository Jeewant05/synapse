#!/usr/bin/env bash
# Three-agent demo scene against a running API (npm run dev:server). Fail-safe level 4.
# Usage: bash scripts/scenario.sh [base_url]   (base_url must include /api)
set -euo pipefail
B="${1:-http://127.0.0.1:8000/api}"
J='Content-Type: application/json'
step() { echo; echo "== $1"; }
post() { curl -sS -X POST "$B$1" -H "$J" ${2:+-d "$2"} | python3 -c "
import sys,json; s=json.load(sys.stdin)
if 'detail' in s: print('  ->', s['detail']); sys.exit(0)
print('  workstreams:', {w['id']: w['status'] for w in s['workstreams']})
print('  open conflicts:', [(c['type'], c['conflicting_field'], c['decision_id']) for c in s['conflicts'] if c['status']=='open'])
print('  last event:', s['events'][-1]['event_type'] if s['events'] else None)"; }

A='{"token":"string","user":"object"}'
C() { echo "{\"method\":\"POST\",\"path\":\"/api/oauth\",\"role\":\"$1\",\"response_fields\":$A}"; }
CS() { echo "{\"id\":\"cs-$1\",\"workstream_id\":\"$1\",\"agent_id\":\"$1-agent\",\"files\":$3,\"contract\":$(C "$2"),\"tests\":[{\"name\":\"$1 test\",\"status\":\"passed\"}]}"; }

step "reset";                 post /reset
step "backend joins";         post /agents/backend-agent/join
step "frontend joins";        post /agents/frontend-agent/join
step "telemetry joins";       post /agents/telemetry-agent/join
step "rogue agent refused";   post /agents/rogue-agent/join

step "all three claim"
post /workstreams/backend/claim   '{"agent_id":"backend-agent"}'
post /workstreams/frontend/claim  '{"agent_id":"frontend-agent"}'
post /workstreams/telemetry/claim '{"agent_id":"telemetry-agent"}'

step "all three declare intent -> 3 FILE COLLISIONS on src/auth/session.ts, decision attached"
post /workstreams/backend/declare   "{\"agent_id\":\"backend-agent\",\"contract\":$(C provides)}"
post /workstreams/frontend/declare  "{\"agent_id\":\"frontend-agent\",\"contract\":$(C consumes)}"
post /workstreams/telemetry/declare "{\"agent_id\":\"telemetry-agent\",\"contract\":$(C consumes)}"

step "frontend tries to submit while blocked -> 409"
post /workstreams/frontend/submit "$(CS frontend consumes '["src/components/login/x.tsx"]')"

step "coordinator assigns one owner per file -> collisions RESOLVE"
post /workstreams/backend/scope   '{"agent_id":"backend-agent","owned_paths":["src/api/auth/oauth.ts","src/auth/session.ts"]}'
post /workstreams/frontend/scope  '{"agent_id":"frontend-agent","owned_paths":["src/components/login/OrganizationLogin.tsx","src/components/login/x.tsx"]}'
post /workstreams/telemetry/scope '{"agent_id":"telemetry-agent","owned_paths":["src/lib/analytics/authEvents.ts"]}'

step "frontend tries to touch session.ts anyway -> 409 outside owned scope"
post /workstreams/frontend/submit "$(CS frontend consumes '["src/auth/session.ts"]')"

step "all three submit non-overlapping ChangeSets -> objective complete"
post /workstreams/backend/submit   "$(CS backend provides '["src/api/auth/oauth.ts","src/auth/session.ts"]')"
post /workstreams/frontend/submit  "$(CS frontend consumes '["src/components/login/x.tsx"]')"
post /workstreams/telemetry/submit "$(CS telemetry consumes '["src/lib/analytics/authEvents.ts"]')"

echo; echo "== done. GET $B/state for the review page."
