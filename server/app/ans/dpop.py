"""ANS-6 Method B: application-layer proof of possession.

The agent signs a compact JWS with the private key of its ANS identity
certificate and sends it in the `DPoP` header. That proves the caller holds the
key *for this request* -- the third leg of ANS-6, alongside identity and liveness
from the badge. Without it, `agent_id` is self-asserted and the identity gate is
decorative.

Written against `cryptography` rather than PyJWT on purpose: §7.4 requires
*rejecting* a proof whose JOSE header carries any parameter beyond the four
permitted ones, and PyJWT ignores unknown header members.

The check order below is normative. Two orderings matter for security and are
called out at the call sites: the `jwk` must be compared to the certificate's
public key *before* the signature is verified, and `jti` is recorded in the
replay cache *only after* every other check has passed.
"""

import base64
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

MAX_PROOF_BYTES = 8 * 1024
MAX_JTI_BYTES = 128
DEFAULT_SKEW_SECONDS = 120

REQUIRED_HEADER_KEYS = frozenset({"typ", "alg", "jwk", "x5c"})
REQUIRED_JWK_KEYS = frozenset({"kty", "crv", "x", "y"})
DPOP_TYP = "dpop+jwt"
DPOP_ALG = "ES256"

DEFAULT_PORTS = {"http": 80, "https": 443}


class DpopError(Exception):
    """A DPoP proof failed verification. The message is safe to log as evidence."""


@dataclass(frozen=True)
class DpopProof:
    """The verified proof, with the certificate the caller proved possession of."""

    certificate: x509.Certificate
    fingerprint: str
    jti: str
    issued_at: int
    htm: str
    htu: str
    claims: dict[str, Any]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse a JSON object with a repeated member name.

    `json.loads` keeps the last occurrence, so `{"htu":"a","htu":"b"}` parses
    cleanly -- but another implementation, or an intermediary, may read the
    first. That disagreement is exactly the canonicalization gap an attacker
    needs: one value gets signed and a different one gets enforced. There is no
    legitimate reason for a duplicate member in a DPoP proof, so it is a
    rejection rather than a normalization.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate member {key!r}")
        seen[key] = value
    return seen


def b64url_decode(segment: str) -> bytes:
    """Strict base64url decode. Rejects padding and non-url alphabets."""
    if not isinstance(segment, str) or not segment:
        raise DpopError("empty base64url segment")
    if "=" in segment or "+" in segment or "/" in segment:
        raise DpopError("segment is not unpadded base64url")
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode())
    except (ValueError, TypeError) as exc:
        raise DpopError(f"segment is not valid base64url: {exc}") from exc


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def content_digest(body: bytes) -> str:
    """`ans_content_digest`: base64url(SHA-256(content)); empty body is the digest of b''."""
    return b64url_encode(hashlib.sha256(body or b"").digest())


def normalize_htu(url: str) -> str:
    """Scheme/host lowercased, default port dropped, query and fragment stripped."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        raise DpopError(f"cannot normalize htu {url!r}")
    port = parts.port
    authority = host if port is None or DEFAULT_PORTS.get(scheme) == port else f"{host}:{port}"
    return f"{scheme}://{authority}{parts.path or '/'}"


def cert_fingerprint(certificate: x509.Certificate) -> str:
    """Lowercase hex SHA-256 over the DER encoding, as the badge records it."""
    return certificate.fingerprint(hashes.SHA256()).hex()


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _jwk_from_public_key(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(_int_to_bytes(numbers.x)),
        "y": b64url_encode(_int_to_bytes(numbers.y)),
    }


class ReplayCache:
    """Bounded single-use `jti` store.

    ANS-6 requires failing closed when this is unavailable or saturated, so
    eviction of an unexpired entry is treated as saturation rather than silently
    forgetting a proof that could then be replayed.
    """

    def __init__(self, max_entries: int = 20_000, retention_seconds: float = 900.0):
        self.max_entries = max_entries
        self.retention_seconds = retention_seconds
        self._entries: OrderedDict[str, float] = OrderedDict()

    def _evict_expired(self, now: float) -> None:
        while self._entries:
            _, expires_at = next(iter(self._entries.items()))
            if expires_at > now:
                break
            self._entries.popitem(last=False)

    def remember(self, jti: str, now: float | None = None) -> None:
        """Record a jti, or raise if it has already been seen."""
        current = time.time() if now is None else now
        self._evict_expired(current)
        if jti in self._entries:
            raise DpopError("DPoP proof has already been used (jti replay)")
        if len(self._entries) >= self.max_entries:
            raise DpopError("replay cache is saturated; refusing to accept the proof")
        self._entries[jti] = current + self.retention_seconds


class DpopVerifier:
    """Verifies ANS-6 Method B proofs against a trusted expected URL."""

    def __init__(
        self,
        public_base_url: str,
        replay_cache: ReplayCache | None = None,
        skew_seconds: int = DEFAULT_SKEW_SECONDS,
    ):
        # The comparison authority comes from configuration, never from the
        # request's Host header (§7.4 step 7).
        self.public_base_url = public_base_url.rstrip("/")
        self.replay_cache = replay_cache or ReplayCache()
        self.skew_seconds = skew_seconds

    def expected_htu(self, path: str) -> str:
        return normalize_htu(f"{self.public_base_url}/{path.lstrip('/')}")

    def verify(self, proof: str, method: str, path: str, body: bytes) -> DpopProof:
        now = time.time()

        # 1. Size bound, before any parsing.
        if not proof:
            raise DpopError("missing DPoP proof")
        if len(proof.encode()) > MAX_PROOF_BYTES:
            raise DpopError("DPoP proof exceeds the maximum accepted size")

        # 2. Compact JWS shape.
        segments = proof.split(".")
        if len(segments) != 3:
            raise DpopError("DPoP proof is not a compact JWS")
        header_segment, payload_segment, signature_segment = segments

        # 3. Strict header decode -- exactly the four permitted parameters.
        header = self._decode_json(header_segment, "header")
        extra = set(header) - REQUIRED_HEADER_KEYS
        if extra:
            raise DpopError(f"DPoP header carries forbidden parameters: {sorted(extra)}")
        missing = REQUIRED_HEADER_KEYS - set(header)
        if missing:
            raise DpopError(f"DPoP header is missing {sorted(missing)}")
        if header["typ"] != DPOP_TYP:
            raise DpopError(f"typ must be {DPOP_TYP!r}")
        if header["alg"] != DPOP_ALG:
            raise DpopError(f"alg must be {DPOP_ALG!r}")

        jwk = header["jwk"]
        if not isinstance(jwk, dict):
            raise DpopError("jwk must be an object")
        if set(jwk) != REQUIRED_JWK_KEYS:
            raise DpopError(f"jwk must carry exactly {sorted(REQUIRED_JWK_KEYS)}")
        if jwk["kty"] != "EC" or jwk["crv"] != "P-256":
            raise DpopError("jwk must be an EC P-256 public key")

        x5c = header["x5c"]
        if not isinstance(x5c, list) or len(x5c) != 1:
            raise DpopError("x5c must contain exactly one certificate")

        # 4. Certificate parse and validity window.
        certificate = self._load_certificate(x5c[0])
        public_key = certificate.public_key()
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
            public_key.curve, ec.SECP256R1
        ):
            raise DpopError("identity certificate must hold an EC P-256 key")
        not_before = certificate.not_valid_before_utc.timestamp()
        not_after = certificate.not_valid_after_utc.timestamp()
        if not not_before <= now <= not_after:
            raise DpopError("identity certificate is not currently valid")

        # 5. jwk must equal the certificate's key -- checked BEFORE the signature,
        #    so a proof can never be verified against an attacker-supplied key.
        if _jwk_from_public_key(public_key) != {k: jwk[k] for k in REQUIRED_JWK_KEYS}:
            raise DpopError("jwk does not match the identity certificate's public key")

        # 6. Signature over the reconstructed JWS signing input.
        self._verify_signature(public_key, header_segment, payload_segment, signature_segment)

        claims = self._decode_json(payload_segment, "payload")

        # 7. HTTP binding.
        if claims.get("htm") != method.upper():
            raise DpopError(f"htm {claims.get('htm')!r} does not match request method {method!r}")
        expected = self.expected_htu(path)
        try:
            presented = normalize_htu(str(claims.get("htu", "")))
        except DpopError as exc:
            raise DpopError(f"htu is not a usable URL: {exc}") from exc
        if presented != expected:
            raise DpopError(f"htu {presented!r} does not match expected {expected!r}")

        # 9. Freshness.
        issued_at = claims.get("iat")
        if not isinstance(issued_at, int):
            raise DpopError("iat must be an integer")
        if abs(now - issued_at) > self.skew_seconds:
            raise DpopError("DPoP proof iat is outside the accepted clock-skew window")

        # 10. Proof identifier.
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti:
            raise DpopError("jti is required")
        if len(jti.encode()) > MAX_JTI_BYTES:
            raise DpopError("jti exceeds the maximum length")

        # 13. Content integrity over the body actually received.
        digest = claims.get("ans_content_digest")
        if not isinstance(digest, str) or not digest:
            raise DpopError("ans_content_digest is required")
        if digest != content_digest(body):
            raise DpopError("ans_content_digest does not match the request body")

        # 14. Replay cache last, so a proof rejected above is never consumed.
        self.replay_cache.remember(jti, now)

        return DpopProof(
            certificate=certificate,
            fingerprint=cert_fingerprint(certificate),
            jti=jti,
            issued_at=issued_at,
            htm=method.upper(),
            htu=presented,
            claims=claims,
        )

    @staticmethod
    def _decode_json(segment: str, label: str) -> dict[str, Any]:
        try:
            value = json.loads(b64url_decode(segment), object_pairs_hook=_reject_duplicate_keys)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DpopError(f"DPoP {label} is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise DpopError(f"DPoP {label} must be a JSON object")
        return value

    @staticmethod
    def _load_certificate(entry: Any) -> x509.Certificate:
        if not isinstance(entry, str):
            raise DpopError("x5c entry must be a base64 string")
        try:
            # x5c is standard base64 of the DER, not base64url (RFC 7515 §4.1.6).
            return x509.load_der_x509_certificate(base64.b64decode(entry, validate=True))
        except Exception as exc:
            raise DpopError(f"x5c entry is not a valid DER certificate: {exc}") from exc

    @staticmethod
    def _verify_signature(
        public_key: ec.EllipticCurvePublicKey,
        header_segment: str,
        payload_segment: str,
        signature_segment: str,
    ) -> None:
        raw = b64url_decode(signature_segment)
        if len(raw) != 64:
            raise DpopError("ES256 signature must be 64 bytes (R||S)")
        der = asym_utils.encode_dss_signature(
            int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
        )
        signing_input = f"{header_segment}.{payload_segment}".encode()
        try:
            public_key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise DpopError("DPoP signature is invalid") from exc
