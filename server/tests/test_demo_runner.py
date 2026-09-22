"""The server-side demo runner, driven against a real app over a faked transport.

The runner is what `/api/demo/run` calls, so this covers the button the dashboard
presses. The seeded fixture hands all three workstreams `src/auth/session.ts` on
purpose, and the coordinator refuses a ChangeSet while any conflict is open --
so the phase order is the thing under test, not an implementation detail.
"""

import httpx
from fastapi.testclient import TestClient

from scripts.database import demo_state
from server.app import demo_runner
from server.app.config import Settings
from server.app.main import create_app
from server.app.store import write_state


def _settings(tmp_path) -> Settings:
    path = tmp_path / "demo.db"
    write_state(path, demo_state())
    return Settings(
        database_path=path,
        identity_mode="mock",
        trace_mode="cache",
        backend_provider="none",
        frontend_provider="none",
        qa_provider="none",
    )


def _route_into(client: TestClient, monkeypatch) -> None:
    """Send the scripted agents' blocking httpx calls into the TestClient."""

    def request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        return client.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", request)


def test_run_completes_the_seeded_scene(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    _route_into(client, monkeypatch)

    result = demo_runner._run(settings, "http://testserver")
    assert result.status == "complete"
    assert result.agents == ["backend-agent", "frontend-agent", "telemetry-agent"]

    state = client.get("/api/state").json()
    assert {w["status"] for w in state["workstreams"]} == {"complete"}
    assert state["objective"]["status"] == "complete"
    assert len(state["conflicts"]) == 3
    assert all(c["status"] == "resolved" for c in state["conflicts"])
    assert {c["conflicting_field"] for c in state["conflicts"]} == {"src/auth/session.ts"}
    assert state["events"][-1]["event_type"] == "objective_completed"


def test_mock_mode_client_carries_no_signer(tmp_path):
    """Mock mode has no identity material, so asking for a signer would fail the run."""
    settings = _settings(tmp_path)
    client = demo_runner._client_for(settings, "http://testserver", "backend-agent")
    assert client.signer is None
    assert client.base_url == "http://testserver"
