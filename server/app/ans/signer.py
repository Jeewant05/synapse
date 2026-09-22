"""Agent-side counterpart to dpop.py: builds ANS-6 Method B proofs.

An agent loads the identity certificate and private key that `ans-cli` produced
for its ANSName and signs one proof per request. Every proof is single use --
`jti` is fresh each time and the coordinator records it.
"""

import base64
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils
from cryptography.x509 import Certificate, load_pem_x509_certificate

from server.app.ans.dpop import (
    DPOP_ALG,
    DPOP_TYP,
    _jwk_from_public_key,
    b64url_encode,
    content_digest,
    normalize_htu,
)
from server.app.ans.names import ANSName


class SignerError(Exception):
    """The identity material could not be loaded or used."""


@dataclass
class DpopSigner:
    """Signs DPoP proofs with an ANS identity certificate's private key."""

    certificate: Certificate
    private_key: ec.EllipticCurvePrivateKey
    base_url: str

    @classmethod
    def from_pem(cls, certificate_pem: str, key_pem: str, base_url: str) -> "DpopSigner":
        """Build a signer from PEM text.

        Deployments cannot ship the material on disk -- private keys must not be
        baked into an image -- so it arrives through a secret instead.
        """
        try:
            certificate = load_pem_x509_certificate(certificate_pem.encode())
            private_key = serialization.load_pem_private_key(key_pem.encode(), password=None)
        except ValueError as exc:
            raise SignerError(f"could not parse ANS identity material: {exc}") from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve, ec.SECP256R1
        ):
            raise SignerError("ANS identity key must be EC P-256")
        return cls(certificate=certificate, private_key=private_key, base_url=base_url)

    @classmethod
    def from_files(
        cls, cert_path: str | Path, key_path: str | Path, base_url: str
    ) -> "DpopSigner":
        try:
            certificate = load_pem_x509_certificate(Path(cert_path).read_bytes())
            private_key = serialization.load_pem_private_key(
                Path(key_path).read_bytes(), password=None
            )
        except (OSError, ValueError) as exc:
            raise SignerError(f"could not load ANS identity material: {exc}") from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve, ec.SECP256R1
        ):
            raise SignerError("ANS identity key must be EC P-256")
        return cls(certificate=certificate, private_key=private_key, base_url=base_url)

    @property
    def ans_name(self) -> ANSName:
        """The ANSName from the certificate's URI SAN."""
        from server.app.ans.identity import ans_name_from_certificate

        return ans_name_from_certificate(self.certificate)

    def proof(self, method: str, path: str, body: bytes = b"") -> str:
        header = {
            "typ": DPOP_TYP,
            "alg": DPOP_ALG,
            "jwk": _jwk_from_public_key(self.private_key.public_key()),
            # Standard base64 of the DER, per RFC 7515 §4.1.6.
            "x5c": [
                base64.b64encode(
                    self.certificate.public_bytes(serialization.Encoding.DER)
                ).decode()
            ],
        }
        claims = {
            "htm": method.upper(),
            "htu": normalize_htu(f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"),
            "iat": int(time.time()),
            "jti": uuid.uuid4().hex,
            "ans_content_digest": content_digest(body),
        }
        signing_input = f"{self._segment(header)}.{self._segment(claims)}"
        der = self.private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{signing_input}.{b64url_encode(raw)}"

    @staticmethod
    def _segment(value: dict) -> str:
        return b64url_encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
