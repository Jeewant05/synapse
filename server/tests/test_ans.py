"""ANS verification tests.

Everything here is offline: a self-signed EC P-256 certificate stands in for the
identity certificate the RA would issue, and DNS/badge responses are supplied
directly. No test touches the network or needs credentials.

The DPoP cases mirror ANS-6 §7.4 one check at a time -- a proof that passes
cryptographically but fails any single binding must still be rejected.
"""

import asyncio
import base64
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from server.app.ans.badge import Badge, BadgeError, BadgeVerifier, parse_badge
from server.app.ans.dpop import (
    DpopError,
    DpopVerifier,
    ReplayCache,
    b64url_encode,
    content_digest,
    normalize_htu,
)
from server.app.ans.identity import ans_name_from_certificate
from server.app.ans.names import ANSName, InvalidANSName
from server.app.ans.resolver import _normalize_version, _parse_pairs
from server.app.ans.signer import DpopSigner

BASE_URL = "http://127.0.0.1:8000"
ANS_NAME = "ans://v1.0.0.backend.synapse-vt.us"


def make_identity(ans_name: str = ANS_NAME, *, valid: bool = True):
    """A self-signed EC P-256 cert carrying an ans:// URI SAN, plus its key."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    not_before, not_after = (
        (now - timedelta(hours=1), now + timedelta(days=1))
        if valid
        else (now - timedelta(days=10), now - timedelta(days=5))
    )
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synapse-test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(ans_name)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate, key


@pytest.fixture
def identity():
    return make_identity()


@pytest.fixture
def signer(identity):
    certificate, key = identity
    return DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)


@pytest.fixture
def verifier():
    return DpopVerifier(public_base_url=BASE_URL)


def fingerprint(certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex()


def retamper(proof: str, *, header=None, claims=None) -> str:
    """Rebuild a proof with edited header/claims, keeping the original signature."""
    head, payload, signature = proof.split(".")
    if header is not None:
        head = b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    if claims is not None:
        payload = b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    return f"{head}.{payload}.{signature}"


def parts(proof: str) -> tuple[dict, dict]:
    head, payload, _ = proof.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    return (
        json.loads(base64.urlsafe_b64decode(pad(head))),
        json.loads(base64.urlsafe_b64decode(pad(payload))),
    )


def resign(header: dict, claims: dict, key) -> str:
    """Produce a genuinely signed proof, so a check later in §7.4 order is reached.

    Tampering with a segment trips the signature check first -- correct behaviour,
    but it means a claim-level check can only be exercised by really signing it.
    """
    from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

    encode = lambda value: b64url_encode(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{encode(header)}.{encode(claims)}"
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = asym_utils.decode_dss_signature(der)
    return f"{signing_input}.{b64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# --- ANSName ---------------------------------------------------------------


def test_ansname_roundtrip():
    name = ANSName.parse(ANS_NAME)
    assert name.host == "backend.synapse-vt.us"
    assert name.version == "1.0.0"
    assert name.value == ANS_NAME


def test_ansname_keeps_multi_label_hosts_intact():
    # The version is the leftmost segment; every remaining label is the host.
    assert ANSName.parse("ans://v2.10.3.a.b.c.example.com").host == "a.b.c.example.com"


@pytest.mark.parametrize(
    "raw",
    [
        "backend.synapse-vt.us",  # no scheme
        "ans://backend.synapse-vt.us",  # no version
        "ans://v1.0.backend.synapse-vt.us",  # partial semver
        "ans://v1.0.0.localhost",  # not an FQDN
        "ans://v01.0.0.backend.example.com",  # leading zero
    ],
)
def test_ansname_rejects_malformed(raw):
    with pytest.raises(InvalidANSName):
        ANSName.parse(raw)


def test_ansname_from_certificate(identity):
    certificate, _ = identity
    assert ans_name_from_certificate(certificate).value == ANS_NAME


# --- DNS record parsing ----------------------------------------------------


def test_parse_badge_record_fields():
    txt = "v=ans-badge1; version=v1.0.0; url=https://api.godaddy.com/v1/agents/abc"
    fields = _parse_pairs(txt)
    assert fields["v"] == "ans-badge1"
    assert _normalize_version(fields["version"]) == "1.0.0"
    assert fields["url"] == "https://api.godaddy.com/v1/agents/abc"


# --- DPoP: the happy path --------------------------------------------------


def test_valid_proof_is_accepted(signer, verifier, identity):
    certificate, _ = identity
    body = b'{"agent_id":"backend-agent"}'
    proof = signer.proof("POST", "/workstreams/backend/claim", body)
    result = verifier.verify(proof, "POST", "/workstreams/backend/claim", body)
    assert result.fingerprint == fingerprint(certificate)
    assert result.htu == f"{BASE_URL}/workstreams/backend/claim"


def test_empty_body_digest_is_the_digest_of_empty_bytes(signer, verifier):
    proof = signer.proof("POST", "/agents/backend-agent/join", b"")
    assert verifier.verify(proof, "POST", "/agents/backend-agent/join", b"")
    _, claims = parts(proof)
    assert claims["ans_content_digest"] == content_digest(b"")


# --- DPoP: one rejection per §7.4 check ------------------------------------


def test_rejects_extra_jose_header_parameter(signer, verifier):
    proof = signer.proof("POST", "/reset", b"")
    header, _ = parts(proof)
    header["kid"] = "smuggled"
    with pytest.raises(DpopError, match="forbidden parameters"):
        verifier.verify(retamper(proof, header=header), "POST", "/reset", b"")


def test_rejects_private_key_material_in_jwk(signer, verifier):
    proof = signer.proof("POST", "/reset", b"")
    header, _ = parts(proof)
    header["jwk"]["d"] = "leaked"
    with pytest.raises(DpopError, match="jwk must carry exactly"):
        verifier.verify(retamper(proof, header=header), "POST", "/reset", b"")


def test_rejects_jwk_that_does_not_match_the_certificate(signer, verifier):
    # A valid-looking key for a different keypair must not be accepted.
    other_key = ec.generate_private_key(ec.SECP256R1())
    from server.app.ans.dpop import _jwk_from_public_key

    proof = signer.proof("POST", "/reset", b"")
    header, _ = parts(proof)
    header["jwk"] = _jwk_from_public_key(other_key.public_key())
    with pytest.raises(DpopError, match="does not match the identity certificate"):
        verifier.verify(retamper(proof, header=header), "POST", "/reset", b"")


def test_rejects_multi_entry_x5c(signer, verifier):
    proof = signer.proof("POST", "/reset", b"")
    header, _ = parts(proof)
    header["x5c"] = [header["x5c"][0], header["x5c"][0]]
    with pytest.raises(DpopError, match="exactly one certificate"):
        verifier.verify(retamper(proof, header=header), "POST", "/reset", b"")


def test_rejects_expired_certificate(verifier):
    certificate, key = make_identity(valid=False)
    expired = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = expired.proof("POST", "/reset", b"")
    with pytest.raises(DpopError, match="not currently valid"):
        verifier.verify(proof, "POST", "/reset", b"")


def test_rejects_tampered_signature(signer, verifier):
    head, payload, signature = signer.proof("POST", "/reset", b"").split(".")
    flipped = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    flipped[0] ^= 0xFF
    with pytest.raises(DpopError, match="signature is invalid"):
        verifier.verify(f"{head}.{payload}.{b64url_encode(bytes(flipped))}", "POST", "/reset", b"")


def test_rejects_wrong_method(signer, verifier):
    proof = signer.proof("POST", "/reset", b"")
    with pytest.raises(DpopError, match="htm"):
        verifier.verify(proof, "DELETE", "/reset", b"")


def test_rejects_proof_replayed_against_a_different_path(signer, verifier):
    # A proof captured on one endpoint must not authorize another.
    proof = signer.proof("POST", "/workstreams/backend/claim", b"")
    with pytest.raises(DpopError, match="htu"):
        verifier.verify(proof, "POST", "/workstreams/telemetry/submit", b"")


def test_rejects_htu_for_a_different_authority(identity, verifier):
    certificate, key = identity
    elsewhere = DpopSigner(
        certificate=certificate, private_key=key, base_url="https://evil.example.com"
    )
    proof = elsewhere.proof("POST", "/reset", b"")
    with pytest.raises(DpopError, match="htu"):
        verifier.verify(proof, "POST", "/reset", b"")


def test_rejects_stale_iat(signer, verifier, identity):
    _, key = identity
    header, claims = parts(signer.proof("POST", "/reset", b""))
    claims["iat"] = int(time.time()) - 600
    with pytest.raises(DpopError, match="clock-skew"):
        verifier.verify(resign(header, claims, key), "POST", "/reset", b"")


def test_rejects_future_dated_iat(signer, verifier, identity):
    _, key = identity
    header, claims = parts(signer.proof("POST", "/reset", b""))
    claims["iat"] = int(time.time()) + 600
    with pytest.raises(DpopError, match="clock-skew"):
        verifier.verify(resign(header, claims, key), "POST", "/reset", b"")


def test_rejects_missing_jti(signer, verifier, identity):
    _, key = identity
    header, claims = parts(signer.proof("POST", "/reset", b""))
    del claims["jti"]
    with pytest.raises(DpopError, match="jti is required"):
        verifier.verify(resign(header, claims, key), "POST", "/reset", b"")


def test_rejects_missing_content_digest(signer, verifier, identity):
    _, key = identity
    header, claims = parts(signer.proof("POST", "/reset", b""))
    del claims["ans_content_digest"]
    with pytest.raises(DpopError, match="ans_content_digest is required"):
        verifier.verify(resign(header, claims, key), "POST", "/reset", b"")


def test_rejects_tampered_body(signer, verifier):
    body = b'{"agent_id":"backend-agent"}'
    proof = signer.proof("POST", "/workstreams/backend/claim", body)
    swapped = b'{"agent_id":"telemetry-agent"}'
    with pytest.raises(DpopError, match="ans_content_digest"):
        verifier.verify(proof, "POST", "/workstreams/backend/claim", swapped)


def test_rejects_replayed_jti(signer, verifier):
    body = b""
    proof = signer.proof("POST", "/reset", body)
    assert verifier.verify(proof, "POST", "/reset", body)
    with pytest.raises(DpopError, match="already been used"):
        verifier.verify(proof, "POST", "/reset", body)


def test_failed_proof_does_not_consume_its_jti(signer, verifier):
    """A rejection must not burn the jti, or a failure becomes a denial of service."""
    body = b'{"a":1}'
    proof = signer.proof("POST", "/reset", body)
    with pytest.raises(DpopError):
        verifier.verify(proof, "POST", "/reset", b'{"a":2}')
    assert verifier.verify(proof, "POST", "/reset", body)


def test_replay_cache_fails_closed_when_saturated():
    cache = ReplayCache(max_entries=1, retention_seconds=900)
    cache.remember("first")
    with pytest.raises(DpopError, match="saturated"):
        cache.remember("second")


def test_normalize_htu_drops_default_port_and_query():
    assert normalize_htu("HTTPS://Example.COM:443/a/b?x=1#f") == "https://example.com/a/b"
    assert normalize_htu("http://example.com") == "http://example.com/"


# --- Badge checks ----------------------------------------------------------


def badge_for(certificate, status: str = "ACTIVE") -> Badge:
    return Badge(
        ans_name=ANS_NAME,
        host="backend.synapse-vt.us",
        status=status,
        identity_cert_fingerprints=frozenset({fingerprint(certificate)}),
        server_cert_fingerprints=frozenset(),
        url="https://api.godaddy.com/v1/agents/abc",
        raw={},
    )


def test_badge_accepts_a_live_sealed_certificate(identity):
    certificate, _ = identity
    verifier = BadgeVerifier(trusted_hosts=frozenset({"api.godaddy.com"}))
    verifier.check(badge_for(certificate), ANSName.parse(ANS_NAME), fingerprint(certificate))


@pytest.mark.parametrize("status", ["REVOKED", "EXPIRED"])
def test_badge_rejects_terminal_status(identity, status):
    certificate, _ = identity
    verifier = BadgeVerifier(trusted_hosts=frozenset({"api.godaddy.com"}))
    with pytest.raises(BadgeError, match=status):
        verifier.check(badge_for(certificate, status), ANSName.parse(ANS_NAME), fingerprint(certificate))


def test_badge_rejects_certificate_not_sealed_in_the_log(identity):
    certificate, _ = identity
    other, _ = make_identity()
    verifier = BadgeVerifier(trusted_hosts=frozenset({"api.godaddy.com"}))
    with pytest.raises(BadgeError, match="not among the badge"):
        verifier.check(badge_for(certificate), ANSName.parse(ANS_NAME), fingerprint(other))


def test_badge_url_host_must_be_allowlisted():
    """A forged TXT record must not steer the verifier at an arbitrary host."""
    verifier = BadgeVerifier(trusted_hosts=frozenset({"api.godaddy.com"}))
    with pytest.raises(BadgeError, match="not in the trusted"):
        asyncio.run(verifier.fetch("https://attacker.example.com/v1/agents/abc"))


def test_badge_url_must_be_https():
    verifier = BadgeVerifier(trusted_hosts=frozenset({"api.godaddy.com"}))
    with pytest.raises(BadgeError, match="must be https"):
        asyncio.run(verifier.fetch("http://api.godaddy.com/v1/agents/abc"))


def test_parse_badge_reads_nested_sealed_event():
    payload = {
        "status": "ACTIVE",
        "payload": {
            "producer": {
                "event": {
                    "ansName": ANS_NAME,
                    "agent": {"host": "backend.synapse-vt.us"},
                }
            }
        },
        "identityCerts": [{"fingerprint": "SHA256:AB:CD"}],
    }
    badge = parse_badge(payload, "https://api.godaddy.com/v1/agents/abc")
    assert badge.ans_name == ANS_NAME
    assert badge.host == "backend.synapse-vt.us"
    assert badge.identity_cert_fingerprints == frozenset({"abcd"})
