"""Source of truth for the shared demo contracts; no coordinator behavior yet."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiContract(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    role: Literal["provides", "consumes"]
    request_fields: dict[str, str] = Field(default_factory=dict)
    response_fields: dict[str, str] = Field(default_factory=dict)


class TestResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "not_run"]
    source: Literal["agent_reported", "synapse_executed"] = "agent_reported"


class ChangeSet(BaseModel):
    id: str
    workstream_id: str
    agent_id: str
    commit_sha: str | None = None
    files: list[str]
    contract: ApiContract
    tests: list[TestResult] = Field(default_factory=list)


class Objective(BaseModel):
    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    status: Literal["active", "complete"] = "active"


class Workstream(BaseModel):
    id: str
    objective_id: str
    title: str
    agent_id: str
    owned_paths: list[str]
    depends_on: list[str] = Field(default_factory=list)
    contract: ApiContract
    status: Literal["pending", "active", "blocked", "complete"] = "pending"
    latest_changeset: ChangeSet | None = None


class AgentPrincipal(BaseModel):
    id: str
    # Canonical ANSName: ans://v<major.minor.patch>.<agentHost>. In mock mode this is
    # a placeholder label; in ANS mode it must resolve. See server/app/ans/names.py.
    ans_name: str
    role: str
    verified: bool = False
    # Populated by ANS registration; absent in mock mode.
    ans_agent_id: str | None = None
    identity_cert_fingerprint: str | None = None
    ans_status: str | None = None


class Conflict(BaseModel):
    id: str
    type: Literal["file", "contract"]
    workstream_ids: list[str]
    explanation: str
    conflicting_field: str
    decision_id: str | None = None
    recommendation: str
    status: Literal["open", "resolved"] = "open"


class Decision(BaseModel):
    decision_id: str
    title: str
    content: str
    affected_component: str
    created_at: str


class Event(BaseModel):
    event_id: str
    objective_id: str
    workstream_id: str | None = None
    agent_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class TraceEvent(BaseModel):
    """Durable debugging record for coordinator and live-agent activity."""

    trace_id: str
    source: Literal["coordinator", "live_agent"]
    event_type: str
    timestamp: str
    run_id: str | None = None
    objective_id: str | None = None
    workstream_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    agent_id: str
    verified: bool
    source: Literal["ans", "mock"]
    evidence: str
    checked_at: str
    # ANS-6 verification tier actually performed, and the badge state it saw.
    tier: Literal["badge", "scitt", "none"] = "none"
    badge_status: str | None = None


class WriteReceipt(BaseModel):
    event_id: str
    source: Literal["databricks", "cache"]
    status: Literal["written", "pending", "failed"]


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    identity_mode: str
    memory_mode: str
    trace_mode: str = "cache"
    live_integrations: bool = False
    # ANS mode only: what the coordinator actually verifies, for the integrations panel.
    identity_tier: Literal["badge", "scitt", "none"] = "none"
    dpop_required: bool = False
    # True when /api/reset needs X-Demo-Token, so the UI prompts only then.
    reset_requires_token: bool = False


class WorkspaceState(BaseModel):
    phase: Literal["foundation"] = "foundation"
    objective: Objective | None = None
    workstreams: list[Workstream] = Field(default_factory=list)
    agents: list[AgentPrincipal] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


# The orchestration API keeps its state separate from the legacy guided-demo
# workspace above. This lets worker agents use the stricter plan-before-write
# protocol without changing the existing coordination demo contract.
class IntentionContract(BaseModel):
    name: str
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)


class IntentionDocument(BaseModel):
    agent_id: str
    objective_id: str
    workstream_id: str
    summary: str
    planned_files: list[str] = Field(default_factory=list)
    planned_symbols: list[str] = Field(default_factory=list)
    contracts_provided: list[IntentionContract] = Field(default_factory=list)
    contracts_consumed: list[IntentionContract] = Field(default_factory=list)
    database_changes: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"]
    version: int = 1


class DemoAgent(BaseModel):
    id: str
    name: str
    role: str
    allowed_paths: list[str]
    # Canonical ANSName, when this demo agent maps to a registered ANS identity.
    # None in mock mode; required for identity_mode=ans to verify it.
    ans_name: str | None = None


class OrchestrationWorkstream(BaseModel):
    id: str
    agent_id: str
    title: str
    allowed_paths: list[str]
    state: Literal[
        "PENDING", "PLANNING", "INTENT_SUBMITTED", "VALIDATING", "BLOCKED",
        "APPROVED", "EXECUTING", "CHANGESET_SUBMITTED", "CONVERGING", "COMPLETE", "FAILED",
    ] = "PENDING"
    intention: IntentionDocument | None = None
    changeset: "OrchestrationChangeSet | None" = None
    block_reason: str | None = None


class OrchestrationConflict(BaseModel):
    conflict_id: str
    objective_id: str
    type: Literal[
        "FILE_CONFLICT", "SYMBOL_CONFLICT", "CONTRACT_CONFLICT", "DEPENDENCY_CONFLICT",
        "PERMISSION_CONFLICT", "INTENT_DRIFT",
    ]
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    workstream_ids: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)
    related_decision_id: str | None = None
    status: Literal["OPEN", "RESOLUTION_PROPOSED", "AWAITING_APPROVAL", "RESOLVED", "REJECTED"] = "OPEN"
    recommended_action: str | None = None
    approved_by: str | None = None
    resolution: dict[str, Any] | None = None


class ResolutionProposal(BaseModel):
    recommended_action: Literal[
        "UPDATE_CONTRACT", "REASSIGN_FILE_OWNER", "ADD_DEPENDENCY", "SERIALIZE_WORKSTREAMS",
        "SPLIT_SHARED_WORK", "REQUEST_SCOPE_EXPANSION", "REQUEST_HUMAN_REVIEW",
    ]
    affected_workstreams: list[str]
    reasoning: str
    proposed_changes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    requires_human_approval: bool = False


class TestSummary(BaseModel):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)


class OrchestrationChangeSet(BaseModel):
    workstream_id: str
    agent_id: str
    intention_version: int
    branch: str
    commit_sha: str | None = None
    changed_files: list[str]
    changed_symbols: list[str] = Field(default_factory=list)
    contracts_provided: list[IntentionContract] = Field(default_factory=list)
    contracts_consumed: list[IntentionContract] = Field(default_factory=list)
    database_changes: list[str] = Field(default_factory=list)
    tests: TestSummary


class OrchestrationEvent(BaseModel):
    event_id: str
    event_type: str
    timestamp: str
    agent_id: str | None = None
    workstream_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class OrchestrationState(BaseModel):
    objective: Objective | None = None
    agents: list[DemoAgent] = Field(default_factory=list)
    workstreams: list[OrchestrationWorkstream] = Field(default_factory=list)
    conflicts: list[OrchestrationConflict] = Field(default_factory=list)
    events: list[OrchestrationEvent] = Field(default_factory=list)
