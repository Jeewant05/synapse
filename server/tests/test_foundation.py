import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from scripts.database import demo_state
from server.app.adapters import MockIdentity
from server.app.config import Settings
from server.app.main import create_app
from server.app.models import AgentPrincipal
from server.app.store import write_state
from server.app.tracing import DatabricksTraceSink


def test_health_and_seeded_state(tmp_path):
    path = tmp_path / "test.db"
    write_state(path, demo_state())
    client = TestClient(create_app(Settings(demo_token=None, 
        database_path=path, trace_mode="cache", backend_provider="none",
        frontend_provider="none", qa_provider="none",
    )))
    health = client.get("/api/health").json()
    assert health["live_integrations"] is False
    assert health["trace_mode"] == "cache"
    state = client.get("/api/state").json()
    assert len(state["workstreams"]) == 3
    assert not any(agent["verified"] for agent in state["agents"])
    assert state["workstreams"][0]["contract"] != state["workstreams"][1]["contract"]


def test_coordinator_events_are_available_from_trace_api(tmp_path):
    path = tmp_path / "test.db"
    write_state(path, demo_state())
    client = TestClient(create_app(Settings(demo_token=None, 
        database_path=path, trace_mode="cache", backend_provider="none",
        frontend_provider="none", qa_provider="none",
    )))

    assert client.post("/api/agents/backend-agent/join").status_code == 200
    traces = client.get("/api/traces").json()

    assert len(traces) == 1
    assert traces[0]["source"] == "coordinator"
    assert traces[0]["event_type"] == "agent_joined"
    assert traces[0]["agent_id"] == "backend-agent"


def test_unknown_agent_is_not_mock_verified():
    result = asyncio.run(
        MockIdentity().verify(AgentPrincipal(id="unknown", ans_name="unknown.demo", role="unknown"))
    )
    assert not result.verified
    assert result.source == "mock"


def test_databricks_trace_reader_keeps_events_with_legacy_malformed_payloads():
    sink = object.__new__(DatabricksTraceSink)
    sink._table_name = "catalog.schema.traces"

    async def execute(_statement):
        return SimpleNamespace(result=SimpleNamespace(data_array=[[
            "trace-1", "live_agent", "run_failed", "2026-09-19 21:57:27",
            "run-1", None, None, "coordinator", '{"message":"line one\nline two"}',
        ]]))

    sink._execute = execute
    events = asyncio.run(sink.recent())

    assert events[0].payload["payload_parse_error"] is True
    assert "line one" in events[0].payload["raw_payload"]
