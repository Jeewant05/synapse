"""MCP server exposing the coordinator to agent hosts (Claude Code, Cursor, ...).

Thin on purpose: each tool is one HTTP call to the running coordinator. The
rules live in server/app/service.py; this file only carries them over stdio.

Run:  uv run python -m server.mcp_server
Env:  SYNAPSE_BASE_URL (default http://127.0.0.1:8000), DEMO_TOKEN (optional)
"""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("SYNAPSE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEMO_TOKEN = os.environ.get("DEMO_TOKEN")

mcp = FastMCP("synapse")


def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"X-Demo-Token": DEMO_TOKEN} if DEMO_TOKEN else {}
    response = httpx.request(
        method, f"{BASE_URL}{path}", json=payload, headers=headers, timeout=15
    )
    if response.is_error:
        # Return the coordinator's reason instead of raising, so the agent can read
        # why it was refused: 409 = collision or scope, 403 = identity.
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300]
        return {"error": response.status_code, "detail": detail}
    return response.json()


@mcp.tool()
def get_state() -> dict[str, Any]:
    """Current objective, agents, workstreams, conflicts and the event log."""
    return _call("GET", "/api/state")


@mcp.tool()
def join(agent_id: str) -> dict[str, Any]:
    """Verify this agent's identity and join the workspace. 403 if unverified."""
    return _call("POST", f"/api/agents/{agent_id}/join")


@mcp.tool()
def claim(workstream_id: str, agent_id: str) -> dict[str, Any]:
    """Claim the workstream assigned to this agent."""
    return _call("POST", f"/api/workstreams/{workstream_id}/claim", {"agent_id": agent_id})


@mcp.tool()
def declare(
    workstream_id: str,
    agent_id: str,
    method: str,
    path: str,
    role: str,
    response_fields: dict[str, str],
) -> dict[str, Any]:
    """Declare the API contract this workstream provides or consumes, before writing
    code. The coordinator re-runs its collision rules; open conflicts block the
    workstream until scope is reassigned."""
    contract = {"method": method, "path": path, "role": role, "response_fields": response_fields}
    return _call(
        "POST",
        f"/api/workstreams/{workstream_id}/declare",
        {"agent_id": agent_id, "contract": contract},
    )


@mcp.tool()
def scope(workstream_id: str, agent_id: str, owned_paths: list[str]) -> dict[str, Any]:
    """Reassign the files this workstream owns. Conflicts that no longer fire resolve."""
    return _call(
        "POST",
        f"/api/workstreams/{workstream_id}/scope",
        {"agent_id": agent_id, "owned_paths": owned_paths},
    )


@mcp.tool()
def submit(
    workstream_id: str,
    agent_id: str,
    files: list[str],
    method: str,
    path: str,
    role: str,
    response_fields: dict[str, str],
    tests_passed: bool = True,
) -> dict[str, Any]:
    """Submit a ChangeSet. 409 if a conflict is open, a file is outside scope, a file
    overlaps an accepted ChangeSet, or the contract differs from the declared one."""
    changeset = {
        "id": f"cs-{workstream_id}",
        "workstream_id": workstream_id,
        "agent_id": agent_id,
        "files": files,
        "contract": {
            "method": method, "path": path, "role": role, "response_fields": response_fields,
        },
        "tests": [
            {"name": f"{workstream_id} tests", "status": "passed" if tests_passed else "failed"}
        ],
    }
    return _call("POST", f"/api/workstreams/{workstream_id}/submit", changeset)


if __name__ == "__main__":
    mcp.run()
