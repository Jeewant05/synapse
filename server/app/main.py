import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.app.acme import build_acme_router, parse_challenges
from server.app.adapters import CacheMemory, IdentityAdapter, MemoryAdapter, MockIdentity
from server.app.config import Settings
from server.app.demo_runner import (
    build_demo_router,
    require_demo_token_for_public,
)
from server.app.live_agents import LiveRuns
from server.app.live_routes import build_live_router
from server.app.models import Health, WorkspaceState
from server.app.orchestration import OrchestrationKernel
from server.app.orchestration_routes import build_orchestration_router
from server.app.orchestrator_agent import OrchestratorAgent
from server.app.providers import ProviderError, build_agent_providers, build_provider
from server.app.routes import build_router
from server.app.service import Coordinator
from server.app.store import read_state, write_state
from server.app.trace_routes import build_trace_router
from server.app.tracing import build_trace_sink

log = logging.getLogger("synapse")
from server.app.web import build_web_router, mount_ui


def _fresh_state_for(settings: Settings):
    """Reset restores the fixture with the same ANSNames the seed script used."""

    def build() -> WorkspaceState:
        from scripts.database import demo_state

        return demo_state(settings.ans_domain)

    return build


def _seed_if_empty(settings: Settings) -> None:
    """Give a blank database the demo fixture before the first request.

    A fresh deployment has no volume contents and nobody to run `npm run seed`,
    so without this the dashboard's first paint is an empty workspace with no
    objective and no way to start one.
    """
    path = settings.resolved_database_path
    if read_state(path).objective is not None:
        return
    from scripts.database import demo_state

    log.info("empty workspace at %s; seeding the demo fixture", path)
    write_state(path, demo_state(settings.ans_domain))


def build_identity(settings: Settings) -> IdentityAdapter:
    # ans mode fails fast on missing credentials rather than silently falling back
    # to the fixture, which would misreport the demo as verified.
    if settings.identity_mode == "ans":
        from server.app.ans.identity import build_ans_identity

        return build_ans_identity(settings)
    return MockIdentity()


def build_memory(settings: Settings) -> MemoryAdapter:
    seeded = read_state(settings.resolved_database_path).decisions
    if settings.memory_mode == "databricks":
        # No Databricks memory implementation exists. Warn rather than crash, and
        # never let the dashboard report decisions as anything but the fixture
        # they are.
        log.warning(
            "MEMORY_MODE=databricks has no implementation; decision memory stays "
            "the local fixture"
        )
    return CacheMemory(seeded)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    require_demo_token_for_public(settings)
    _seed_if_empty(settings)
    app = FastAPI(title="Synapse API", version="0.6.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins.split(","),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    trace = build_trace_sink(settings)
    identity = build_identity(settings)
    # Only the ANS adapter can verify a DPoP proof, so the gate is wired only then.
    ans_identity = identity if settings.identity_mode == "ans" else None
    coordinator = Coordinator(
        settings.resolved_database_path, identity, build_memory(settings), trace
    )

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health(
            identity_mode=settings.identity_mode,
            memory_mode=settings.memory_mode,
            trace_mode=trace.source,
            live_integrations=settings.live_integrations,
            identity_tier="badge" if ans_identity else "none",
            dpop_required=bool(ans_identity and settings.ans_dpop_required),
            reset_requires_token=bool(settings.demo_token),
        )

    @app.get("/api/state", response_model=WorkspaceState)
    def state() -> WorkspaceState:
        return read_state(settings.resolved_database_path)

    app.include_router(
        build_router(
            coordinator,
            _fresh_state_for(settings),
            ans_identity=ans_identity,
            dpop_required=settings.ans_dpop_required,
            demo_token=settings.demo_token,
        )
    )
    app.include_router(build_trace_router(trace))
    configured = build_agent_providers(settings)
    try:
        orchestrator_provider = build_provider(settings.orchestrator_provider, settings, "orchestrator") \
            if settings.orchestrator_provider != "none" else None
    except ProviderError:  # Provider availability is surfaced by orchestrator trace fallback.
        orchestrator_provider = None
    app.include_router(
        build_orchestration_router(
            OrchestrationKernel(settings.resolved_database_path, identity, trace),
            OrchestratorAgent(orchestrator_provider),
            ans_identity=ans_identity,
            dpop_required=settings.ans_dpop_required,
        )
    )
    providers = {
        role: configured[source]
        for role, source in {
            "backend": "backend",
            "frontend": "frontend",
            "integration": "qa",
        }.items()
        if source in configured
    }
    runs = LiveRuns(providers, settings.resolved_database_path.parent / "live-runs", trace)
    # Open by design: a run costs provider credit but loses no data, and the
    # dashboard must work without a prompt. Only /api/reset is behind DEMO_TOKEN.
    # /live/config reports which providers are configured either way.
    app.include_router(build_live_router(runs))

    app.include_router(
        build_demo_router(settings, local_base=settings.local_base_url)
    )
    # ANS domain validation answers here instead of via DNS TXT records.
    challenges = parse_challenges(settings.acme_challenges)
    if challenges:
        log.info("serving %d ACME challenge(s)", len(challenges))
    app.include_router(build_acme_router(challenges))
    app.include_router(build_web_router(settings))
    # Mounted last so every /api route still matches first.
    mount_ui(app)
    return app


app = create_app()
