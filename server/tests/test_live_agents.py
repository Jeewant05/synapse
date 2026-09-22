import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.config import Settings
from server.app.live_agents import Artifact, LiveRun, LiveRuns
from server.app.live_preview import render_agent_preview
from server.app.live_routes import build_live_router
from server.app.main import create_app
from server.app.tracing import CacheTraceSink

INTERACTIVE_PREVIEW = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Task Flow</title></head>
<body><button id="add">Add task</button><output id="count">0</output>
<script>let count=0;add.onclick=()=>{count+=1;document.querySelector('#count').value=count}</script>
</body></html>"""


class FakeProvider:
    def __init__(self, role: str, invalid_path: bool = False):
        self.role = role
        self.invalid_path = invalid_path
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Do not write code yet" in prompt:
            return json.dumps({"intention": f"{self.role} will implement its owned deliverable"})
        responses = {
            "backend": {
                "report": "Built the item API.",
                "files": [{"path": "backend/app.py", "content": "def items(): return []\n"}],
            },
            "frontend": {
                "report": "Built the task list.",
                "files": [
                    {
                        "path": "backend/stolen.py" if self.invalid_path else "frontend/App.tsx",
                        "content": "export function App() { return null }\n",
                    },
                    {
                        "path": "frontend/preview.html",
                        "content": INTERACTIVE_PREVIEW,
                    },
                ],
            },
            "integration": {
                "report": "Added the contract handoff.",
                "files": [{"path": "integration/README.md", "content": "# Integration\n"}],
            },
        }
        return json.dumps(responses[self.role])


class MalformedProposalThenValidProvider(FakeProvider):
    def __init__(self, role: str):
        super().__init__(role)
        self.build_calls = 0

    async def generate(self, prompt: str) -> str:
        if "Do not write code yet" in prompt:
            return await super().generate(prompt)
        self.prompts.append(prompt)
        self.build_calls += 1
        if self.build_calls == 1:
            return json.dumps({
                "report": "I made the frontend.",
                "files": [{"path": "frontend/App.tsx"}],
            })
        return json.dumps({
            "report": "Built the task list.",
            "files": [
                {"path": "frontend/App.tsx", "content": "export function App() { return null }\n"},
                {
                    "path": "frontend/preview.html",
                    "content": INTERACTIVE_PREVIEW,
                },
            ],
        })


def providers(invalid_frontend: bool = False):
    return {
        "backend": FakeProvider("backend"),
        "frontend": FakeProvider("frontend", invalid_frontend),
        "integration": FakeProvider("integration"),
    }


def test_three_apis_plan_then_generate_scoped_project(tmp_path):
    agent_providers = providers()
    run = LiveRun("run-test", "Build tasks", tmp_path / "run-test", agent_providers)

    asyncio.run(run.execute())

    assert run.status == "complete"
    assert all(len(provider.prompts) == 2 for provider in agent_providers.values())
    assert run.intentions == {
        "backend": "backend will implement its owned deliverable",
        "frontend": "frontend will implement its owned deliverable",
        "integration": "integration will implement its owned deliverable",
    }
    implementation_prompts = [provider.prompts[1] for provider in agent_providers.values()]
    assert all(
        all(intention in prompt for intention in run.intentions.values())
        for prompt in implementation_prompts
    )
    assert {item.agent_id for item in run.artifacts} == {
        "coordinator",
        "backend",
        "frontend",
        "integration",
    }
    assert (run.root / "backend/app.py").exists()
    assert (run.root / "frontend/App.tsx").exists()
    assert (run.root / "frontend/preview.html").exists()
    assert (run.root / "integration/README.md").exists()
    assert run.preview_html is not None
    assert "Task Flow" in run.preview_html
    assert run.snapshot()["preview_url"] == "/api/live/runs/run-test/preview"
    assert run.snapshot()["git_repository"] is True
    assert run.events[-1]["event_type"] == "run_complete"


def test_invalid_proposal_commits_no_files(tmp_path):
    run = LiveRun(
        "run-invalid", "Build tasks", tmp_path / "run-invalid", providers(invalid_frontend=True)
    )

    asyncio.run(run.execute())

    assert run.status == "failed"
    assert run.events[-1]["event_type"] == "run_failed"
    assert "outside frontend/" in run.events[-1]["message"]
    assert run.artifacts == []
    assert (run.root / ".git").is_dir()
    assert not [
        path for path in run.root.rglob("*")
        if ".git" not in path.parts and path.name != ".synapse-run.json"
    ]


def test_malformed_file_proposal_is_corrected_once_before_staging(tmp_path):
    agent_providers = providers()
    frontend = MalformedProposalThenValidProvider("frontend")
    agent_providers["frontend"] = frontend
    run = LiveRun("run-retry", "Build tasks", tmp_path / "run-retry", agent_providers)

    asyncio.run(run.execute())

    assert run.status == "complete"
    assert frontend.build_calls == 2
    assert "previous proposal was invalid" in frontend.prompts[-1]


def test_completed_run_flushes_its_trace_events(tmp_path):
    trace = CacheTraceSink()
    run = LiveRun("run-traced", "Build tasks", tmp_path / "run-traced", providers(), trace)

    asyncio.run(run.execute())
    events = asyncio.run(trace.recent(run_id="run-traced"))

    assert run.status == "complete"
    assert events[0].event_type == "run_complete"
    assert any(event.event_type == "validation_passed" for event in events)


def test_completed_run_can_be_restored_after_api_restart(tmp_path):
    root = tmp_path / "run-restored"
    run = LiveRun("run-restored", "Build tasks", root, providers())

    asyncio.run(run.execute())
    restored = LiveRun.load(root)

    assert restored is not None
    assert restored.status == "complete"
    assert restored.snapshot()["artifacts"] == run.snapshot()["artifacts"]


def test_legacy_workspace_can_be_recovered_for_dashboard(tmp_path):
    root = tmp_path / "run-legacy"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "main.py").write_text("print('ready')\n", encoding="utf-8")

    recovered = LiveRun.recover(root)

    assert recovered is not None
    assert recovered.status == "complete"
    assert recovered.artifacts[0].agent_id == "backend"


def test_interactive_preview_is_served_in_an_opaque_networkless_sandbox(tmp_path):
    html = render_agent_preview(
        "Build an interactive task list",
        [Artifact("frontend/preview.html", "frontend", INTERACTIVE_PREVIEW)],
    )
    assert "add.onclick" in html

    runs = LiveRuns({}, tmp_path, None)  # type: ignore[arg-type]
    run = LiveRun("run-preview", "Build safely", tmp_path / "run-preview", {})
    run.status = "complete"
    run.preview_html = html
    runs.runs[run.run_id] = run
    app = FastAPI()
    app.include_router(build_live_router(runs))

    response = TestClient(app).get("/api/live/runs/run-preview/preview")
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert policy.startswith("sandbox allow-scripts;")
    assert "allow-same-origin" not in policy
    assert "script-src 'unsafe-inline'" in policy
    assert "connect-src 'none'" in policy
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "content",
    [
        "<html><body>No script</body></html>",
        "<html><body><iframe srcdoc='bad'></iframe><script>void 0</script></body></html>",
        "<html><body><script>void 0</script><meta http-equiv='refresh' content='0'></body></html>",
    ],
)
def test_preview_rejects_noninteractive_or_embedded_documents(content):
    with pytest.raises(ValueError):
        render_agent_preview("Build safely", [Artifact("frontend/preview.html", "frontend", content)])


def test_live_config_reports_each_missing_api(tmp_path):
    client = TestClient(create_app(Settings(demo_token=None, 
        database_path=tmp_path / "state.db", trace_mode="cache", backend_provider="none",
        frontend_provider="none", qa_provider="none",
    )))

    config = client.get("/api/live/config")
    start = client.post("/api/live/runs", json={"objective": "Build tasks"})

    assert config.status_code == 200
    assert config.json()["configured"] is False
    assert [role["configured"] for role in config.json()["roles"]] == [False, False, False]
    assert start.status_code == 503
    assert "backend, frontend, integration" in start.json()["detail"]


# --- Why a run failed ---------------------------------------------------------


class RejectingProvider:
    """A provider whose upstream refuses every request, as with a spent account."""

    name = "huggingface"
    model = "openai/gpt-oss-120b:fastest"

    def __init__(self, message: str):
        self.message = message

    async def generate(self, prompt: str) -> str:
        raise RuntimeError(self.message)


CREDITS = 'huggingface 402: {"error":"You have depleted your monthly included credits."}'


def test_a_provider_rejection_is_not_reported_as_a_validation_failure(tmp_path):
    """No code is generated when the provider refuses, so there is nothing to validate.

    This surfaced as "The build did not pass validation" on the live site while
    the real cause was an out-of-credits account, which sent the operator to
    debug generated code that never existed.
    """
    rejecting = {role: RejectingProvider(CREDITS) for role in ("backend", "frontend", "integration")}
    run = LiveRun("run-402", "Build tasks", tmp_path / "run-402", rejecting)

    asyncio.run(run.execute())

    snapshot = run.snapshot()
    assert snapshot["status"] == "failed"
    assert "402" in snapshot["error"]
    assert snapshot["failure_title"] == "huggingface rejected the request: the account is out of credits."
    assert "validation" not in snapshot["failure_title"]


def test_a_successful_run_reports_no_failure(tmp_path):
    run = LiveRun("run-ok", "Build tasks", tmp_path / "run-ok", providers())
    asyncio.run(run.execute())
    snapshot = run.snapshot()
    assert snapshot["status"] == "complete"
    assert snapshot["error"] is None and snapshot["failure_title"] is None


def test_genuine_validation_failures_keep_the_validation_wording(tmp_path):
    run = LiveRun("run-bad", "Build tasks", tmp_path / "run-bad", providers(invalid_frontend=True))
    asyncio.run(run.execute())
    assert run.status == "failed"
    assert run.snapshot()["failure_title"] == "The agent build failed validation."


def test_failure_headlines_name_the_actual_cause():
    from server.app.live_agents import explain_failure

    assert "out of credits" in explain_failure(CREDITS)
    assert "rejected the API key" in explain_failure("huggingface 401: bad token")
    assert "rate limiting" in explain_failure("groq 429: rate limit exceeded")
    assert "not configured" in explain_failure("HUGGINGFACE_API_KEY is not set")
    assert "could not be reached" in explain_failure("huggingface request timed out")


# --- File-count limits: stated, counted, and retried once ---------------------


class OverproducingProvider(FakeProvider):
    """Returns too many files for its first `bad_answers` build answers, then behaves."""

    def __init__(self, role: str, bad_answers: int, too_many: int = 9):
        super().__init__(role)
        self.bad_answers, self.too_many, self.build_calls = bad_answers, too_many, 0

    async def generate(self, prompt: str) -> str:
        if "Do not write code yet" in prompt:
            return await super().generate(prompt)
        self.build_calls += 1
        if self.build_calls <= self.bad_answers:
            files = [{"path": f"backend/part{i}.py", "content": "x = 1\n"} for i in range(self.too_many)]
            return json.dumps({"report": "Split everything into many files.", "files": files})
        return await super().generate(prompt)


def swap_backend(provider):
    mixed = providers()
    mixed["backend"] = provider
    return mixed


def test_the_prompt_states_the_file_limit_the_validator_enforces(tmp_path):
    """The limit was enforced but never mentioned, so models exceeded it blindly."""
    backend = FakeProvider("backend")
    run = LiveRun("run-limits", "Build tasks", tmp_path / "run-limits", swap_backend(backend))
    asyncio.run(run.execute())

    build_prompt = next(p for p in backend.prompts if "Do not write code yet" not in p)
    assert "between 1 and 6 files" in build_prompt
    assert "characters" in build_prompt


def test_one_over_limit_answer_is_corrected_by_a_retry(tmp_path):
    flaky = OverproducingProvider("backend", bad_answers=1)
    run = LiveRun("run-retry", "Build tasks", tmp_path / "run-retry", swap_backend(flaky))

    asyncio.run(run.execute())

    assert run.status == "complete"
    assert flaky.build_calls == 2
    retry = next(e for e in run.events if e["event_type"] == "proposal_retry")
    assert "returned 9 files" in retry["message"]
    # The model is told what was wrong, so the second answer can fix it.
    assert "rejected: returned 9 files" in flaky.prompts[-1]


def test_a_persistent_over_limit_answer_fails_and_reports_the_count(tmp_path):
    stubborn = OverproducingProvider("backend", bad_answers=99, too_many=9)
    run = LiveRun("run-stubborn", "Build tasks", tmp_path / "run-stubborn", swap_backend(stubborn))

    asyncio.run(run.execute())

    assert run.status == "failed"
    assert stubborn.build_calls == 2  # one retry, not a loop
    assert "backend returned 9 files; it must return 1-6" in run.snapshot()["error"]
    assert run.snapshot()["failure_title"] == "The agent build failed validation."


def test_an_empty_answer_is_reported_as_empty_not_as_a_bad_count(tmp_path):
    class Empty(FakeProvider):
        async def generate(self, prompt: str) -> str:
            if "Do not write code yet" in prompt:
                return await super().generate(prompt)
            return json.dumps({"report": "Nothing to do.", "files": []})

    run = LiveRun("run-empty", "Build tasks", tmp_path / "run-empty", swap_backend(Empty("backend")))
    asyncio.run(run.execute())
    assert run.status == "failed"
    assert "returned no files" in run.snapshot()["error"]


# --- Provider overload: retried, then explained -------------------------------


def test_an_overload_is_named_as_such_not_as_a_validation_failure():
    from server.app.live_agents import explain_failure

    spike = 'gemini 503: {"error":{"message":"This model is currently experiencing high demand."}}'
    assert explain_failure(spike) == "gemini is temporarily overloaded. Try again shortly."
    assert "overloaded" in explain_failure("huggingface 502: bad gateway")
    # A genuine validation error must not be mistaken for an overload.
    assert explain_failure("backend returned 9 files; it must return 1-6") == (
        "The agent build failed validation."
    )


# --- Tolerant parsing of model answers ---------------------------------------


def parse(raw: str):
    return LiveRun._json(raw)


def test_a_raw_newline_inside_a_string_is_accepted():
    """The failure seen on the live site: `Invalid control character at line 18`.

    A model puts a whole markdown file inside a JSON string and leaves some real
    line breaks in it. The content is right and only the escaping is off.
    """
    raw = '{"report": "ok", "files": [{"path": "integration/README.md", "content": "# Title\nbody\n\ttabbed"}]}'
    assert parse(raw)["files"][0]["content"] == "# Title\nbody\n\ttabbed"


def test_json_wrapped_in_prose_or_fences_is_still_read():
    body = '{"report": "ok", "files": []}'
    assert parse(f"Here is the result:\n{body}\nHope that helps.") == {"report": "ok", "files": []}
    assert parse(f"```json\n{body}\n```") == {"report": "ok", "files": []}


def test_genuinely_malformed_json_still_fails():
    for raw in ['{"report": "ok", "files": [', "no json here at all", '{"report": ok}']:
        with pytest.raises(ValueError):
            parse(raw)
    with pytest.raises(TypeError):
        parse("[1, 2, 3]")


def test_the_retry_tells_the_model_where_its_json_broke(tmp_path):
    """Naming the error location lets the second answer fix it instead of guess."""

    class BrokenThenFine(FakeProvider):
        def __init__(self, role):
            super().__init__(role)
            self.build_calls = 0

        async def generate(self, prompt: str) -> str:
            if "Do not write code yet" in prompt:
                return await super().generate(prompt)
            self.build_calls += 1
            if self.build_calls == 1:
                return '{"report": "ok", "files": [{"path": "backend/a.py", "content": "x"'
            return await super().generate(prompt)

    backend = BrokenThenFine("backend")
    run = LiveRun("run-json", "Build tasks", tmp_path / "run-json", swap_backend(backend))
    asyncio.run(run.execute())

    assert run.status == "complete"
    retry = next(e for e in run.events if e["event_type"] == "proposal_retry")
    assert "was not valid JSON" in retry["message"] and "line" in retry["message"]


# --- A run must never hang silently ------------------------------------------


def test_a_missing_git_binary_fails_the_run_with_a_reason(tmp_path, monkeypatch):
    """Live on the site: no `git` in the container, so every run hung.

    `git init` ran outside execute()'s error handling, the background task died
    with nothing recorded, and the run stayed in "planning" with no events and no
    error. The dashboard keeps its buttons disabled behind a run in that state, so
    the whole page looked dead.
    """

    async def no_git(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_git)
    run = LiveRun("run-nogit", "Build tasks", tmp_path / "run-nogit", providers())

    asyncio.run(run.execute())

    assert run.status == "failed"  # not stuck in "planning"
    assert "git is not installed" in run.snapshot()["error"]
    assert run.events[-1]["event_type"] == "run_failed"
    assert run.snapshot()["failure_title"] == "The server could not set up this run's workspace."


def test_a_run_whose_directory_cannot_be_created_still_reports_failure(tmp_path):
    """The failure handler saves state; that must not raise when there is no directory."""
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the run directory should go")
    run = LiveRun("run-blocked", "Build tasks", blocker / "run", providers())

    asyncio.run(run.execute())  # raises out of the handler if _persist is unsafe

    assert run.status == "failed"
    assert run.snapshot()["error"]


def test_the_container_image_installs_git():
    """The cause was the environment, not the code, so guard the Dockerfile itself."""
    from server.app.config import ROOT

    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile[dockerfile.index("AS runtime"):]
    assert "apt-get install" in runtime and " git" in runtime
