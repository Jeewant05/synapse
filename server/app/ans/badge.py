"""Transparency Log badge verification (ANS-6 badge tier).

The badge answers two of the three proofs an ANS relying party needs: identity
(this certificate is sealed in the log for this agent) and liveness (the
registration is valid right now). Possession is proved separately -- see dpop.py.

Badge-tier rather than SCITT tier: liveness comes from a live log query instead
of a locally verified COSE receipt. ANS-6 permits this and it makes revocation
visible immediately, at the cost of a network call on the verification path.

The badge URL comes out of DNS, which means it is attacker-influenced input. Its
host is checked against a configured allowlist *before* the fetch, so a forged
TXT record cannot point the coordinator at an arbitrary URL.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from server.app.ans.names import ANSName

log = logging.getLogger("synapse.ans")

# ANS-6 §6.2: a badge in one of these states is live enough to act on.
LIVE_STATUSES = frozenset({"ACTIVE", "WARNING", "DEPRECATED"})
TERMINAL_STATUSES = frozenset({"REVOKED", "EXPIRED"})


class BadgeError(Exception):
    """The badge could not be fetched or did not satisfy the ANS-6 checks."""


class BadgeUnavailable(BadgeError):
    """The log could not be reached. Indeterminate; callers fail closed."""


@dataclass(frozen=True)
class Badge:
    """The fields ANS-6 requires a verifier to match against."""

    ans_name: str
    host: str
    status: str
    identity_cert_fingerprints: frozenset[str]
    server_cert_fingerprints: frozenset[str]
    url: str
    raw: dict[str, Any]

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES


def _first(payload: dict[str, Any], *paths: str) -> Any:
    """Read the first present dotted path.

    The reference log and the GoDaddy deployment nest the sealed event slightly
    differently, so every field is looked up through a short list of candidates
    rather than one hard-coded path. docs/ANS.md records how to pin these against
    a real `ans-cli badge --json` response.
    """
    for path in paths:
        cursor: Any = payload
        for key in path.split("."):
            if not isinstance(cursor, dict) or key not in cursor:
                cursor = None
                break
            cursor = cursor[key]
        if cursor is not None:
            return cursor
    return None


def _fingerprints(value: Any) -> frozenset[str]:
    """Normalize a cert list into a set of lowercase hex SHA-256 fingerprints."""
    if not value:
        return frozenset()
    entries = value if isinstance(value, list) else [value]
    out: set[str] = set()
    for entry in entries:
        raw = entry
        if isinstance(entry, dict):
            raw = (
                entry.get("fingerprint")
                or entry.get("sha256")
                or entry.get("certificateFingerprint")
                or entry.get("thumbprint")
            )
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip().lower().removeprefix("sha256:").replace(":", "")
        if cleaned:
            out.add(cleaned)
    return frozenset(out)


def parse_badge(payload: dict[str, Any], url: str) -> Badge:
    # Path order matters: the live GoDaddy log nests everything under the sealed
    # producer event, with certificates under `attestations`. Those paths are
    # pinned against a real badge in server/tests/fixtures/, and the flatter
    # candidates are kept for the reference implementation's shape.
    event = "payload.producer.event"
    attest = f"{event}.attestations"
    return Badge(
        ans_name=_first(payload, f"{event}.ansName", "ansName", "agent.ansName") or "",
        host=_first(payload, f"{event}.agent.host", "agent.host", "host") or "",
        status=str(_first(payload, "status", "agentStatus", f"{event}.status") or "").upper(),
        identity_cert_fingerprints=_fingerprints(
            _first(
                payload,
                f"{attest}.validIdentityCerts",
                f"{attest}.identityCert",
                "identityCerts",
                "validIdentityCerts",
                f"{event}.identityCerts",
            )
        ),
        server_cert_fingerprints=_fingerprints(
            _first(
                payload,
                f"{attest}.validServerCerts",
                f"{attest}.serverCert",
                "serverCerts",
                "validServerCerts",
                f"{event}.serverCerts",
            )
        ),
        url=url,
        raw=payload,
    )


class BadgeVerifier:
    """Fetches and checks Transparency Log badges, with a short TTL cache."""

    def __init__(
        self,
        trusted_hosts: frozenset[str],
        ttl_seconds: float = 60.0,
        timeout: float = 10.0,
    ):
        self.trusted_hosts = trusted_hosts
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, tuple[float, Badge]] = {}

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise BadgeError(f"badge URL must be https: {url!r}")
        host = (parsed.hostname or "").lower()
        if not host:
            raise BadgeError(f"badge URL has no host: {url!r}")
        # Allowlist check happens before any network call (ANS-6 §6.2).
        if self.trusted_hosts and host not in self.trusted_hosts:
            raise BadgeError(
                f"badge host {host!r} is not in the trusted transparency-log allowlist"
            )

    async def fetch(self, url: str) -> Badge:
        self._check_url(url)
        cached = self._cache.get(url)
        now = time.monotonic()
        if cached and now - cached[0] < self.ttl_seconds:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise BadgeUnavailable(f"badge fetch failed: {exc}") from exc
        if response.is_error:
            raise BadgeUnavailable(f"badge fetch returned {response.status_code}")
        try:
            badge = parse_badge(response.json(), url)
        except ValueError as exc:
            raise BadgeError(f"badge is not valid JSON: {exc}") from exc
        self._cache[url] = (now, badge)
        return badge

    def check(self, badge: Badge, expected: ANSName, cert_fingerprint: str) -> None:
        """The four ANS-6 §6.2 equalities. Raises BadgeError on any mismatch."""
        if badge.status in TERMINAL_STATUSES:
            raise BadgeError(f"agent registration is {badge.status}")
        if not badge.is_live:
            raise BadgeError(f"badge status {badge.status or 'unknown'!r} is not a live state")
        if badge.ans_name and badge.ans_name.strip().lower() != expected.value.lower():
            raise BadgeError(
                f"badge ANSName {badge.ans_name!r} does not match certificate {expected.value!r}"
            )
        if badge.host and not expected.matches_host(badge.host):
            raise BadgeError(
                f"badge host {badge.host!r} does not match ANSName host {expected.host!r}"
            )
        known = badge.identity_cert_fingerprints
        if not known:
            raise BadgeError("badge lists no identity certificates to match against")
        if cert_fingerprint.lower() not in known:
            raise BadgeError("identity certificate is not among the badge's sealed certificates")

    def invalidate(self, url: str) -> None:
        self._cache.pop(url, None)
