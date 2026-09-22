import argparse
import json

from server.app.config import ROOT, Settings
from server.app.models import (
    AgentPrincipal,
    ApiContract,
    Decision,
    Objective,
    WorkspaceState,
    Workstream,
)
from server.app.store import read_state, write_state

# One ANS host per agent: an ANSName is host + version, so two agents cannot
# share a host. `coordinator` sits on the apex.
# agent id -> subdomain label. "" means the zone apex. Must match AGENTS in
# scripts/ans_register.py, or the seeded ANSName will not be the registered one.
AGENT_HOSTS = {
    "backend-agent": "backend",
    "frontend-agent": "frontend",
    "telemetry-agent": "telemetry",
    "coordinator": "",
}
ANS_VERSION = "1.0.0"
ANS_MATERIAL_ROOT = ROOT / ".local" / "ans"


def ans_name_for(agent_id: str, domain: str | None) -> str:
    """The agent's ANSName.

    Prefers what `ans-cli` actually registered (recorded in .local/ans/<agent>/
    agent.json by scripts/ans_register.sh) so the fixture cannot drift from the
    registry. Falls back to the deterministic name for ANS_DOMAIN, and finally to
    a clearly-labelled placeholder when no domain is configured at all -- mock
    mode must keep working on a fresh clone with no credentials.
    """
    recorded = ANS_MATERIAL_ROOT / agent_id / "agent.json"
    if recorded.is_file():
        try:
            name = json.loads(recorded.read_text()).get("ansName")
            if name:
                return name
        except (OSError, ValueError):
            pass
    label = AGENT_HOSTS.get(agent_id, agent_id)
    if not domain:
        return f"{label or agent_id}.unregistered.invalid"
    host = f"{label}.{domain}" if label else domain
    return f"ans://v{ANS_VERSION}.{host}"


def demo_state(domain: str | None = None) -> WorkspaceState:
    return WorkspaceState(
        objective=Objective(
            id="oauth-objective",
            title="Add organization-level OAuth login",
            description="Coordinate three coding agents before they edit overlapping files.",
            acceptance_criteria=[
                "Verify all three participating coding agents.",
                "Detect overlapping file intent before implementation begins.",
                "Reassign clear ownership and review the independent ChangeSets together.",
            ],
        ),
        agents=[
            AgentPrincipal(
                id="backend-agent", ans_name=ans_name_for("backend-agent", domain), role="backend"
            ),
            AgentPrincipal(
                id="frontend-agent",
                ans_name=ans_name_for("frontend-agent", domain),
                role="frontend",
            ),
            AgentPrincipal(
                id="telemetry-agent",
                ans_name=ans_name_for("telemetry-agent", domain),
                role="telemetry",
            ),
        ],
        workstreams=[
            Workstream(
                id="backend",
                objective_id="oauth-objective",
                title="OAuth API",
                agent_id="backend-agent",
                owned_paths=["src/api/auth/oauth.ts", "src/auth/session.ts"],
                contract=ApiContract(
                    method="POST",
                    path="/api/oauth",
                    role="provides",
                    response_fields={"token": "string", "user": "object"},
                ),
            ),
            Workstream(
                id="frontend",
                objective_id="oauth-objective",
                title="Organization login",
                agent_id="frontend-agent",
                owned_paths=["src/components/login/OrganizationLogin.tsx", "src/auth/session.ts"],
                depends_on=["backend"],
                contract=ApiContract(
                    method="POST",
                    path="/api/oauth",
                    role="consumes",
                    response_fields={"token": "string", "user": "object"},
                ),
            ),
            Workstream(
                id="telemetry",
                objective_id="oauth-objective",
                title="Login telemetry",
                agent_id="telemetry-agent",
                owned_paths=["src/lib/analytics/authEvents.ts", "src/auth/session.ts"],
                depends_on=["backend"],
                contract=ApiContract(
                    method="POST",
                    path="/api/oauth",
                    role="consumes",
                    response_fields={"token": "string", "user": "object"},
                ),
            ),
        ],
        decisions=[
            Decision(
                decision_id="auth-response",
                title="Authentication response contract",
                content="POST /api/oauth must return token (string) and user (object).",
                affected_component="authentication",
                created_at="2026-09-19T00:00:00Z",
            ),
            Decision(
                decision_id="scope-boundaries",
                title="Workstream ownership",
                content="One agent owns each file. Shared session logic stays with backend; UI and telemetry consume its contract from separate files.",
                affected_component="coordination",
                created_at="2026-09-19T00:00:00Z",
            ),
            Decision(
                decision_id="review-required",
                title="Convergence requires review",
                content="Resolve declared contract conflicts before completing the objective.",
                affected_component="review",
                created_at="2026-09-19T00:00:00Z",
            ),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local demo fixtures only.")
    parser.add_argument("action", choices=["seed", "reset"])
    args = parser.parse_args()
    settings = Settings()
    path = settings.resolved_database_path
    if args.action == "seed" and read_state(path).objective:
        print("Local workspace already seeded; left unchanged.")
        return
    write_state(path, demo_state(settings.ans_domain))
    print(f"Local workspace {args.action} complete: {path}. No external data changed.")


if __name__ == "__main__":
    main()
