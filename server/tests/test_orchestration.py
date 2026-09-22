"""The plan-before-write workflow for all three repository demo agents."""

from fastapi.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app


def _client(tmp_path):
    return TestClient(create_app(Settings(demo_token=None, database_path=tmp_path / "orchestration.db", trace_mode="cache")))


def _changeset(workstream_id: str, agent_id: str, files: list[str], symbols: list[str], *, version: int = 1,
               provided=None, consumed=None, database_changes=None):
    return {
        "workstream_id": workstream_id,
        "agent_id": agent_id,
        "intention_version": version,
        "branch": f"synapse/{agent_id}",
        "changed_files": files,
        "changed_symbols": symbols,
        "contracts_provided": provided or [],
        "contracts_consumed": consumed or [],
        "database_changes": database_changes or [],
        "tests": {"passed": 3, "failed": 0},
    }


def test_three_demo_agents_are_gated_then_converge(tmp_path):
    client = _client(tmp_path)
    state = client.post("/api/objectives").json()
    assert [agent["id"] for agent in state["agents"]] == ["demo-agent-1", "demo-agent-2", "demo-agent-3"]

    for agent_id in ("demo-agent-1", "demo-agent-2", "demo-agent-3"):
        state = client.post(f"/api/agents/{agent_id}/plan").json()

    contract = next(conflict for conflict in state["conflicts"] if conflict["type"] == "CONTRACT_CONFLICT")
    assert {workstream["state"] for workstream in state["workstreams"]} >= {"BLOCKED", "APPROVED"}
    assert client.post("/api/agents/demo-agent-2/execute").status_code == 409

    proposal = client.post(f"/api/conflicts/{contract['conflict_id']}/resolve").json()
    assert proposal["recommended_action"] == "UPDATE_CONTRACT"
    state = client.post(f"/api/conflicts/{contract['conflict_id']}/approve", json={"approved_by": "demo-human"}).json()
    agent2 = next(workstream for workstream in state["workstreams"] if workstream["agent_id"] == "demo-agent-2")
    assert agent2["state"] == "APPROVED"
    assert agent2["intention"]["version"] == 2

    assert client.post("/api/agents/demo-agent-3/execute").status_code == 200
    agent3 = _changeset(
        "workstream-agent-3", "demo-agent-3", ["src/models/user.ts"], ["User"],
        database_changes=["Add oauthProviderId and organizationId to user model."],
    )
    state = client.post("/api/changesets/submit", json=agent3).json()
    agent1 = next(workstream for workstream in state["workstreams"] if workstream["agent_id"] == "demo-agent-1")
    assert agent1["state"] == "APPROVED"

    assert client.post("/api/agents/demo-agent-2/execute").status_code == 200
    agent2_changes = _changeset(
        "workstream-agent-2", "demo-agent-2", ["src/components/login/OrganizationLogin.tsx"], ["OrganizationLogin"],
        version=2, consumed=[{"name": "POST /api/oauth", "response_fields": ["token", "user"]}],
    )
    assert client.post("/api/changesets/submit", json=agent2_changes).status_code == 200

    assert client.post("/api/agents/demo-agent-1/execute").status_code == 200
    agent1_changes = _changeset(
        "workstream-agent-1", "demo-agent-1", ["src/api/auth/oauth.ts", "src/api/auth/types.ts"], ["handleOAuth", "OAuthResponse"],
        provided=[{"name": "POST /api/oauth", "request_fields": ["authorizationCode"], "response_fields": ["token", "user"]}],
    )
    assert client.post("/api/changesets/submit", json=agent1_changes).status_code == 200
    final = client.post("/api/objectives/objective-oauth/converge")
    assert final.status_code == 200
    assert final.json()["objective"]["status"] == "complete"


def test_changeset_drift_is_rejected_before_convergence(tmp_path):
    client = _client(tmp_path)
    client.post("/api/objectives")
    client.post("/api/agents/demo-agent-3/plan")
    assert client.post("/api/agents/demo-agent-3/execute").status_code == 200
    drift = _changeset("workstream-agent-3", "demo-agent-3", ["src/auth/session.ts"], ["User"])
    state = client.post("/api/changesets/submit", json=drift).json()
    workstream = next(item for item in state["workstreams"] if item["id"] == "workstream-agent-3")
    assert workstream["state"] == "FAILED"
    assert any(conflict["type"] == "INTENT_DRIFT" for conflict in state["conflicts"])


def test_orchestrator_agent_records_a_resolution_proposal(tmp_path):
    client = _client(tmp_path)
    client.post("/api/objectives")
    for agent_id in ("demo-agent-1", "demo-agent-2", "demo-agent-3"):
        client.post(f"/api/agents/{agent_id}/plan")

    state = client.post("/api/objectives/objective-oauth/orchestrate")
    assert state.status_code == 200
    body = state.json()
    proposal = next(conflict for conflict in body["conflicts"] if conflict["status"] == "RESOLUTION_PROPOSED")
    assert proposal["resolution"]["reasoning"]
    assert any(event["event_type"] == "orchestrator_proposal" for event in body["events"])
