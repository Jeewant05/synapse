"""Stable interfaces. Local implementations do not establish real ANS identity."""

from datetime import UTC, datetime
from typing import Protocol

from server.app.models import AgentPrincipal, Decision, Event, VerificationResult, WriteReceipt


class IdentityAdapter(Protocol):
    async def verify(self, agent: AgentPrincipal) -> VerificationResult: ...


class MemoryAdapter(Protocol):
    async def search(self, query: str) -> list[Decision]: ...
    async def log(self, event: Event) -> WriteReceipt: ...


class MockIdentity:
    async def verify(self, agent: AgentPrincipal) -> VerificationResult:
        return VerificationResult(
            agent_id=agent.id,
            verified=agent.id in {
                "backend-agent", "frontend-agent", "telemetry-agent",
                "demo-agent-1", "demo-agent-2", "demo-agent-3",
            },
            source="mock",
            evidence="Local allowlist fixture only; no ANS operation performed.",
            checked_at=datetime.now(UTC).isoformat(),
        )


class CacheMemory:
    def __init__(self, decisions: list[Decision] | None = None):
        self.decisions = decisions or []
        self.events: dict[str, Event] = {}

    async def search(self, query: str) -> list[Decision]:
        words = query.lower().split()
        return [
            decision
            for decision in self.decisions
            if any(
                word in f"{decision.title} {decision.content} {decision.affected_component}".lower()
                for word in words
            )
        ]

    async def log(self, event: Event) -> WriteReceipt:
        self.events[event.event_id] = event
        return WriteReceipt(event_id=event.event_id, source="cache", status="pending")
