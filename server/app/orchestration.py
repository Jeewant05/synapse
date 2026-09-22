"""Deterministic plan-before-write coordination for the three demo agents."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from agents.demo_agents import DEMO_AGENTS, default_intention
from server.app.adapters import IdentityAdapter
from server.app.coordinator import paths_overlap
from server.app.models import (
    AgentPrincipal,
    IntentionContract,
    IntentionDocument,
    Objective,
    OrchestrationChangeSet,
    OrchestrationConflict,
    OrchestrationEvent,
    OrchestrationState,
    OrchestrationWorkstream,
    ResolutionProposal,
    TraceEvent,
)
from server.app.orchestrator_agent import OrchestratorAgent
from server.app.store import read_orchestration_state, write_orchestration_state
from server.app.tracing import TraceSink


def _now() -> str:
    return datetime.now(UTC).isoformat()


class OrchestrationError(Exception):
    pass


class OrchestrationKernel:
    """The sole authority for workstream states, approvals, and ChangeSet acceptance."""

    def __init__(self, db_path: Path, identity: IdentityAdapter, trace: TraceSink):
        self.db_path, self.identity, self.trace = db_path, identity, trace

    async def _event(self, state: OrchestrationState, event_type: str, *, agent_id: str | None = None,
                     workstream_id: str | None = None, payload: dict | None = None) -> None:
        event = OrchestrationEvent(
            event_id=f"orch-{uuid.uuid4().hex[:10]}", event_type=event_type, timestamp=_now(),
            agent_id=agent_id, workstream_id=workstream_id, payload=payload or {},
        )
        state.events.append(event)
        await self.trace.log(TraceEvent(
            trace_id=event.event_id, source="coordinator", event_type=event.event_type,
            timestamp=event.timestamp, objective_id=state.objective.id if state.objective else None,
            workstream_id=workstream_id, agent_id=agent_id, payload=event.payload,
        ))

    @staticmethod
    def _workstream(state: OrchestrationState, workstream_id: str) -> OrchestrationWorkstream:
        for workstream in state.workstreams:
            if workstream.id == workstream_id:
                return workstream
        raise OrchestrationError(f"unknown workstream {workstream_id}")

    @staticmethod
    def _agent(state: OrchestrationState, agent_id: str):
        for agent in state.agents:
            if agent.id == agent_id:
                return agent
        raise OrchestrationError(f"unregistered agent {agent_id}")

    @staticmethod
    def _bind(agent, proven_ans_name: str | None) -> None:
        """Tie the proven ANS identity to the agent_id the caller claims.

        `proven_ans_name` comes from the identity certificate the caller proved
        possession of, so this is what stops one registered agent acting as
        another. In mock mode it is None and this is a no-op.
        """
        if proven_ans_name is None:
            return
        if not agent.ans_name:
            raise OrchestrationError(f"{agent.id} has no registered ANSName to match")
        if agent.ans_name.strip().lower() != proven_ans_name.strip().lower():
            raise OrchestrationError(
                f"caller proved {proven_ans_name} but claimed {agent.id}, "
                f"which is registered as {agent.ans_name}"
            )

    def state(self, objective_id: str) -> OrchestrationState:
        state = read_orchestration_state(self.db_path)
        if state.objective is None or state.objective.id != objective_id:
            raise OrchestrationError(f"unknown objective {objective_id}")
        return state

    async def create_objective(self, objective: Objective) -> OrchestrationState:
        state = OrchestrationState(
            objective=objective, agents=DEMO_AGENTS,
            workstreams=[
                OrchestrationWorkstream(id="workstream-agent-1", agent_id="demo-agent-1", title="OAuth API", allowed_paths=DEMO_AGENTS[0].allowed_paths),
                OrchestrationWorkstream(id="workstream-agent-2", agent_id="demo-agent-2", title="Organization login", allowed_paths=DEMO_AGENTS[1].allowed_paths),
                OrchestrationWorkstream(id="workstream-agent-3", agent_id="demo-agent-3", title="User data model", allowed_paths=DEMO_AGENTS[2].allowed_paths),
            ],
        )
        await self._event(state, "objective_created", payload={"objective_id": objective.id})
        write_orchestration_state(self.db_path, state)
        return state

    async def plan(self, agent_id: str, intention: IntentionDocument | None = None,
                   *, proven_ans_name: str | None = None) -> OrchestrationState:
        state = read_orchestration_state(self.db_path)
        if state.objective is None:
            raise OrchestrationError("create an objective before planning")
        agent = self._agent(state, agent_id)
        self._bind(agent, proven_ans_name)
        workstream = next((w for w in state.workstreams if w.agent_id == agent_id), None)
        if workstream is None:
            raise OrchestrationError(f"no workstream assigned to {agent_id}")
        workstream.state = "PLANNING"
        # Verify the agent's registered identity. `agent_id` is not an ANSName, so
        # passing it as one made every check fail under identity_mode=ans; the
        # registered name comes from the agent record instead.
        verification = await self.identity.verify(
            AgentPrincipal(id=agent_id, ans_name=agent.ans_name or agent_id, role=agent.role)
        )
        if not verification.verified:
            workstream.state, workstream.block_reason = "BLOCKED", verification.evidence
            await self._event(state, "identity_rejected", agent_id=agent_id, workstream_id=workstream.id)
            write_orchestration_state(self.db_path, state)
            return state
        await self._event(state, "identity_verified", agent_id=agent_id, workstream_id=workstream.id,
                          payload={"source": verification.source, "evidence": verification.evidence})
        intention = intention or default_intention(agent_id, state.objective.id, workstream.id)
        if intention.agent_id != agent_id or intention.objective_id != state.objective.id or intention.workstream_id != workstream.id:
            raise OrchestrationError("intention identity or objective does not match the assigned workstream")
        workstream.intention = intention
        workstream.state = "INTENT_SUBMITTED"
        await self._event(state, "intention_submitted", agent_id=agent_id, workstream_id=workstream.id,
                          payload={"intention_version": intention.version, "planned_files": intention.planned_files})
        await self._validate_and_detect(state)
        write_orchestration_state(self.db_path, state)
        return state

    async def _validate_and_detect(self, state: OrchestrationState) -> None:
        invalid_workstreams: set[str] = set()
        for workstream in state.workstreams:
            if workstream.intention is None or workstream.state == "COMPLETE":
                continue
            workstream.state, workstream.block_reason = "VALIDATING", None
            intention = workstream.intention
            invalid = []
            if not intention.planned_files and not intention.planned_symbols:
                invalid.append("an intention needs planned_files or planned_symbols")
            if not all(any(paths_overlap(path, allowed) for allowed in workstream.allowed_paths) for path in intention.planned_files):
                invalid.append("planned files exceed the assigned scope")
            known_ids = {w.id for w in state.workstreams}
            if not set(intention.dependencies).issubset(known_ids - {workstream.id}):
                invalid.append("dependencies must name other existing workstreams")
            if invalid:
                workstream.state, workstream.block_reason = "BLOCKED", "; ".join(invalid)
                invalid_workstreams.add(workstream.id)
                await self._event(state, "intention_rejected", agent_id=workstream.agent_id,
                                  workstream_id=workstream.id, payload={"errors": invalid})
        conflicts = self._detect_conflicts(state)
        state.conflicts = conflicts
        blocked = {
            wid
            for conflict in conflicts if conflict.status == "OPEN"
            for wid in (conflict.workstream_ids[:1] if conflict.type == "DEPENDENCY_CONFLICT" else conflict.workstream_ids)
        }
        for workstream in state.workstreams:
            if workstream.intention is None or workstream.state == "COMPLETE" or workstream.id in invalid_workstreams:
                continue
            if workstream.id in blocked:
                workstream.state, workstream.block_reason = "BLOCKED", "unresolved coordination conflict"
            else:
                workstream.state, workstream.block_reason = "APPROVED", None
                await self._event(state, "scope_approved", agent_id=workstream.agent_id,
                                  workstream_id=workstream.id,
                                  payload={"paths": workstream.intention.planned_files})
        for conflict in conflicts:
            await self._event(state, "conflict_detected", payload={
                "conflict_id": conflict.conflict_id, "type": conflict.type,
                "workstreams": conflict.workstream_ids, "evidence": conflict.evidence,
            })

    def _detect_conflicts(self, state: OrchestrationState) -> list[OrchestrationConflict]:
        active = [w for w in state.workstreams if w.intention and w.state != "FAILED"]
        conflicts: list[OrchestrationConflict] = []
        for workstream in active:
            escaped_paths = [
                path for path in workstream.intention.planned_files
                if not any(paths_overlap(path, allowed) for allowed in workstream.allowed_paths)
            ]
            if escaped_paths:
                conflicts.append(self._conflict(
                    state, "PERMISSION_CONFLICT", "HIGH", [workstream.id],
                    {"unauthorized_paths": escaped_paths},
                ))
        for index, left in enumerate(active):
            for right in active[index + 1:]:
                files = [path for path in left.intention.planned_files for other in right.intention.planned_files if paths_overlap(path, other)]
                if files:
                    conflicts.append(self._conflict(state, "FILE_CONFLICT", "MEDIUM", [left.id, right.id], {"paths": files}))
                symbols = sorted(set(left.intention.planned_symbols) & set(right.intention.planned_symbols))
                if symbols:
                    conflicts.append(self._conflict(state, "SYMBOL_CONFLICT", "MEDIUM", [left.id, right.id], {"symbols": symbols}))
                for provider in left.intention.contracts_provided + right.intention.contracts_provided:
                    consumer_intention = right.intention if provider in left.intention.contracts_provided else left.intention
                    for consumer in consumer_intention.contracts_consumed:
                        if provider.name == consumer.name and not set(consumer.response_fields).issubset(provider.response_fields):
                            conflicts.append(self._conflict(state, "CONTRACT_CONFLICT", "MEDIUM", [left.id, right.id], {
                                "contract": provider.name, "provider_fields": provider.response_fields,
                                "consumer_fields": consumer.response_fields,
                            }, "auth-response"))
        graph = {w.id: w.intention.dependencies for w in active}
        for workstream in active:
            visiting: set[str] = set()

            def has_cycle(node: str, seen: set[str] = visiting) -> bool:
                if node in seen:
                    return True
                seen.add(node)
                cycle = any(has_cycle(dependency) for dependency in graph.get(node, []) if dependency in graph)
                seen.remove(node)
                return cycle

            if has_cycle(workstream.id):
                conflicts.append(self._conflict(state, "DEPENDENCY_CONFLICT", "HIGH", [workstream.id], {
                    "cycle_at": workstream.id,
                }))
        for workstream in active:
            unmet = [dep for dep in graph[workstream.id] if self._workstream(state, dep).state != "COMPLETE"]
            if unmet:
                conflicts.append(self._conflict(state, "DEPENDENCY_CONFLICT", "LOW", [workstream.id, *unmet], {"unmet_dependencies": unmet}))
        return conflicts

    @staticmethod
    def _conflict(state: OrchestrationState, type: str, severity: str, workstreams: list[str], evidence: dict,
                  decision: str | None = None) -> OrchestrationConflict:
        signature = "-".join(sorted(workstreams)) + "-" + type + "-" + str(sorted(evidence.items()))
        return OrchestrationConflict(conflict_id=f"conflict-{uuid.uuid5(uuid.NAMESPACE_URL, signature).hex[:10]}",
            objective_id=state.objective.id, type=type, severity=severity, workstream_ids=workstreams,
            evidence=evidence, related_decision_id=decision)

    async def propose_resolution(self, conflict_id: str) -> ResolutionProposal:
        state = read_orchestration_state(self.db_path)
        conflict = next((c for c in state.conflicts if c.conflict_id == conflict_id), None)
        if conflict is None:
            raise OrchestrationError("open conflict not found")
        if conflict.status == "RESOLUTION_PROPOSED" and conflict.resolution:
            return ResolutionProposal.model_validate(conflict.resolution)
        if conflict.status != "OPEN":
            raise OrchestrationError("open conflict not found")
        if conflict.type != "CONTRACT_CONFLICT":
            raise OrchestrationError("this demo resolver only proposes deterministic contract updates")
        proposal = ResolutionProposal(recommended_action="UPDATE_CONTRACT", affected_workstreams=[conflict.workstream_ids[1]],
            reasoning="The provider owns the approved OAuth interface; the attached project decision requires token and user.",
            proposed_changes={"contracts_consumed": [{"name": conflict.evidence["contract"], "response_fields": conflict.evidence["provider_fields"]}]},
            confidence=1.0, requires_human_approval=False)
        conflict.status, conflict.recommended_action = "RESOLUTION_PROPOSED", proposal.recommended_action
        write_orchestration_state(self.db_path, state)
        return proposal

    async def orchestrate(self, objective_id: str, agent: OrchestratorAgent) -> OrchestrationState:
        """Ask the proposal agent for resolutions and retain its rationale as trace evidence."""
        state = self.state(objective_id)
        open_conflicts = [conflict for conflict in state.conflicts if conflict.status == "OPEN"]
        await self._event(state, "orchestrator_started", agent_id=agent.id,
                          payload={"open_conflicts": len(open_conflicts)})
        for conflict in open_conflicts:
            proposal = await agent.propose(state, conflict)
            conflict.status = "RESOLUTION_PROPOSED"
            conflict.recommended_action = proposal.recommended_action
            conflict.resolution = proposal.model_dump()
            await self._event(state, "orchestrator_proposal", agent_id=agent.id,
                              payload={"conflict_id": conflict.conflict_id, "proposal": proposal.model_dump()})
        write_orchestration_state(self.db_path, state)
        return state

    async def approve_resolution(self, conflict_id: str, approved_by: str) -> OrchestrationState:
        state = read_orchestration_state(self.db_path)
        conflict = next((c for c in state.conflicts if c.conflict_id == conflict_id), None)
        if conflict is None or conflict.type != "CONTRACT_CONFLICT":
            raise OrchestrationError("contract conflict not found")
        consumer_id = conflict.workstream_ids[1]
        consumer = self._workstream(state, consumer_id)
        if consumer.intention is None:
            raise OrchestrationError("consumer intention is missing")
        provider_fields = conflict.evidence["provider_fields"]
        consumer.intention.contracts_consumed = [IntentionContract(name=conflict.evidence["contract"], response_fields=provider_fields)]
        consumer.intention.version += 1
        conflict.status, conflict.approved_by = "RESOLVED", approved_by
        await self._event(state, "resolution_accepted", agent_id=consumer.agent_id, workstream_id=consumer.id,
                          payload={"conflict_id": conflict_id, "approved_by": approved_by})
        await self._validate_and_detect(state)
        write_orchestration_state(self.db_path, state)
        return state

    async def execute(self, agent_id: str, *, proven_ans_name: str | None = None) -> OrchestrationState:
        state = read_orchestration_state(self.db_path)
        self._bind(self._agent(state, agent_id), proven_ans_name)
        workstream = next((w for w in state.workstreams if w.agent_id == agent_id), None)
        if workstream is None or workstream.state != "APPROVED":
            raise OrchestrationError("agent has no approved scope lease")
        workstream.state = "EXECUTING"
        await self._event(state, "execution_started", agent_id=agent_id, workstream_id=workstream.id,
                          payload={"allowed_paths": workstream.intention.planned_files})
        write_orchestration_state(self.db_path, state)
        return state

    async def submit_changeset(self, changeset: OrchestrationChangeSet) -> OrchestrationState:
        state = read_orchestration_state(self.db_path)
        workstream = self._workstream(state, changeset.workstream_id)
        intention = workstream.intention
        if workstream.agent_id != changeset.agent_id or intention is None or workstream.state != "EXECUTING":
            raise OrchestrationError("changeset is not from an executing assigned agent")
        drift = (
            changeset.intention_version != intention.version
            or not set(changeset.changed_files).issubset(intention.planned_files)
            or not set(changeset.changed_symbols).issubset(intention.planned_symbols)
            or changeset.database_changes != intention.database_changes
            or changeset.contracts_provided != intention.contracts_provided
            or changeset.contracts_consumed != intention.contracts_consumed
            or changeset.tests.failed > 0
        )
        if drift:
            state.conflicts.append(self._conflict(state, "INTENT_DRIFT", "HIGH", [workstream.id], {
                "intention_version": intention.version, "submitted_version": changeset.intention_version,
                "changed_files": changeset.changed_files, "changed_symbols": changeset.changed_symbols,
                "failed_tests": changeset.tests.failed,
            }))
            workstream.state, workstream.block_reason = "FAILED", "changeset differs from the approved intention"
            await self._event(state, "changeset_rejected", agent_id=changeset.agent_id, workstream_id=workstream.id)
        else:
            workstream.changeset, workstream.state = changeset, "COMPLETE"
            await self._event(state, "changeset_submitted", agent_id=changeset.agent_id, workstream_id=workstream.id,
                              payload={"changed_files": changeset.changed_files, "tests": changeset.tests.model_dump()})
            await self._validate_and_detect(state)
        write_orchestration_state(self.db_path, state)
        return state

    async def converge(self, objective_id: str) -> OrchestrationState:
        state = self.state(objective_id)
        if any(c.status == "OPEN" for c in state.conflicts) or any(w.state != "COMPLETE" for w in state.workstreams):
            raise OrchestrationError("cannot converge until every conflict resolves and every workstream completes")
        state.objective.status = "complete"
        await self._event(state, "convergence_completed", payload={"objective_id": objective_id})
        write_orchestration_state(self.db_path, state)
        return state
