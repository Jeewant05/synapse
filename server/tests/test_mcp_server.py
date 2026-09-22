"""The MCP surface exposes exactly the coordinator's six operations."""

import asyncio

from server.mcp_server import mcp


def test_mcp_exposes_the_six_coordinator_tools():
    tools = asyncio.run(mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "get_state", "join", "claim", "declare", "scope", "submit",
    }
