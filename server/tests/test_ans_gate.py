"""The identity gate end to end, over real HTTP, in ANS mode.

The unit tests prove the verifier rejects bad proofs. These prove the coordinator
is actually wired to it: that an unsigned call cannot mutate state, that a signed
call can, and that a genuinely registered agent still cannot act as a different
one.
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.database import demo_state
from server.app.adapters import CacheMemory
from server.app.ans.badge import Badge
from server.app.ans.dpop import DpopVerifier
from server.app.ans.identity import AnsIdentity
from server.app.ans.names import ANSName
from server.app.ans.signer import DpopSigner
from server.app.models import AgentPrincipal
from server.app.routes import build_router
from server.app.service import Coordinator
from server.app.store import read_state, write_state
from server.tests.test_ans import make_identity
from server.tests.test_ans_adversarial import StubBadges, StubDiscovery, fingerprint

BASE_URL = "http://testserver"
DOMAIN = "synapse-vt.us"

AGENTS = {
    "backend-agent": f"ans://v1.0.0.backend.{DOMAIN}",
    "frontend-agent": f"ans://v1.0.0.frontend.{DOMAIN}",
    "telemetry-agent": f"ans://v1.0.0.telemetry.{DOMAIN}",
}


class MultiBadges(StubBadges):
    """Serves the badge belonging to whichever agent host was resolved."""

    def __init__(self, badges: dict[str, Badge]):
        super().__init__(next(iter(badges.values())))
        self._by_host = {badge.host: badge for badge in badges.values()}

    async def fetch(self, url):
        self._check_url(url)
        return self._by_host[url.rsplit("/", 1)[-1]]


class NamedDiscovery(StubDiscovery):
    async def badge_url_for(self, host, version):
        return f"https://api.godaddy.com/v1/agents/{host}"


def build(tmp_path):
    """A coordinator in ANS mode, with three registered agents."""
    certs = {agent_id: make_identity(name) for agent_id, name in AGENTS.items()}
    badges = {
        name: Badge(
            ans_name=name,
            host=ANSName.parse(name).host,
            status="ACTIVE",
            identity_cert_fingerprints=frozenset({fingerprint(certs[agent_id][0])}),
            server_cert_fingerprints=frozenset(),
            url=f"https://api.godaddy.com/v1/agents/{name}",
            raw={},
        )
        for agent_id, name in AGENTS.items()
    }

    state = demo_state(DOMAIN)
    state.agents = [
        AgentPrincipal(id=agent_id, ans_name=name, role=agent_id.split("-")[0])
        for agent_id, name in AGENTS.items()
    ]
    path = tmp_path / "ans.db"
    write_state(path, state)

    identity = AnsIdentity(
        discovery=NamedDiscovery(),
        badges=MultiBadges(badges),
        dpop=DpopVerifier(public_base_url=BASE_URL),
    )
    coordinator = Coordinator(path, identity, CacheMemory(state.decisions))
    app = FastAPI()
    app.include_router(
        build_router(coordinator, lambda: demo_state(DOMAIN), ans_identity=identity)
    )
    signers = {
        agent_id: DpopSigner(certificate=cert, private_key=key, base_url=BASE_URL)
        for agent_id, (cert, key) in certs.items()
    }
    return TestClient(app), signers, path


def signed_post(client, signer, path, payload=None):
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    headers = {"DPoP": signer.proof("POST", path, body)}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.post(path, content=body or None, headers=headers)


def test_unsigned_call_cannot_join(tmp_path):
    client, _, _ = build(tmp_path)
    response = client.post("/api/agents/backend-agent/join")
    assert response.status_code == 401
    assert "DPoP" in response.json()["detail"]


def test_signed_call_joins_and_records_ans_evidence(tmp_path):
    client, signers, path = build(tmp_path)
    response = signed_post(client, signers["backend-agent"], "/api/agents/backend-agent/join")
    assert response.status_code == 200, response.text

    state = read_state(path)
    agent = next(a for a in state.agents if a.id == "backend-agent")
    assert agent.verified is True
    assert agent.ans_status == "ACTIVE"

    joined = next(e for e in state.events if e.event_type == "agent_joined")
    assert joined.payload["source"] == "ans"
    assert joined.payload["tier"] == "badge"
    assert joined.payload["ans_name"] == AGENTS["backend-agent"]


def test_registered_agent_cannot_act_as_another(tmp_path):
    """The impersonation row: a real ANS identity is still bound to one agent_id."""
    client, signers, _ = build(tmp_path)
    joined = signed_post(client, signers["frontend-agent"], "/api/agents/frontend-agent/join")
    assert joined.status_code == 200, joined.text

    # frontend-agent's key, claiming to be backend-agent.
    body = json.dumps({"agent_id": "backend-agent"}, separators=(",", ":")).encode()
    response = client.post(
        "/api/workstreams/backend/claim",
        content=body,
        headers={
            "DPoP": signers["frontend-agent"].proof("POST", "/api/workstreams/backend/claim", body),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 403
    assert "claimed backend-agent" in response.json()["detail"]


def test_replayed_request_is_refused_by_the_gate(tmp_path):
    client, signers, _ = build(tmp_path)
    proof = signers["backend-agent"].proof("POST", "/api/agents/backend-agent/join", b"")
    first = client.post("/api/agents/backend-agent/join", headers={"DPoP": proof})
    assert first.status_code == 200
    replay = client.post("/api/agents/backend-agent/join", headers={"DPoP": proof})
    assert replay.status_code == 401
    assert "already been used" in replay.json()["detail"]


def test_duplicate_dpop_headers_are_refused(tmp_path):
    client, signers, _ = build(tmp_path)
    proof = signers["backend-agent"].proof("POST", "/api/agents/backend-agent/join", b"")
    # httpx sends a repeated header when given a list of tuples.
    response = client.post(
        "/api/agents/backend-agent/join", headers=[("DPoP", proof), ("DPoP", proof)]
    )
    assert response.status_code == 401
    assert "multiple DPoP headers" in response.json()["detail"]


def test_unverified_agent_cannot_declare(tmp_path):
    """declare() previously skipped the verified check entirely."""
    client, signers, _ = build(tmp_path)
    payload = {
        "agent_id": "backend-agent",
        "contract": {
            "method": "POST",
            "path": "/api/oauth",
            "role": "provides",
            "response_fields": {"token": "string"},
        },
    }
    response = signed_post(
        client, signers["backend-agent"], "/api/workstreams/backend/declare", payload
    )
    assert response.status_code == 403
    assert "not a verified agent" in response.json()["detail"]
