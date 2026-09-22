"""Read-only client for the ANS Registry Authority.

Registration, ACME validation and certificate issuance all happen through
`ans-cli` (docs/ANS.md) -- this client exists so the coordinator and the
diagnostic scripts can read an agent's lifecycle state straight from the RA
without shelling out.

Authentication is GoDaddy's `sso-key <key>:<secret>` scheme.
"""

import logging
from typing import Any

import httpx

log = logging.getLogger("synapse.ans")


class RegistryError(Exception):
    """The RA rejected the call or could not be reached."""


class AnsRegistryClient:
    """Minimal RA reader."""

    def __init__(self, base_url: str, credential: str, timeout: float = 10.0):
        if not credential:
            raise RegistryError("ANS_API_KEY is required for identity_mode=ans")
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"sso-key {credential}",
            "Accept": "application/json",
        }
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._headers, params=params)
        except httpx.HTTPError as exc:
            raise RegistryError(f"ANS registry unreachable: {exc}") from exc
        if response.status_code in (401, 403):
            raise RegistryError(f"ANS registry rejected the credential ({response.status_code})")
        if response.is_error:
            raise RegistryError(
                f"ANS registry returned {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    async def agent(self, agent_id: str) -> dict[str, Any]:
        """Lifecycle detail for one registered agent."""
        return await self._get(f"/v1/agents/{agent_id}")

    async def search(self, **criteria: Any) -> dict[str, Any]:
        """Discovery search. At least one criterion is required by the RA."""
        params = {k: v for k, v in criteria.items() if v is not None}
        if not params:
            raise RegistryError("at least one search criterion is required")
        return await self._get("/v1/agents", params)
