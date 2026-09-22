"""ANS-3 DNS discovery.

Two TXT records per registered agent, both required:

    _ans.<host>        v=ans1; version=v1.0.0; p=mcp; mode=direct; url=https://...
    _ans-badge.<host>  v=ans-badge1; version=v1.0.0; url=<transparency log badge URL>

A host may carry several ACTIVE versions at once, so each lookup returns every
record and the caller selects by the version it already learned from the
identity certificate's URI SAN. Selecting by version rather than by "the first
record" is what stops a stale v1 badge from vouching for a v2 certificate.

DANE/TLSA is deliberately not implemented: it only adds assurance under DNSSEC,
and the demo zone is unsigned. See docs/ANS.md.
"""

import logging
from dataclasses import dataclass, field

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

log = logging.getLogger("synapse.ans")

ANS_PREFIX = "_ans"
BADGE_PREFIX = "_ans-badge"

ANS_TAG = "ans1"
BADGE_TAG = "ans-badge1"


class DnsUnavailable(Exception):
    """DNS could not answer. Indeterminate -- the caller decides the failure policy."""


class NotAnAnsAgent(Exception):
    """NXDOMAIN or no matching record. Determinate rejection (ANS-6 failure handling)."""


def _parse_pairs(txt: str) -> dict[str, str]:
    """Parse `k=v; k=v` into a dict. Keys lowercase, values kept verbatim."""
    pairs: dict[str, str] = {}
    for chunk in txt.split(";"):
        key, sep, value = chunk.partition("=")
        if not sep:
            continue
        pairs[key.strip().lower()] = value.strip()
    return pairs


def _normalize_version(raw: str) -> str:
    """`v1.0.0` and `1.0.0` both mean 1.0.0."""
    value = (raw or "").strip()
    return value[1:] if value.startswith(("v", "V")) else value


@dataclass(frozen=True)
class AnsRecord:
    """One parsed `_ans` TXT record."""

    version: str
    protocol: str | None
    url: str | None
    mode: str | None
    raw: str
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BadgeRecord:
    """One parsed `_ans-badge` TXT record."""

    version: str
    url: str
    raw: str


class DnsDiscovery:
    """Resolves the ANS TXT records for an agent host."""

    def __init__(self, nameservers: list[str] | None = None, timeout: float = 5.0):
        self._resolver = dns.asyncresolver.Resolver()
        if nameservers:
            self._resolver.nameservers = nameservers
        self._resolver.timeout = timeout
        self._resolver.lifetime = timeout

    async def _txt(self, name: str) -> list[str]:
        try:
            answer = await self._resolver.resolve(name, dns.rdatatype.TXT)
        except dns.resolver.NXDOMAIN as exc:
            raise NotAnAnsAgent(f"{name} does not exist") from exc
        except dns.resolver.NoAnswer as exc:
            raise NotAnAnsAgent(f"{name} has no TXT record") from exc
        except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.exception.DNSException) as exc:
            # SERVFAIL/timeout is indeterminate, never "not an agent".
            raise DnsUnavailable(f"{name} lookup failed: {exc}") from exc
        # A TXT rdata is a sequence of <=255-octet strings that concatenate.
        return ["".join(part.decode() for part in rdata.strings) for rdata in answer]

    async def ans_records(self, host: str) -> list[AnsRecord]:
        records = []
        for txt in await self._txt(f"{ANS_PREFIX}.{host}"):
            fields = _parse_pairs(txt)
            if fields.get("v") != ANS_TAG:
                continue
            records.append(
                AnsRecord(
                    version=_normalize_version(fields.get("version", "")),
                    protocol=fields.get("p"),
                    url=fields.get("url"),
                    mode=fields.get("mode"),
                    raw=txt,
                    fields=fields,
                )
            )
        if not records:
            raise NotAnAnsAgent(f"{ANS_PREFIX}.{host} carries no {ANS_TAG} record")
        return records

    async def badge_records(self, host: str) -> list[BadgeRecord]:
        records = []
        for txt in await self._txt(f"{BADGE_PREFIX}.{host}"):
            fields = _parse_pairs(txt)
            if fields.get("v") != BADGE_TAG or not fields.get("url"):
                continue
            records.append(
                BadgeRecord(
                    version=_normalize_version(fields.get("version", "")),
                    url=fields["url"],
                    raw=txt,
                )
            )
        if not records:
            raise NotAnAnsAgent(f"{BADGE_PREFIX}.{host} carries no {BADGE_TAG} record")
        return records

    async def badge_url_for(self, host: str, version: str) -> str:
        """The badge URL for one exact version, as required by ANS-6 §6.2."""
        records = await self.badge_records(host)
        for record in records:
            if record.version == version:
                return record.url
        available = ", ".join(sorted(r.version for r in records)) or "none"
        raise NotAnAnsAgent(
            f"{BADGE_PREFIX}.{host} has no record for version {version} (present: {available})"
        )
