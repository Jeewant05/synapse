"""Production web surface: the built dashboard and per-agent ANS agent cards.

In development Vite serves the UI and proxies /api. In production there is no
proxy, so FastAPI serves both from one origin -- which is also what ANS wants,
since one hostname is one agent endpoint with one certificate.

Agent cards are routed by Host header. Each registered agent subdomain resolves
to this same deployment, and each must serve its own card at the metadata URL its
ANS registration declares; otherwise the registration describes an endpoint that
answers with someone else's identity.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.app.config import Settings

log = logging.getLogger("synapse.web")

# Host label -> the agent it identifies. The apex is the coordinator.
AGENT_BY_LABEL = {
    "backend": ("backend-agent", "Synapse Backend Agent", "Owns the OAuth API and session contract."),
    "frontend": ("frontend-agent", "Synapse Frontend Agent", "Owns the organization login UI."),
    "telemetry": ("telemetry-agent", "Synapse Telemetry Agent", "Owns login telemetry."),
}
COORDINATOR = ("coordinator", "Synapse Coordinator", "Pre-merge coordination between coding agents.")


def _agent_for_host(host: str, domain: str | None):
    """Resolve a request Host to the agent identity it represents."""
    hostname = (host or "").split(":")[0].strip().lower().rstrip(".")
    if domain and hostname != domain.lower():
        label = hostname.removesuffix(f".{domain.lower()}")
        if label in AGENT_BY_LABEL:
            return AGENT_BY_LABEL[label], hostname
    return COORDINATOR, hostname


def build_web_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["web"])

    @router.get("/api")
    def service_descriptor(request: Request) -> JSONResponse:
        """Self-description at the endpoint URL ANS registration seals.

        Registration declares https://<host>/api as the agent endpoint. Sealing a
        URL that 404s would publish a claim the service does not honour, so this
        answers there and points at the agent card for the rest.
        """
        from scripts.database import ANS_VERSION

        (_agent_id, name, _description), hostname = _agent_for_host(
            request.headers.get("host", ""), settings.ans_domain
        )
        return JSONResponse({
            "service": "Synapse coordinator",
            "agent": name,
            "ansName": f"ans://v{ANS_VERSION}.{hostname}" if hostname else None,
            "protocol": "HTTP-API",
            "agentCard": f"https://{hostname}/.well-known/agent-card.json",
            "operations": {
                "join": "POST /api/agents/{agent_id}/join",
                "claim": "POST /api/workstreams/{workstream_id}/claim",
                "declare": "POST /api/workstreams/{workstream_id}/declare",
                "submit": "POST /api/workstreams/{workstream_id}/submit",
                "state": "GET /api/state",
            },
            "authentication": "ANS-6 Method B (DPoP) on every privileged call",
        })

    @router.get("/.well-known/agent-card.json")
    def agent_card(request: Request) -> JSONResponse:
        from scripts.database import ANS_VERSION

        (_agent_id, name, description), hostname = _agent_for_host(
            request.headers.get("host", ""), settings.ans_domain
        )
        # Derived from the host actually requested, not from a lookup table: a
        # card served at host X can then only claim ans://vN.X. The registration
        # for that host is the same name, so the two cannot drift.
        ans_name = f"ans://v{ANS_VERSION}.{hostname}" if hostname else None
        return JSONResponse({
            "name": name,
            "description": description,
            "ansName": ans_name,
            "host": hostname,
            "protocol": "HTTP-API",
            "url": f"https://{hostname}/api",
            "capabilities": ["declare-contract", "submit-changeset"],
            # Stated rather than implied: the badge is the identity anchor here.
            "identity": {
                "scheme": "ans",
                "verification": "badge-tier",
                "possession": "ANS-6 Method B (DPoP)",
            },
        })

    return router


def mount_ui(app: FastAPI, dist: Path | None = None) -> bool:
    """Serve the built dashboard at / -- registered last so /api still wins."""
    target = dist or Path(__file__).resolve().parents[2] / "ui" / "dist"
    if not (target / "index.html").is_file():
        log.info("no built UI at %s; API-only (run `npm run build --workspace ui`)", target)
        return False
    app.mount("/", StaticFiles(directory=target, html=True), name="ui")
    return True
