"""Small deterministic clients used to exercise the coordinator over HTTP.

These clients intentionally model scripted agents, not autonomous agents. They use
only the public coordinator API so the same flow can be run against a local server
or a deployed review environment.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from server.app.ans.signer import DpopSigner

# Where scripts/ans_register.sh leaves each agent's identity material.
ANS_MATERIAL_ROOT = Path(__file__).resolve().parents[1] / ".local" / "ans"


@dataclass(frozen=True)
class AgentClient:
    """HTTP client for one coordinator agent.

    When `signer` is set, every call carries an ANS-6 Method B `DPoP` proof, so
    the coordinator can bind the request to a registered ANS identity instead of
    trusting the `agent_id` in the body. Without it the client behaves exactly as
    before, which is what mock mode expects.
    """

    base_url: str
    agent_id: str
    signer: DpopSigner | None = None

    @classmethod
    def with_ans_identity(
        cls, base_url: str, agent_id: str, material_dir: Path | None = None
    ) -> "AgentClient":
        """Load the identity certificate and key `ans-cli` issued for this agent."""
        directory = material_dir or ANS_MATERIAL_ROOT / agent_id
        return cls(
            base_url=base_url,
            agent_id=agent_id,
            signer=DpopSigner.from_files(
                cert_path=directory / "identity.crt",
                key_path=directory / "identity.key",
                base_url=base_url,
            ),
        )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        # Serialize here so the signed digest covers exactly the bytes sent.
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers: dict[str, str] = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.signer is not None:
            headers["DPoP"] = self.signer.proof(method, path, body)
        response = httpx.request(
            method,
            f"{self.base_url.rstrip('/')}{path}",
            timeout=15,
            content=body or None,
            headers=headers or None,
        )
        response.raise_for_status()
        return response.json()

    def join(self) -> dict[str, Any]:
        return self._request("POST", f"/api/agents/{self.agent_id}/join")

    def claim(self, workstream_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/workstreams/{workstream_id}/claim", {"agent_id": self.agent_id}
        )

    def declare(self, workstream_id: str, contract: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/workstreams/{workstream_id}/declare",
            {"agent_id": self.agent_id, "contract": contract},
        )

    def scope(self, workstream_id: str, owned_paths: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/workstreams/{workstream_id}/scope",
            {"agent_id": self.agent_id, "owned_paths": owned_paths},
        )

    def submit(
        self,
        workstream_id: str,
        contract: dict[str, Any],
        files: list[str],
        tests: list[dict[str, str]],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/workstreams/{workstream_id}/submit",
            {
                "id": f"test-{workstream_id}",
                "workstream_id": workstream_id,
                "agent_id": self.agent_id,
                "files": files,
                "contract": contract,
                "tests": tests,
            },
        )


APPROVED_CONTRACT = {
    "method": "POST",
    "path": "/api/oauth",
    "role": "provides",
    "response_fields": {"token": "string", "user": "object"},
}

CONSUMER_CONTRACT = {
    "method": "POST",
    "path": "/api/oauth",
    "role": "consumes",
    "response_fields": {"token": "string", "user": "object"},
}

INCOMPATIBLE_CONTRACT = {
    "method": "POST",
    "path": "/api/oauth",
    "role": "consumes",
    "response_fields": {"accessToken": "string", "profile": "object"},
}

PASSED_TEST = [{"name": "agent smoke test", "status": "passed"}]
