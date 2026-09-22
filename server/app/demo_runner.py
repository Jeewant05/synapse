"""Server-side demo runner for ANS mode.

The dashboard drives the whole coordination flow, but in ANS mode every
privileged call needs a proof signed by an agent's identity key. A browser must
never hold those keys, so the run happens here instead and the dashboard just
asks for it.

The proofs are real, not bypassed. Each scripted agent signs with
`ANS_PUBLIC_BASE_URL` -- the public origin -- while the request travels over
loopback. That works because an ANS-6 verifier compares `htu` against its
configured authority and never reads the `Host` header, so a proof signed for
the public URL verifies on a local hop. The gate is the same one an external
caller would face.
"""

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from agents.clients import APPROVED_CONTRACT, CONSUMER_CONTRACT, PASSED_TEST, AgentClient
from server.app.ans.material import load as load_material
from server.app.ans.signer import DpopSigner
from server.app.config import Settings

log = logging.getLogger("synapse.demo")

# Matches the seeded workstreams in scripts/database.py.
PLAN = [
    ("backend-agent", "backend", APPROVED_CONTRACT,
     ["src/api/auth/oauth.ts", "src/auth/session.ts"]),
    ("frontend-agent", "frontend", CONSUMER_CONTRACT,
     ["src/components/login/OrganizationLogin.tsx"]),
    ("telemetry-agent", "telemetry", CONSUMER_CONTRACT,
     ["src/lib/analytics/authEvents.ts"]),
]


class DemoRunResponse(BaseModel):
    status: str
    agents: list[str]
    detail: str | None = None


def _client_for(settings: Settings, local_base: str, agent_id: str) -> AgentClient:
    """One scripted agent's client, signed only when the coordinator checks proofs.

    Mock mode has no identity material and needs none, so building a signer there
    would fail the whole run on a fresh clone for a gate that is not armed.

    In ANS mode the proof is signed against the public origin but sent over
    loopback: an ANS-6 verifier compares `htu` to its configured authority and
    never reads the `Host` header, so the proof is the same one an external
    caller would present.
    """
    if settings.identity_mode != "ans":
        return AgentClient(base_url=local_base, agent_id=agent_id)
    certificate_pem, key_pem = load_material(agent_id, settings.ans_agent_identities)
    signer = DpopSigner.from_pem(certificate_pem, key_pem, settings.ans_public_base_url)
    return AgentClient(base_url=local_base, agent_id=agent_id, signer=signer)


def _run(settings: Settings, local_base: str) -> DemoRunResponse:
    """Drive the three scripted agents with real DPoP proofs.

    Phased, not agent-by-agent. The seeded workstreams all claim
    `src/auth/session.ts`, which is the collision the demo exists to show, and
    the coordinator refuses a ChangeSet while any conflict is open. So every
    agent must be re-scoped before the first submit -- the same order the
    dashboard used to drive by hand.
    """
    clients = {a: _client_for(settings, local_base, a) for a, *_ in PLAN}

    for agent_id, _ws, _contract, _files in PLAN:
        clients[agent_id].join()
    for agent_id, workstream, _contract, _files in PLAN:
        clients[agent_id].claim(workstream)
    for agent_id, workstream, contract, _files in PLAN:
        clients[agent_id].declare(workstream, contract)
    # Resolve every overlap before anything is submitted.
    for agent_id, workstream, _contract, files in PLAN:
        clients[agent_id].scope(workstream, files)
    for agent_id, workstream, contract, files in PLAN:
        clients[agent_id].submit(workstream, contract, files, PASSED_TEST)

    return DemoRunResponse(status="complete", agents=[a for a, *_ in PLAN])


def build_demo_router(settings: Settings, local_base: str) -> APIRouter:
    # This is the button the dashboard presses. It never resets, so replaying
    # it cannot lose state.
    router = APIRouter(prefix="/api/demo", tags=["demo"])

    @router.post("/run", response_model=DemoRunResponse)
    async def run() -> DemoRunResponse:
        try:
            # The scripted clients are blocking httpx; keep the event loop free.
            return await asyncio.to_thread(_run, settings, local_base)
        except Exception as exc:  # noqa: BLE001 - surface the failure to the dashboard
            log.warning("demo run failed: %s", exc)
            return DemoRunResponse(status="failed", agents=[], detail=str(exc)[:400])

    return router
