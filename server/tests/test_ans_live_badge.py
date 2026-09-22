"""Parse a real GoDaddy transparency-log badge.

The badge shape was guessed from the reference specification until a live one
existed. It was wrong: certificates live at
`payload.producer.event.attestations.validIdentityCerts`, which none of the
original candidate paths matched, so every agent would have been refused with
"badge lists no identity certificates".

This fixture is the actual badge for ans://v1.0.0.backend.synapse-vt.us. It
fails if the parser drifts from the shape the log really serves.
"""

import json
import pathlib

import pytest

from server.app.ans.badge import BadgeError, BadgeVerifier, parse_badge
from server.app.ans.names import ANSName

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "live-badge.json"
ANS_NAME = "ans://v1.0.0.backend.synapse-vt.us"
IDENTITY_FP = "d8612968783ecaba05d832f9c81ce5351b6e222b5527fcb844bd67f09ed6331c"
SERVER_FP = "56cbe28c5f400d792c72ff2d4750d114997515fe9611e832944ba4f87d87861b"


@pytest.fixture
def badge():
    return parse_badge(json.loads(FIXTURE.read_text()), "https://transparency.ans.godaddy.com/v1/agents/x")


def test_reads_the_sealed_event(badge):
    assert badge.ans_name == ANS_NAME
    assert badge.host == "backend.synapse-vt.us"
    assert badge.status == "ACTIVE"
    assert badge.is_live


def test_finds_certificates_under_attestations(badge):
    """The path that was wrong. An empty set here means every agent is refused."""
    assert badge.identity_cert_fingerprints == frozenset({IDENTITY_FP})
    assert badge.server_cert_fingerprints == frozenset({SERVER_FP})


def test_strips_the_sha256_prefix(badge):
    """The log writes `SHA256:<hex>`; the verifier compares bare lowercase hex."""
    assert all(":" not in fp and fp.islower() for fp in badge.identity_cert_fingerprints)


def test_accepts_the_registered_identity_certificate(badge):
    verifier = BadgeVerifier(trusted_hosts=frozenset({"transparency.ans.godaddy.com"}))
    verifier.check(badge, ANSName.parse(ANS_NAME), IDENTITY_FP)


def test_rejects_a_certificate_the_log_never_sealed(badge):
    verifier = BadgeVerifier(trusted_hosts=frozenset({"transparency.ans.godaddy.com"}))
    with pytest.raises(BadgeError, match="not among the badge"):
        verifier.check(badge, ANSName.parse(ANS_NAME), "00" * 32)


def test_the_real_transparency_host_is_trusted_by_default():
    """Registration returns badge URLs on transparency.ans.godaddy.com."""
    from server.app.config import Settings

    assert "transparency.ans.godaddy.com" in Settings().trusted_tl_hosts
