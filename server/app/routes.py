"""Coordinator HTTP API. Owner: P1. Every call returns the full WorkspaceState.

In ANS mode every mutating call must carry an ANS-6 Method B `DPoP` proof. The
proven ANSName -- taken from the identity certificate, not from the request body
-- is handed to the coordinator, which refuses the call when it does not match
the `agent_id` the caller claims. That is what makes `agent_id` unforgeable.

In mock mode the dependency is inert and the endpoints behave exactly as before.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.app.ans.gate import build_authenticator, proven
from server.app.ans.identity import AnsIdentity, AuthenticatedAgent
from server.app.demo_runner import build_demo_guard
from server.app.models import ApiContract, ChangeSet, WorkspaceState
from server.app.service import Blocked, Coordinator, Forbidden, NotFound


class ClaimRequest(BaseModel):
    agent_id: str


class DeclareRequest(BaseModel):
    agent_id: str
    contract: ApiContract


class ScopeRequest(BaseModel):
    agent_id: str
    owned_paths: list[str]


def build_router(
    coordinator: Coordinator,
    fresh_state,
    ans_identity: AnsIdentity | None = None,
    dpop_required: bool = True,
    demo_token: str | None = None,
) -> APIRouter:
    # One rule for the whole API: everything under /api, static UI at /.
    # DPoP htu binds the exact path, so dev and prod must agree.
    router = APIRouter(prefix="/api")
    authenticate = build_authenticator(ans_identity, dpop_required)

    # Annotated form rather than a Depends() default: same wiring, and it keeps the
    # dependency out of the function's mutable-default position.
    Caller = Annotated[AuthenticatedAgent | None, Depends(authenticate)]

    @router.post("/agents/{agent_id}/join", response_model=WorkspaceState)
    async def join(agent_id: str, caller: Caller):
        try:
            return await coordinator.join(agent_id, proven_ans_name=proven(caller))
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except Forbidden as e:
            raise HTTPException(403, str(e)) from e

    @router.post("/workstreams/{ws_id}/claim", response_model=WorkspaceState)
    async def claim(
        ws_id: str, body: ClaimRequest, caller: Caller
    ):
        try:
            return await coordinator.claim(ws_id, body.agent_id, proven_ans_name=proven(caller))
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except Forbidden as e:
            raise HTTPException(403, str(e)) from e

    @router.post("/workstreams/{ws_id}/declare", response_model=WorkspaceState)
    async def declare(
        ws_id: str, body: DeclareRequest, caller: Caller
    ):
        try:
            return await coordinator.declare(
                ws_id, body.agent_id, body.contract, proven_ans_name=proven(caller)
            )
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except Forbidden as e:
            raise HTTPException(403, str(e)) from e

    @router.post("/workstreams/{ws_id}/scope", response_model=WorkspaceState)
    async def reassign_scope(
        ws_id: str, body: ScopeRequest, caller: Caller
    ):
        try:
            return await coordinator.reassign_scope(
                ws_id, body.agent_id, body.owned_paths, proven_ans_name=proven(caller)
            )
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except Forbidden as e:
            raise HTTPException(403, str(e)) from e
        except Blocked as e:
            raise HTTPException(409, str(e)) from e

    @router.post("/workstreams/{ws_id}/submit", response_model=WorkspaceState)
    async def submit(
        ws_id: str, body: ChangeSet, caller: Caller
    ):
        try:
            return await coordinator.submit(ws_id, body, proven_ans_name=proven(caller))
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except Forbidden as e:
            raise HTTPException(403, str(e)) from e
        except Blocked as e:
            raise HTTPException(409, str(e)) from e

    @router.post("/reset", response_model=WorkspaceState,
                 dependencies=[Depends(build_demo_guard(demo_token))])
    async def reset():
        """Replace the workspace with the seeded fixture.

        Destructive, so it is behind the shared demo secret: on a public
        deployment an open reset lets anyone wipe the demo mid-presentation.
        """
        return coordinator.reset(fresh_state())

    return router
