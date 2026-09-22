"""Where the agents' ANS identity material comes from.

Locally it sits in `.local/ans/<agent>/`, which is gitignored and excluded from
the container image -- a private key must never be baked into an image. On a
deployment it arrives through the ANS_AGENT_IDENTITIES secret instead:

    {"backend-agent": {"cert": "<base64 PEM>", "key": "<base64 PEM>"}}

Base64 so the PEM newlines survive an environment variable intact.
"""

import base64
import binascii
import json
import logging
from pathlib import Path

log = logging.getLogger("synapse.ans")

MATERIAL_ROOT = Path(__file__).resolve().parents[3] / ".local" / "ans"


class MaterialMissing(Exception):
    """No identity material for that agent, from either source."""


def _decode(value: str, label: str) -> str:
    """Accept base64 or raw PEM, so a hand-set secret works either way."""
    if "BEGIN" in value:
        return value
    try:
        return base64.b64decode(value, validate=True).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise MaterialMissing(f"{label} is neither PEM nor valid base64: {exc}") from exc


def from_secret(raw: str | None, agent_id: str) -> tuple[str, str] | None:
    if not raw or not raw.strip():
        return None
    try:
        bundle = json.loads(raw)
    except ValueError as exc:
        log.warning("ANS_AGENT_IDENTITIES is not valid JSON, ignoring: %s", exc)
        return None
    entry = bundle.get(agent_id) if isinstance(bundle, dict) else None
    if not isinstance(entry, dict) or "cert" not in entry or "key" not in entry:
        return None
    return _decode(entry["cert"], f"{agent_id} cert"), _decode(entry["key"], f"{agent_id} key")


def from_disk(agent_id: str, root: Path | None = None) -> tuple[str, str] | None:
    directory = (root or MATERIAL_ROOT) / agent_id
    certificate, key = directory / "identity.crt", directory / "identity.key"
    if not (certificate.is_file() and key.is_file()):
        return None
    return certificate.read_text(), key.read_text()


def load(agent_id: str, secret: str | None = None, root: Path | None = None) -> tuple[str, str]:
    """The secret wins, so a deployment never silently falls back to stale files."""
    material = from_secret(secret, agent_id) or from_disk(agent_id, root)
    if material is None:
        raise MaterialMissing(
            f"no ANS identity material for {agent_id}: set ANS_AGENT_IDENTITIES or "
            f"run `npm run ans -- certs --agent {agent_id}`"
        )
    return material
