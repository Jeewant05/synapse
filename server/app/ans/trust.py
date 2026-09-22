"""Identity-certificate trust anchor (ANS-6 §6.1).

The badge already refuses a certificate whose fingerprint is not sealed in the
transparency log, and that alone blocks a self-signed certificate bearing someone
else's ANSName. This module is the second, independent layer: it checks that the
certificate was actually issued by the ANS Registration Authority's private CA.

Two layers because they fail differently. The badge check depends on correctly
locating `identityCerts[]` in a response shape we do not control; chain
validation depends only on cryptography we hold locally. An attacker would have
to defeat both.

The bundle is optional: with no bundle configured the badge remains the sole
anchor, and `describe()` says so rather than implying a check that is not
happening.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

log = logging.getLogger("synapse.ans")


class UntrustedCertificate(Exception):
    """The identity certificate does not chain to a configured ANS trust anchor."""


@dataclass
class TrustStore:
    """ANS Registration Authority issuing certificates."""

    anchors: list[x509.Certificate]

    @classmethod
    def from_pem_bundle(cls, path: str | Path) -> "TrustStore":
        raw = Path(path).read_bytes()
        anchors = x509.load_pem_x509_certificates(raw)
        if not anchors:
            raise UntrustedCertificate(f"{path} contains no certificates")
        return cls(anchors=anchors)

    @property
    def configured(self) -> bool:
        return bool(self.anchors)

    def describe(self) -> str:
        if not self.configured:
            return "no ANS CA bundle configured; transparency-log badge is the sole anchor"
        subjects = ", ".join(anchor.subject.rfc4514_string() for anchor in self.anchors)
        return f"{len(self.anchors)} ANS trust anchor(s): {subjects}"

    def verify(self, certificate: x509.Certificate) -> None:
        """Check the leaf was signed by one of the anchors."""
        if not self.configured:
            return
        issuer = certificate.issuer
        candidates = [a for a in self.anchors if a.subject == issuer]
        if not candidates:
            raise UntrustedCertificate(
                f"issuer {issuer.rfc4514_string()!r} is not a configured ANS trust anchor"
            )
        for anchor in candidates:
            if self._signed_by(certificate, anchor):
                return
        raise UntrustedCertificate(
            "identity certificate signature does not verify against its stated ANS issuer"
        )

    @staticmethod
    def _signed_by(certificate: x509.Certificate, anchor: x509.Certificate) -> bool:
        public_key = anchor.public_key()
        try:
            if isinstance(public_key, ec.EllipticCurvePublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    ec.ECDSA(certificate.signature_hash_algorithm),
                )
            elif isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    certificate.signature_hash_algorithm,
                )
            else:
                return False
        except (InvalidSignature, TypeError, ValueError):
            return False
        return True


EMPTY_TRUST_STORE = TrustStore(anchors=[])
