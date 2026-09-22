"""HTTP surface for the strict three-agent orchestration flow."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.app.ans.gate import build_authenticator, proven
from server.app.ans.identity import AnsIdentity, AuthenticatedAgent
from server.app.models import (
    IntentionDocument,
    Objective,
    OrchestrationChangeSet,
    OrchestrationState,
    ResolutionProposal,
)
from server.app.orchestration import OrchestrationError, OrchestrationKernel
from server.app.orchestrator_agent import OrchestratorAgent


class ObjectiveRequest(BaseModel):
    id: str = "objective-oauth"
    title: str = "Add organization-level OAuth login"
    description: str = "Coordinate three agents before overlapping changes are written."
    acceptance_criteria: list[str] = Field(default_factory=lambda: [
        "Every agent declares a structured intention before execution.",
        "Contract and dependency conflicts resolve before code changes begin.",
        "Every submitted ChangeSet matches its approved intention.",
    ])


class ResolutionApproval(BaseModel):
    approved_by: str


def build_orchestration_router(
    kernel: OrchestrationKernel,
    orchestrator: OrchestratorAgent,
    ans_identity: AnsIdentity | None = None,
    dpop_required: bool = True,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["orchestration"])
    # These endpoints mutate an agent's scope lease and land ChangeSets, so they
    # need the same possession proof the coordinator requires. Verifying identity
    # and liveness without possession would accept a replayed public artifact.
    authenticate = build_authenticator(ans_identity, dpop_required)
    Caller = Annotated[AuthenticatedAgent | None, Depends(authenticate)]

    def fail(error: OrchestrationError) -> HTTPException:
        return HTTPException(status_code=409, detail=str(error))

    @router.post("/objectives", response_model=OrchestrationState)
    async def create_objective(body: ObjectiveRequest | None = None):
        body = body or ObjectiveRequest()
        return await kernel.create_objective(Objective(
            id=body.id, title=body.title, description=body.description,
            acceptance_criteria=body.acceptance_criteria,
        ))

    @router.get("/objectives/{objective_id}/status", response_model=OrchestrationState)
    async def status(objective_id: str):
        try:
            return kernel.state(objective_id)
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/objectives/{objective_id}/orchestrate", response_model=OrchestrationState)
    async def orchestrate(objective_id: str):
        try:
            return await kernel.orchestrate(objective_id, orchestrator)
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/agents/{agent_id}/plan", response_model=OrchestrationState)
    async def plan(agent_id: str, caller: Caller, intention: IntentionDocument | None = None):
        try:
            state = await kernel.plan(agent_id, intention, proven_ans_name=proven(caller))
            if state.workstreams and all(workstream.intention for workstream in state.workstreams):
                return await kernel.orchestrate(state.objective.id, orchestrator)
            return state
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/intentions/validate", response_model=OrchestrationState)
    async def validate_intention(intention: IntentionDocument, caller: Caller):
        try:
            state = await kernel.plan(intention.agent_id, intention, proven_ans_name=proven(caller))
            if state.workstreams and all(workstream.intention for workstream in state.workstreams):
                return await kernel.orchestrate(state.objective.id, orchestrator)
            return state
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/conflicts/detect", response_model=OrchestrationState)
    async def detect_conflicts(objective_id: str):
        try:
            state = kernel.state(objective_id)
            await kernel._validate_and_detect(state)  # kernel owns the state transition
            from server.app.store import write_orchestration_state
            write_orchestration_state(kernel.db_path, state)
            return state
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/conflicts/{conflict_id}/resolve", response_model=ResolutionProposal)
    async def resolve_conflict(conflict_id: str):
        try:
            return await kernel.propose_resolution(conflict_id)
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/conflicts/{conflict_id}/approve", response_model=OrchestrationState)
    async def approve_conflict(conflict_id: str, body: ResolutionApproval, caller: Caller):
        try:
            return await kernel.approve_resolution(conflict_id, body.approved_by)
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/agents/{agent_id}/execute", response_model=OrchestrationState)
    async def execute(agent_id: str, caller: Caller):
        try:
            return await kernel.execute(agent_id, proven_ans_name=proven(caller))
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/changesets/submit", response_model=OrchestrationState)
    async def submit_changeset(changeset: OrchestrationChangeSet, caller: Caller):
        try:
            return await kernel.submit_changeset(changeset)
        except OrchestrationError as error:
            raise fail(error) from error

    @router.post("/objectives/{objective_id}/converge", response_model=OrchestrationState)
    async def converge(objective_id: str):
        try:
            return await kernel.converge(objective_id)
        except OrchestrationError as error:
            raise fail(error) from error

    return router
