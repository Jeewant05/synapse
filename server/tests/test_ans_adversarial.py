"""Adversarial battery against the ANS identity gate.

Modelled on the attack classes an external fraud-test agent runs against ANS
suppliers. The payment-specific rows (mandate amount, quote binding, on-chain
settlement replay) have no analogue here -- Synapse authorizes file scope, not
money -- so they are represented by their structural equivalents: binding a
proof to one caller, one endpoint and one body.

Every attack MUST be blocked, and MUST be blocked *cleanly*: the verifier has to
raise its own typed error rather than escape as an unhandled exception, because
an unhandled exception is a 500 and a 500 is not a denial.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from server.app.ans.badge import Badge, BadgeVerifier
from server.app.ans.dpop import DpopError, DpopVerifier, b64url_encode
from server.app.ans.identity import AnsIdentity, AnsVerificationError
from server.app.ans.signer import DpopSigner
from server.app.ans.trust import TrustStore, UntrustedCertificate
from server.tests.test_ans import BASE_URL, make_identity, parts, resign

VICTIM = "ans://v1.0.0.backend.synapse-vt.us"
ATTACKER = "ans://v1.0.2.fraud.webmesh.ai"


def fingerprint(certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex()


class StubDiscovery:
    """DNS that always points at one allowlisted badge URL."""

    def __init__(self, url="https://api.godaddy.com/v1/agents/victim"):
        self.url = url

    async def badge_url_for(self, host, version):
        return self.url


class StubBadges(BadgeVerifier):
    """Badge verifier serving a fixed badge, keeping the real check() logic."""

    def __init__(self, badge: Badge):
        super().__init__(trusted_hosts=frozenset({"api.godaddy.com"}))
        self._badge = badge

    async def fetch(self, url):
        self._check_url(url)
        return self._badge


def victim_badge(certificate, status="ACTIVE") -> Badge:
    return Badge(
        ans_name=VICTIM,
        host="backend.synapse-vt.us",
        status=status,
        identity_cert_fingerprints=frozenset({fingerprint(certificate)}),
        server_cert_fingerprints=frozenset(),
        url="https://api.godaddy.com/v1/agents/victim",
        raw={},
    )


def build_identity(badge: Badge) -> AnsIdentity:
    return AnsIdentity(
        discovery=StubDiscovery(),
        badges=StubBadges(badge),
        dpop=DpopVerifier(public_base_url=BASE_URL),
    )


def attack(fn, *args, **kwargs):
    """Run an attack; return the typed error, or fail loudly on the wrong outcome."""
    try:
        fn(*args, **kwargs)
    except (DpopError, AnsVerificationError) as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - totality: this is itself a finding
        pytest.fail(f"VULNERABLE (not clean): escaped as {type(exc).__name__}: {exc}")
    pytest.fail("VULNERABLE: the attack was accepted")


# --- Signature and key binding --------------------------------------------


def test_attack_wrong_dpop_key(verifier_and_victim):
    """A proof signed by a key other than the certificate's must be rejected."""
    verifier, certificate, _ = verifier_and_victim
    rogue = ec.generate_private_key(ec.SECP256R1())
    header, claims = parts(
        DpopSigner(
            certificate=certificate, private_key=rogue, base_url=BASE_URL
        ).proof("POST", "/reset", b"")
    )
    # Header advertises the real certificate; the signature is the rogue key's.
    proof = resign(header, claims, rogue)
    assert attack(verifier.verify, proof, "POST", "/reset", b"")


def test_attack_corrupt_jws_is_rejected_cleanly(verifier_and_victim):
    """Flipping signature bytes must produce a typed rejection, never a crash."""
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    head, payload, signature = signer.proof("POST", "/reset", b"").split(".")
    raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    raw[-1] ^= 0xFF
    raw[-2] ^= 0xFF
    error = attack(
        verifier.verify, f"{head}.{payload}.{b64url_encode(bytes(raw))}", "POST", "/reset", b""
    )
    assert isinstance(error, DpopError)


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "not-a-jws",
        "a.b",
        "a.b.c.d",
        "....",
        "!!!.???.***",
        "e30.e30.",
        "x" * 20_000,
    ],
)
def test_attack_malformed_proofs_are_rejected_cleanly(verifier_and_victim, garbage):
    """Verifier totality: no input shape may escape as an unhandled exception."""
    verifier, _, _ = verifier_and_victim
    assert attack(verifier.verify, garbage, "POST", "/reset", b"")


def test_attack_superseded_format_stripped_header(verifier_and_victim):
    """A legacy/incomplete proof must not be leniently accepted."""
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    header, claims = parts(signer.proof("POST", "/reset", b""))
    del header["x5c"]
    assert attack(verifier.verify, resign(header, claims, key), "POST", "/reset", b"")


# --- Replay ----------------------------------------------------------------


def test_attack_replay_spent_proof(verifier_and_victim):
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    assert verifier.verify(proof, "POST", "/reset", b"")
    assert attack(verifier.verify, proof, "POST", "/reset", b"")


def test_attack_replay_across_endpoints(verifier_and_victim):
    """wrong_scope equivalent: a proof for one route must not authorize another."""
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = signer.proof("POST", "/workstreams/backend/claim", b"")
    assert attack(verifier.verify, proof, "POST", "/workstreams/telemetry/submit", b"")


def test_attack_wrong_audience(verifier_and_victim):
    """A proof minted for another supplier must not be accepted here."""
    verifier, certificate, key = verifier_and_victim
    elsewhere = DpopSigner(
        certificate=certificate, private_key=key, base_url="https://rogue-supplier.example.com"
    )
    assert attack(verifier.verify, elsewhere.proof("POST", "/reset", b""), "POST", "/reset", b"")


# --- Canonicalization drift ------------------------------------------------


@pytest.mark.parametrize("value", [True, 1.0, "1700000000", None, [], {}])
def test_attack_iat_type_confusion(verifier_and_victim, value):
    """M2: a non-integer iat must be refused, not coerced."""
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    header, claims = parts(signer.proof("POST", "/reset", b""))
    claims["iat"] = value
    assert attack(verifier.verify, resign(header, claims, key), "POST", "/reset", b"")


def test_attack_duplicate_json_keys(verifier_and_victim):
    """Duplicate members let two parsers disagree about what was signed."""
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    header, claims = parts(signer.proof("POST", "/reset", b""))
    # Two htu members: a lenient parser takes one, a strict one must refuse.
    raw = json.dumps(claims)
    doubled = raw[:-1] + ',"htu":"http://127.0.0.1:8000/reset"}'
    head = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    payload = b64url_encode(doubled.encode())
    signing_input = f"{head}.{payload}"
    from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = asym_utils.decode_dss_signature(der)
    proof = f"{signing_input}.{b64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    assert attack(verifier.verify, proof, "POST", "/reset", b"")


def test_attack_tampered_body(verifier_and_victim):
    verifier, certificate, key = verifier_and_victim
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = signer.proof("POST", "/workstreams/backend/claim", b'{"agent_id":"backend-agent"}')
    assert attack(
        verifier.verify, proof, "POST", "/workstreams/backend/claim", b'{"agent_id":"admin"}'
    )


# --- Trust anchor: the unknown-key / fail-closed row -----------------------


def test_attack_unknown_ca_self_signed_certificate():
    """A self-signed cert claiming the victim's ANSName must be refused.

    This is the unknown_key row: the attacker mints their own certificate with
    the victim's ans:// URI SAN. The DPoP layer alone cannot catch it -- the
    signature really is valid for that key -- so the trust anchor has to.
    """
    victim_cert, _ = make_identity(VICTIM)
    forged_cert, forged_key = make_identity(VICTIM)
    identity = build_identity(victim_badge(victim_cert))
    signer = DpopSigner(certificate=forged_cert, private_key=forged_key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    error = attack(
        lambda: asyncio.run(identity.authenticate(proof, "POST", "/reset", b"")),
    )
    assert isinstance(error, AnsVerificationError)


def test_attack_badge_lists_no_certificates_fails_closed():
    """An empty identityCerts array must deny, never admit."""
    certificate, key = make_identity(VICTIM)
    empty = Badge(
        ans_name=VICTIM,
        host="backend.synapse-vt.us",
        status="ACTIVE",
        identity_cert_fingerprints=frozenset(),
        server_cert_fingerprints=frozenset(),
        url="https://api.godaddy.com/v1/agents/victim",
        raw={},
    )
    identity = build_identity(empty)
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    assert attack(lambda: asyncio.run(identity.authenticate(proof, "POST", "/reset", b"")))


@pytest.mark.parametrize("status", ["REVOKED", "EXPIRED", "", "BOGUS"])
def test_attack_non_live_badge_is_refused(status):
    certificate, key = make_identity(VICTIM)
    identity = build_identity(victim_badge(certificate, status))
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    assert attack(lambda: asyncio.run(identity.authenticate(proof, "POST", "/reset", b"")))


def test_attack_certificate_without_ans_san():
    """A certificate with no ans:// URI SAN has no ANS identity to assert."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "no-san")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    identity = build_identity(victim_badge(certificate))
    signer = DpopSigner(certificate=certificate, private_key=key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    assert attack(lambda: asyncio.run(identity.authenticate(proof, "POST", "/reset", b"")))


def test_attack_badge_for_a_different_ansname():
    """A genuinely registered attacker must not pass as the victim."""
    attacker_cert, attacker_key = make_identity(ATTACKER)
    identity = build_identity(victim_badge(attacker_cert))  # badge still names the victim
    signer = DpopSigner(certificate=attacker_cert, private_key=attacker_key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    assert attack(lambda: asyncio.run(identity.authenticate(proof, "POST", "/reset", b"")))


@pytest.fixture
def verifier_and_victim():
    certificate, key = make_identity(VICTIM)
    return DpopVerifier(public_base_url=BASE_URL), certificate, key


# --- Trust anchor as an independent layer ----------------------------------


def make_ca(name: str = "ANS Test RA"):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate, key


def issue_from(ca_cert, ca_key, ans_name: str):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "issued")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(ans_name)]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return certificate, key


def test_trust_store_accepts_a_certificate_from_its_anchor():
    ca_cert, ca_key = make_ca()
    leaf, _ = issue_from(ca_cert, ca_key, VICTIM)
    TrustStore(anchors=[ca_cert]).verify(leaf)


def test_trust_store_rejects_a_self_signed_certificate():
    ca_cert, _ = make_ca()
    forged, _ = make_identity(VICTIM)
    with pytest.raises(UntrustedCertificate, match="not a configured ANS trust anchor"):
        TrustStore(anchors=[ca_cert]).verify(forged)


def test_trust_store_rejects_a_forged_issuer_name():
    """Claiming the CA's subject without its key must not be enough."""
    ca_cert, _ = make_ca()
    impostor_cert, impostor_key = make_ca("ANS Test RA")  # same subject, different key
    leaf, _ = issue_from(impostor_cert, impostor_key, VICTIM)
    with pytest.raises(UntrustedCertificate, match="does not verify"):
        TrustStore(anchors=[ca_cert]).verify(leaf)


def test_unconfigured_trust_store_is_explicit_about_it():
    store = TrustStore(anchors=[])
    store.verify(make_identity(VICTIM)[0])  # no-op, does not raise
    assert "sole anchor" in store.describe()


def test_attack_untrusted_ca_blocked_when_bundle_configured():
    """With a bundle configured, a forged cert dies before the badge is consulted."""
    ca_cert, _ = make_ca()
    forged_cert, forged_key = make_identity(VICTIM)
    identity = AnsIdentity(
        discovery=StubDiscovery(),
        badges=StubBadges(victim_badge(forged_cert)),  # badge would have allowed it
        dpop=DpopVerifier(public_base_url=BASE_URL),
        trust=TrustStore(anchors=[ca_cert]),
    )
    signer = DpopSigner(certificate=forged_cert, private_key=forged_key, base_url=BASE_URL)
    proof = signer.proof("POST", "/reset", b"")
    error = attack(lambda: asyncio.run(identity.authenticate(proof, "POST", "/reset", b"")))
    assert "untrusted identity certificate" in str(error)
