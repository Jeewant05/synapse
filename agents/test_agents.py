"""End-to-end smoke test for the two scripted demo agents.

Run with a seeded API server:

    uv run python -m agents.test_agents

The test deliberately verifies the important coordination behavior: incompatible
contracts block a changeset, and a compatible redeclaration allows both agents to
complete. It exits non-zero on any failed assertion so it is suitable for CI.
"""

import argparse

import httpx

from agents.clients import (
    APPROVED_CONTRACT,
    CONSUMER_CONTRACT,
    INCOMPATIBLE_CONTRACT,
    PASSED_TEST,
    AgentClient,
)


def run(base_url: str) -> None:
    """Exercise the public API as backend-agent and frontend-agent."""
    base_url = base_url.rstrip("/")
    with httpx.Client(base_url=base_url, timeout=5) as client:
        reset = client.post("/reset")
        reset.raise_for_status()

    backend = AgentClient(base_url, "backend-agent")
    frontend = AgentClient(base_url, "frontend-agent")

    assert backend.join()["agents"][0]["verified"] is True
    assert all(agent["verified"] for agent in frontend.join()["agents"])
    backend.claim("backend")
    frontend.claim("frontend")

    backend.declare("backend", APPROVED_CONTRACT)
    blocked_state = frontend.declare("frontend", INCOMPATIBLE_CONTRACT)
    open_conflicts = [c for c in blocked_state["conflicts"] if c["status"] == "open"]
    assert len(open_conflicts) == 1, "incompatible contracts should open one conflict"
    assert {ws["status"] for ws in blocked_state["workstreams"]} == {"blocked"}

    blocked = frontend
    try:
        blocked.submit("frontend", INCOMPATIBLE_CONTRACT, ["agent-test.ts"], PASSED_TEST)
    except httpx.HTTPStatusError as error:
        assert error.response.status_code == 409, error.response.text
    else:
        raise AssertionError("a blocked changeset unexpectedly succeeded")

    frontend.declare("frontend", CONSUMER_CONTRACT)
    backend.submit("backend", APPROVED_CONTRACT, ["oauth.ts"], PASSED_TEST)
    complete = frontend.submit("frontend", CONSUMER_CONTRACT, ["login.tsx"], PASSED_TEST)
    assert complete["objective"]["status"] == "complete"
    assert all(ws["status"] == "complete" for ws in complete["workstreams"])
    print("Agent coordination smoke test passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    run(args.base_url)


if __name__ == "__main__":
    main()
