"""The ANS identity adapter.

Implements the existing `IdentityAdapter` protocol from server/app/adapters.py, so
the coordinator's transitions are unchanged -- only the evidence behind
`VerificationResult` becomes real.

Two entry points, matching the two questions the coordinator asks:

    verify(agent)          -- is this registration live right now? (badge tier)
    authenticate(request)  -- who is actually calling, and do they hold the key?

`authenticate` is the one that closes the spoofing hole: it establishes all three
ANS-6 proofs together, because any two without the third is exploitable.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from server.app.ans.badge import Badge, BadgeError, BadgeUnavailable, BadgeVerifier
from server.app.ans.dpop import DpopError, DpopVerifier
from server.app.ans.names import ANSName, InvalidANSName
from server.app.ans.resolver import DnsDiscovery, DnsUnavailable, NotAnAnsAgent
from server.app.ans.trust import EMPTY_TRUST_STORE, TrustStore, UntrustedCertificate
from server.app.models import AgentPrincipal, VerificationResult

log = logging.getLogger("synapse.ans")


class AnsVerificationError(Exception):
    """Verification failed. The message is the evidence string shown to operators."""


@dataclass(frozen=True)
class AuthenticatedAgent:
    """The outcome of a fully verified privileged call."""

    ans_name: ANSName
    fingerprint: str
    badge: Badge
    jti: str

    @property
    def evidence(self) -> str:
        return (
            f"ANS badge tier: {self.ans_name.value} status={self.badge.status}; "
            f"identity cert {self.fingerprint[:16]}… sealed in the transparency log; "
            f"DPoP possession proved (jti {self.jti[:8]}…)."
        )


def ans_name_from_certificate(certificate: x509.Certificate) -> ANSName:
    """Read the ANSName out of the identity certificate's URI SAN (ANS-0)."""
    try:
        san = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        uris = san.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound as exc:
        raise AnsVerificationError("identity certificate has no subjectAltName") from exc
    for uri in uris:
        if uri.lower().startswith("ans://"):
            try:
                return ANSName.parse(uri)
            except InvalidANSName as exc:
                raise AnsVerificationError(f"certificate URI SAN is malformed: {exc}") from exc
    raise AnsVerificationError("identity certificate carries no ans:// URI SAN")


class AnsIdentity:
    """Badge-tier ANS verification with ANS-6 Method B proof of possession."""

    def __init__(
        self,
        discovery: DnsDiscovery,
        badges: BadgeVerifier,
        dpop: DpopVerifier,
        trust: TrustStore | None = None,
    ):
        self.discovery = discovery
        self.badges = badges
        self.dpop = dpop
        self.trust = trust or EMPTY_TRUST_STORE

    async def _badge_for(self, ans_name: ANSName) -> Badge:
        try:
            url = await self.discovery.badge_url_for(ans_name.host, ans_name.version)
        except NotAnAnsAgent as exc:
            raise AnsVerificationError(f"not a registered ANS agent: {exc}") from exc
        except DnsUnavailable as exc:
            # Indeterminate: fail closed rather than admit an unverified caller.
            raise AnsVerificationError(f"DNS unavailable, failing closed: {exc}") from exc
        try:
            return await self.badges.fetch(url)
        except BadgeUnavailable as exc:
            raise AnsVerificationError(f"transparency log unavailable: {exc}") from exc
        except BadgeError as exc:
            raise AnsVerificationError(str(exc)) from exc

    async def verify(self, agent: AgentPrincipal) -> VerificationResult:
        """IdentityAdapter protocol: is this principal's registration live?"""
        checked_at = datetime.now(UTC).isoformat()
        try:
            ans_name = ANSName.parse(agent.ans_name)
        except InvalidANSName as exc:
            return VerificationResult(
                agent_id=agent.id,
                verified=False,
                source="ans",
                evidence=f"{agent.ans_name!r} is not a valid ANSName: {exc}",
                checked_at=checked_at,
            )
        try:
            badge = await self._badge_for(ans_name)
        except AnsVerificationError as exc:
            return VerificationResult(
                agent_id=agent.id,
                verified=False,
                source="ans",
                evidence=str(exc),
                checked_at=checked_at,
            )
        live = badge.is_live
        return VerificationResult(
            agent_id=agent.id,
            verified=live,
            source="ans",
            tier="badge",
            badge_status=badge.status or None,
            evidence=(
                f"Transparency log badge for {ans_name.value} reports status "
                f"{badge.status or 'unknown'}"
                + ("." if live else "; not a live state, access refused.")
            ),
            checked_at=checked_at,
        )

    async def authenticate(
        self, proof: str, method: str, path: str, body: bytes
    ) -> AuthenticatedAgent:
        """All three ANS-6 proofs: possession, then identity and liveness."""
        try:
            verified = self.dpop.verify(proof, method, path, body)
        except DpopError as exc:
            raise AnsVerificationError(f"DPoP proof rejected: {exc}") from exc

        # Independent of the badge: was this certificate issued by the ANS RA at all?
        try:
            self.trust.verify(verified.certificate)
        except UntrustedCertificate as exc:
            raise AnsVerificationError(f"untrusted identity certificate: {exc}") from exc

        ans_name = ans_name_from_certificate(verified.certificate)
        badge = await self._badge_for(ans_name)
        try:
            self.badges.check(badge, ans_name, verified.fingerprint)
        except BadgeError as exc:
            raise AnsVerificationError(str(exc)) from exc

        return AuthenticatedAgent(
            ans_name=ans_name,
            fingerprint=verified.fingerprint,
            badge=badge,
            jti=verified.jti,
        )


def build_ans_identity(settings) -> AnsIdentity:
    """Assemble the adapter from settings, failing fast on missing credentials."""
    if not settings.ans_credential:
        raise AnsVerificationError(
            "identity_mode=ans requires ANS_API_KEY (and ANS_API_SECRET) in .env"
        )
    trust = EMPTY_TRUST_STORE
    if settings.ans_identity_ca_bundle:
        trust = TrustStore.from_pem_bundle(settings.ans_identity_ca_bundle)
    log.info("ANS identity trust: %s", trust.describe())
    return AnsIdentity(
        discovery=DnsDiscovery(nameservers=settings.dns_nameservers),
        badges=BadgeVerifier(
            trusted_hosts=settings.trusted_tl_hosts,
            ttl_seconds=settings.ans_badge_ttl_seconds,
        ),
        dpop=DpopVerifier(public_base_url=settings.ans_public_base_url),
        trust=trust,
    )
