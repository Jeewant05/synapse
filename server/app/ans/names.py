"""ANSName parsing.

Canonical form is `ans://v{major}.{minor}.{patch}.{agentHost}` (ANS-2). The version
segment sits between the scheme and the host, so the leftmost `v<semver>.` is the
split point -- the host itself may contain any number of further labels.
"""

import re
from dataclasses import dataclass

SCHEME = "ans://"

# v1.0.0.backend.synapse-vt.us -> ("1", "0", "0", "backend.synapse-vt.us")
_ANS_NAME = re.compile(
    r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)\.(?P<host>.+)$"
)
_HOST = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")

# ANS-2 length ceilings.
MAX_NAME_OCTETS = 400
MAX_HOST_OCTETS = 237


class InvalidANSName(ValueError):
    """Raised when a string is not a well-formed ANSName."""


@dataclass(frozen=True)
class ANSName:
    """A parsed, validated ANSName."""

    host: str
    version: str

    @property
    def value(self) -> str:
        return f"{SCHEME}v{self.version}.{self.host}"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def parse(cls, raw: str) -> "ANSName":
        if not isinstance(raw, str):
            raise InvalidANSName("ANSName must be a string")
        candidate = raw.strip()
        if len(candidate.encode()) > MAX_NAME_OCTETS:
            raise InvalidANSName(f"ANSName exceeds {MAX_NAME_OCTETS} octets")
        if not candidate.lower().startswith(SCHEME):
            raise InvalidANSName(f"ANSName must start with {SCHEME!r}: {raw!r}")
        match = _ANS_NAME.match(candidate[len(SCHEME) :])
        if match is None:
            raise InvalidANSName(f"ANSName must be {SCHEME}v<major.minor.patch>.<host>: {raw!r}")
        host = match["host"].rstrip(".").lower()
        if len(host.encode()) > MAX_HOST_OCTETS:
            raise InvalidANSName(f"agent host exceeds {MAX_HOST_OCTETS} octets")
        if not _HOST.match(host):
            raise InvalidANSName(f"agent host is not a valid FQDN: {host!r}")
        return cls(host=host, version=f"{match['major']}.{match['minor']}.{match['patch']}")

    @classmethod
    def build(cls, host: str, version: str) -> "ANSName":
        return cls.parse(f"{SCHEME}v{version}.{host}")

    def matches_host(self, other: str) -> bool:
        """Case-insensitive host comparison, tolerating a trailing root dot."""
        return self.host == (other or "").strip().rstrip(".").lower()
