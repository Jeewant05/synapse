"""The three deterministic demo contributors used by the orchestration API."""

from server.app.config import Settings
from server.app.models import DemoAgent, IntentionContract, IntentionDocument

# Each demo contributor acts as one of the registered ANS agents, so the
# orchestration demo verifies against real identities under identity_mode=ans
# instead of inventing a principal that no registry knows about.
ANS_IDENTITY = {
    "demo-agent-1": "backend-agent",
    "demo-agent-2": "frontend-agent",
    "demo-agent-3": "telemetry-agent",
}


def ans_name_for_demo_agent(agent_id: str) -> str | None:
    """The registered ANSName this demo agent acts as, or None in mock mode."""
    from scripts.database import ans_name_for

    coordinator_id = ANS_IDENTITY.get(agent_id)
    domain = Settings().ans_domain
    if coordinator_id is None or not domain:
        return None
    return ans_name_for(coordinator_id, domain)


DEMO_AGENTS = [
    DemoAgent(ans_name=ans_name_for_demo_agent("demo-agent-1"), id="demo-agent-1", name="Demo Agent 1", role="OAuth API", allowed_paths=[
        "src/api/auth/oauth.ts", "src/api/auth/types.ts",
    ]),
    DemoAgent(ans_name=ans_name_for_demo_agent("demo-agent-2"), id="demo-agent-2", name="Demo Agent 2", role="Organization login", allowed_paths=[
        "src/components/login/OrganizationLogin.tsx",
        "src/components/login/OrganizationLogin.test.tsx",
    ]),
    DemoAgent(ans_name=ans_name_for_demo_agent("demo-agent-3"), id="demo-agent-3", name="Demo Agent 3", role="User data model", allowed_paths=[
        "src/models/user.ts", "src/models/user.test.ts",
    ]),
]


def default_intention(agent_id: str, objective_id: str, workstream_id: str) -> IntentionDocument:
    if agent_id == "demo-agent-1":
        return IntentionDocument(
            agent_id=agent_id, objective_id=objective_id, workstream_id=workstream_id,
            summary="Implement the OAuth callback and token exchange.",
            planned_files=["src/api/auth/oauth.ts", "src/api/auth/types.ts"],
            planned_symbols=["handleOAuth", "OAuthResponse"],
            contracts_provided=[IntentionContract(
                name="POST /api/oauth", request_fields=["authorizationCode"],
                response_fields=["token", "user"],
            )],
            dependencies=["workstream-agent-3"],
            assumptions=["The user model exposes oauthProviderId."], risk_level="medium",
        )
    if agent_id == "demo-agent-2":
        return IntentionDocument(
            agent_id=agent_id, objective_id=objective_id, workstream_id=workstream_id,
            summary="Add the organization login form and consume OAuth.",
            planned_files=["src/components/login/OrganizationLogin.tsx"],
            planned_symbols=["OrganizationLogin"],
            contracts_consumed=[IntentionContract(
                name="POST /api/oauth", response_fields=["accessToken", "profile"],
            )],
            assumptions=["The OAuth endpoint is available to the browser."], risk_level="medium",
        )
    if agent_id == "demo-agent-3":
        return IntentionDocument(
            agent_id=agent_id, objective_id=objective_id, workstream_id=workstream_id,
            summary="Add OAuth identity fields to the user model.",
            planned_files=["src/models/user.ts"], planned_symbols=["User"],
            database_changes=["Add oauthProviderId and organizationId to user model."],
            assumptions=["The migration system derives fields from the model."], risk_level="high",
        )
    raise KeyError(agent_id)
