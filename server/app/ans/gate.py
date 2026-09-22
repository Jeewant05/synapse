"""Shared ANS-6 request gate.

The coordinator router and the orchestration router both let an agent mutate
state, so both need the same three proofs. Keeping the header handling in one
place means a fix to the ANS-6 rules cannot land on one router and miss the
other.

In mock mode the authenticator is inert and returns None, so every endpoint
behaves exactly as it did before ANS.
"""

from fastapi import HTTPException, Request

from server.app.ans.identity import AnsVerificationError, AuthenticatedAgent

NO_PROOF = (
    "identity_mode=ans requires an ANS-6 DPoP proof on this call. Browsers cannot "
    "hold agent identity keys; drive this flow with the scripted agents."
)


def build_authenticator(ans_identity, dpop_required: bool = True):
    """A FastAPI dependency that proves possession, identity and liveness."""
    enforcing = ans_identity is not None and dpop_required

    async def authenticate(request: Request) -> AuthenticatedAgent | None:
        if not enforcing:
            return None
        proofs = request.headers.getlist("dpop")
        if len(proofs) > 1:
            # ANS-6: a duplicated security header is a rejection, not a choice.
            raise HTTPException(401, "multiple DPoP headers presented")
        if not proofs:
            raise HTTPException(401, NO_PROOF)
        # Starlette caches the body, so reading it here does not disturb binding.
        body = await request.body()
        try:
            return await ans_identity.authenticate(
                proofs[0], request.method, request.url.path, body
            )
        except AnsVerificationError as exc:
            raise HTTPException(401, str(exc)) from exc

    return authenticate


def proven(caller: AuthenticatedAgent | None) -> str | None:
    """The ANSName the caller actually proved, or None in mock mode."""
    return caller.ans_name.value if caller else None
