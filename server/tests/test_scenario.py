"""The full demo scene, end to end, in mock/cache mode. If this passes, the curl demo works."""

from fastapi.testclient import TestClient

from scripts.database import demo_state
from server.app.config import Settings
from server.app.main import create_app
from server.app.store import write_state

APPROVED = {"token": "string", "user": "object"}


def _client(tmp_path):
    path = tmp_path / "scenario.db"
    write_state(path, demo_state())
    return TestClient(create_app(Settings(
        database_path=path, trace_mode="cache", backend_provider="none",
        frontend_provider="none", qa_provider="none",
    )))


def _contract(role, fields):
    return {"method": "POST", "path": "/api/oauth", "role": role, "response_fields": fields}


def _changeset(ws, agent, fields, files):
    return {
        "id": f"cs-{ws}",
        "workstream_id": ws,
        "agent_id": agent,
        "files": files,
        "contract": _contract("provides" if ws == "backend" else "consumes", fields),
        "tests": [{"name": "t1", "status": "passed"}],
    }


def _types(state):
    return [e["event_type"] for e in state["events"]]


def test_full_scene(tmp_path):
    c = _client(tmp_path)

    # 1. join
    s = c.post("/api/agents/backend-agent/join").json()
    assert s["agents"][0]["verified"] is True
    s = c.post("/api/agents/frontend-agent/join").json()
    s = c.post("/api/agents/telemetry-agent/join").json()
    assert all(a["verified"] for a in s["agents"])

    # revoked / unknown identity is refused
    assert c.post("/api/agents/rogue-agent/join").status_code == 404

    # 2. claim
    c.post("/api/workstreams/backend/claim", json={"agent_id": "backend-agent"})
    c.post("/api/workstreams/frontend/claim", json={"agent_id": "frontend-agent"})
    s = c.post("/api/workstreams/telemetry/claim", json={"agent_id": "telemetry-agent"}).json()
    assert {w["status"] for w in s["workstreams"]} == {"active"}

    # wrong agent cannot claim
    r = c.post("/api/workstreams/backend/claim", json={"agent_id": "frontend-agent"})
    assert r.status_code == 403

    # 3. declare intent -> three file collisions open before implementation
    c.post("/api/workstreams/backend/declare",
           json={"agent_id": "backend-agent", "contract": _contract("provides", APPROVED)})
    c.post("/api/workstreams/frontend/declare",
           json={"agent_id": "frontend-agent", "contract": _contract("consumes", APPROVED)})
    s = c.post("/api/workstreams/telemetry/declare",
               json={"agent_id": "telemetry-agent", "contract": _contract("consumes", APPROVED)}).json()
    open_conflicts = [x for x in s["conflicts"] if x["status"] == "open"]
    assert len(open_conflicts) == 3
    assert {x["type"] for x in open_conflicts} == {"file"}
    assert {x["conflicting_field"] for x in open_conflicts} == {"src/auth/session.ts"}
    assert {x["decision_id"] for x in open_conflicts} == {"scope-boundaries"}
    assert {w["status"] for w in s["workstreams"]} == {"blocked"}
    assert "conflict_opened" in _types(s)

    # submit while blocked -> 409
    r = c.post("/api/workstreams/frontend/submit",
               json=_changeset("frontend", "frontend-agent", APPROVED, ["src/components/login/x.tsx"]))
    assert r.status_code == 409

    # 4. coordinator assigns one owner per file -> collisions resolve
    c.post("/api/workstreams/backend/scope", json={
        "agent_id": "backend-agent",
        "owned_paths": ["src/api/auth/oauth.ts", "src/auth/session.ts", "src/api/auth/oauth.test.ts"],
    })
    c.post("/api/workstreams/frontend/scope", json={
        "agent_id": "frontend-agent",
        "owned_paths": ["src/components/login/OrganizationLogin.tsx", "src/components/login/x.tsx"],
    })
    s = c.post("/api/workstreams/telemetry/scope", json={
        "agent_id": "telemetry-agent",
        "owned_paths": ["src/lib/analytics/authEvents.ts"],
    }).json()
    assert all(x["status"] == "resolved" for x in s["conflicts"])
    assert {w["status"] for w in s["workstreams"]} == {"active"}
    assert "conflict_resolved" in _types(s)
    assert _types(s).count("scope_reassigned") == 3

    # reassignment is enforced, not just displayed
    escaped = _changeset("frontend", "frontend-agent", APPROVED, ["src/auth/session.ts"])
    r = c.post("/api/workstreams/frontend/submit", json=escaped)
    assert r.status_code == 409
    assert "outside owned scope" in r.json()["detail"]

    # 5. submit all three independent ChangeSets -> complete
    c.post("/api/workstreams/backend/submit",
           json=_changeset("backend", "backend-agent", APPROVED,
                           ["src/api/auth/oauth.ts", "src/auth/session.ts"]))
    cs = _changeset("frontend", "frontend-agent", APPROVED, ["src/components/login/x.tsx"])
    c.post("/api/workstreams/frontend/submit", json=cs)
    telemetry = _changeset("telemetry", "telemetry-agent", APPROVED,
                           ["src/lib/analytics/authEvents.ts"])
    s = c.post("/api/workstreams/telemetry/submit", json=telemetry).json()
    assert {w["status"] for w in s["workstreams"]} == {"complete"}
    assert s["objective"]["status"] == "complete"
    assert _types(s)[-1] == "objective_completed"

    # 6. reset
    s = c.post("/api/reset").json()
    assert s["events"] == [] and s["conflicts"] == []


def test_state_persists_across_requests(tmp_path):
    c = _client(tmp_path)
    c.post("/api/agents/backend-agent/join")
    assert c.get("/api/state").json()["agents"][0]["verified"] is True
