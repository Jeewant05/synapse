"""HTTP-01 ACME responder for ANS domain validation.

ANS registration offers DNS-01 and HTTP-01. DNS-01 means publishing a TXT record
per agent and waiting for propagation; HTTP-01 means answering on a URL we
already serve. We control the web server for all four hostnames, so HTTP-01
removes the slowest and most error-prone step -- base64 tokens contain `/` and
`=`, which DNS editors mangle.

Challenges are supplied as JSON in ACME_CHALLENGES so they can be rotated with a
secret update rather than a code change:

    {"<token>": "<keyAuthorization>"}

The responder is read-only and reveals nothing: a key authorization is only
useful to whoever already knows the token, and the validator is the one asking.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

log = logging.getLogger("synapse.acme")


def parse_challenges(raw: str | None) -> dict[str, str]:
    """Token -> keyAuthorization, tolerant of an unset or malformed value."""
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        log.warning("ACME_CHALLENGES is not valid JSON, ignoring: %s", exc)
        return {}
    if not isinstance(value, dict):
        log.warning("ACME_CHALLENGES must be a JSON object, ignoring")
        return {}
    return {str(k): str(v) for k, v in value.items()}


def build_acme_router(challenges: dict[str, str]) -> APIRouter:
    router = APIRouter(tags=["acme"])

    # `:path` because ANS tokens are base64 and contain `/`, so the token spans
    # more than one URL segment.
    @router.get("/.well-known/acme-challenge/{token:path}", response_class=PlainTextResponse)
    def respond(token: str) -> str:
        key_authorization = challenges.get(token)
        if key_authorization is None:
            raise HTTPException(404, "unknown ACME challenge token")
        return key_authorization

    return router
