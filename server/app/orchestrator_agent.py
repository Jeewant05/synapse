"""LLM-backed proposal agent for the strict orchestration kernel."""

import json

from server.app.models import OrchestrationConflict, OrchestrationState, ResolutionProposal
from server.app.providers import Provider, ProviderError


class OrchestratorAgent:
    """Proposes coordination changes; it never changes kernel state itself."""

    id = "orchestrator-agent"

    def __init__(self, provider: Provider | None = None) -> None:
        self.provider = provider

    async def propose(self, state: OrchestrationState, conflict: OrchestrationConflict) -> ResolutionProposal:
        fallback = self._fallback(conflict)
        if self.provider is None:
            return fallback
        prompt = json.dumps({
            "task": "Propose a safe coordination resolution. Return JSON only matching ResolutionProposal.",
            "objective": state.objective.model_dump() if state.objective else {},
            "workstreams": [workstream.model_dump() for workstream in state.workstreams],
            "conflict": conflict.model_dump(),
            "constraints": [
                "Do not propose code changes.",
                "Only name affected workstreams from the conflict.",
                "Prefer isolated ownership and explicit dependencies.",
                "The deterministic kernel will validate and approve this proposal.",
            ],
        })
        try:
            proposal = ResolutionProposal.model_validate(json.loads(await self.provider.generate(prompt)))
        except (ProviderError, ValueError, TypeError, json.JSONDecodeError):
            return fallback
        if not set(proposal.affected_workstreams).issubset(conflict.workstream_ids):
            return fallback
        return proposal

    @staticmethod
    def _fallback(conflict: OrchestrationConflict) -> ResolutionProposal:
        if conflict.type == "CONTRACT_CONFLICT":
            return ResolutionProposal(
                recommended_action="UPDATE_CONTRACT", affected_workstreams=[conflict.workstream_ids[-1]],
                reasoning="The provider's contract is authoritative; align the consumer before execution.",
                proposed_changes={"contract": conflict.evidence.get("contract")}, confidence=0.82,
            )
        if conflict.type in {"FILE_CONFLICT", "SYMBOL_CONFLICT"}:
            return ResolutionProposal(
                recommended_action="REASSIGN_FILE_OWNER", affected_workstreams=conflict.workstream_ids,
                reasoning="Assign one owner to shared implementation and isolate dependent work.",
                proposed_changes={"evidence": conflict.evidence}, confidence=0.8,
            )
        return ResolutionProposal(
            recommended_action="REQUEST_HUMAN_REVIEW", affected_workstreams=conflict.workstream_ids,
            reasoning="This conflict requires a validated scope or dependency decision.",
            proposed_changes={"evidence": conflict.evidence}, confidence=0.5, requires_human_approval=True,
        )
