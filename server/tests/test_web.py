"""The production web surface: agent cards and static serving.

The card's ANSName must equal the registered name for the host it is served on.
A mismatch publishes a claim the registry does not back, which is the failure
mode an external verifier looks for -- so it is worth a test rather than a
convention.
"""

import json

from fastapi.testclient import TestClient

from scripts.database import ANS_VERSION, ans_name_for
from server.app.config import Settings
from server.app.main import create_app

DOMAIN = "synapse-vt.us"

# agent id -> the host it is registered on.
HOSTS = {
    "backend-agent": f"backend.{DOMAIN}",
    "frontend-agent": f"frontend.{DOMAIN}",
    "telemetry-agent": f"telemetry.{DOMAIN}",
    "coordinator": DOMAIN,  # the apex, not coordinator.<domain>
}


# Settings() reads the developer's .env. With live providers and keys configured
# there, POST /api/live/runs would start a real run against the provider: slow,
# billable, and different on every machine. These tests only care about routing
# and auth, so every provider is pinned off.
NO_PROVIDERS = dict(
    backend_provider="none", frontend_provider="none", qa_provider="none",
    orchestrator_provider="none",
)


def client(tmp_path):
    return TestClient(create_app(Settings(demo_token=None, database_path=tmp_path / "web.db", ans_domain=DOMAIN, **NO_PROVIDERS)))


def test_agent_card_ansname_matches_the_host_it_is_served_on(tmp_path):
    c = client(tmp_path)
    for host in HOSTS.values():
        card = c.get("/.well-known/agent-card.json", headers={"Host": host}).json()
        assert card["ansName"] == f"ans://v{ANS_VERSION}.{host}", host
        assert card["host"] == host


def test_agent_card_matches_what_registration_would_seal(tmp_path):
    """The card and scripts/ans_register.py must agree on every host."""
    c = client(tmp_path)
    for agent_id, host in HOSTS.items():
        card = c.get("/.well-known/agent-card.json", headers={"Host": host}).json()
        assert card["ansName"] == ans_name_for(agent_id, DOMAIN), agent_id


def test_each_subdomain_serves_its_own_identity(tmp_path):
    c = client(tmp_path)
    names = {
        h: c.get("/.well-known/agent-card.json", headers={"Host": h}).json()["name"]
        for h in HOSTS.values()
    }
    assert len(set(names.values())) == len(names), names


def test_api_still_wins_over_the_static_mount(tmp_path):
    assert client(tmp_path).get("/api/health").status_code == 200


def test_every_api_route_lives_under_api(tmp_path):
    """No server route may sit outside /api.

    The dashboard calls /api/* and the dev proxy no longer rewrites the prefix,
    so a router mounted anywhere else 404s in the browser while every server-side
    test still passes. That is exactly how /live and /traces shipped broken.

    Read from the OpenAPI schema, not app.routes: included routers appear there
    as opaque objects with no .path, so walking app.routes silently inspects
    almost nothing and the check passes no matter what is mounted.
    """
    app = create_app(Settings(demo_token=None, database_path=tmp_path / "routes.db", ans_domain=DOMAIN))
    # /.well-known/* is fixed by external specs -- the ANS agent card and the
    # ACME HTTP-01 challenge path -- so it cannot live under /api.
    stray = [
        path
        for path in app.openapi()["paths"]
        if not path.startswith("/api") and not path.startswith("/.well-known/")
    ]
    assert not stray, f"routes outside /api will 404 from the dashboard: {stray}"


def test_the_paths_the_dashboard_actually_calls_exist(tmp_path):
    """Pin the exact paths ui/src calls, so a prefix change cannot silently break them."""
    c = client(tmp_path)
    for path in ["/api/health", "/api/state", "/api/traces?limit=12", "/api/live/config"]:
        assert c.get(path).status_code != 404, path


def test_the_declared_ans_endpoint_answers(tmp_path):
    """Registration seals https://<host>/api, so that URL must not 404."""
    c = client(tmp_path)
    for host in HOSTS.values():
        r = c.get("/api", headers={"Host": host})
        assert r.status_code == 200, host
        assert r.json()["ansName"] == f"ans://v{ANS_VERSION}.{host}"


# --- ACME HTTP-01 responder -------------------------------------------------


def test_acme_challenge_is_served_when_configured(tmp_path):
    """The token is base64 and contains '/', so it spans several URL segments."""
    token = "wl6tGSamNFoaXjw/KtXtFFtesYnCVkWHFr5es9kA4as="
    key_auth = f"{token}.thumbprint"
    app = create_app(Settings(
        demo_token=None, database_path=tmp_path / "acme.db", ans_domain=DOMAIN,
        acme_challenges=json.dumps({token: key_auth}),
    ))
    r = TestClient(app).get(f"/.well-known/acme-challenge/{token}")
    assert r.status_code == 200
    assert r.text == key_auth


def test_unknown_acme_token_is_not_found(tmp_path):
    app = create_app(Settings(
        demo_token=None, database_path=tmp_path / "acme.db", ans_domain=DOMAIN,
        acme_challenges='{"real": "real.thumb"}',
    ))
    assert TestClient(app).get("/.well-known/acme-challenge/made-up").status_code == 404


def test_malformed_acme_config_does_not_break_startup(tmp_path):
    """A bad secret must not take the whole site down."""
    for raw in ["not json", "[]", "", None]:
        app = create_app(Settings(
            demo_token=None, database_path=tmp_path / "acme.db", ans_domain=DOMAIN,
            acme_challenges=raw,
        ))
        assert TestClient(app).get("/api/health").status_code == 200


# --- DEMO_TOKEN guards /api/reset and nothing else ---------------------------


def guarded(tmp_path):
    return TestClient(create_app(Settings(
        demo_token="s3cret", database_path=tmp_path / "guard.db", ans_domain=DOMAIN, **NO_PROVIDERS,
    )))


def test_reset_requires_the_token_when_one_is_configured(tmp_path):
    """Reset destroys state, so it is the one endpoint a stranger must not reach."""
    c = guarded(tmp_path)
    assert c.post("/api/reset").status_code == 401
    assert c.post("/api/reset", headers={"X-Demo-Token": "wrong"}).status_code == 401
    assert c.post("/api/reset", headers={"X-Demo-Token": "s3cret"}).status_code == 200


def test_live_runs_and_the_demo_runner_are_open(tmp_path):
    """The dashboard presses these with no prompt, so they must not need the token.

    Neither may answer 401. The status they do return without providers or
    identity material configured is beside the point here.
    """
    c = guarded(tmp_path)
    assert c.post("/api/live/runs", json={"objective": "Build tasks"}).status_code != 401
    assert c.post("/api/demo/run").status_code != 401
    assert c.get("/api/live/config").status_code == 200


def test_health_tells_the_ui_whether_reset_needs_a_token(tmp_path):
    assert guarded(tmp_path).get("/api/health").json()["reset_requires_token"] is True
    assert client(tmp_path).get("/api/health").json()["reset_requires_token"] is False


def test_a_public_deployment_still_refuses_to_start_with_reset_open(tmp_path):
    import pytest

    with pytest.raises(RuntimeError, match="/api/reset"):
        create_app(Settings(
            demo_token=None, database_path=tmp_path / "pub.db",
            ans_public_base_url="https://synapse-vt.us", ans_domain=DOMAIN,
        ))
