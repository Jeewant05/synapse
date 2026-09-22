"""Bounded three-agent coding runs with plan-before-write coordination."""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.live_preview import PREVIEW_PATH, render_agent_preview
from server.app.models import TraceEvent
from server.app.providers import Provider
from server.app.tracing import TraceSink

log = logging.getLogger("synapse")


@dataclass(frozen=True)
class AgentRole:
    id: str
    title: str
    responsibility: str
    allowed_root: str


ROLES = (
    AgentRole(
        "backend",
        "Backend agent",
        "Build the API, data model, and server-side behavior.",
        "backend",
    ),
    AgentRole(
        "frontend",
        "Frontend agent",
        "Build the UI that consumes the shared API contract.",
        "frontend",
    ),
    AgentRole(
        "integration",
        "Integration agent",
        "Review both implementations and add contract tests, setup, and handoff docs.",
        "integration",
    ),
)
ROLE_BY_ID = {role.id: role for role in ROLES}
MAX_OBJECTIVE_CHARS = 2_000
MAX_FILES_PER_AGENT = 6
MAX_FILE_CHARS = 50_000
MAX_TOTAL_CHARS = 180_000


def explain_failure(message: str) -> str:
    """One-line headline for a failed run, from the error the run recorded.

    A provider rejecting the request is not a validation failure -- no code was
    ever generated to validate -- and calling it one misdirects the operator.
    """
    text = message.lower()
    provider = message.split(" ", 1)[0] if message[:1].isalpha() else "The provider"
    if " 402" in text or "depleted" in text or "credits" in text or "payment required" in text:
        return f"{provider} rejected the request: the account is out of credits."
    if " 401" in text or " 403" in text or "unauthorized" in text or "invalid api key" in text:
        return f"{provider} rejected the API key."
    if " 429" in text or "rate limit" in text:
        return f"{provider} is rate limiting requests. Try again shortly."
    if re.search(r" 5\d\d\b", text) or "unavailable" in text or "high demand" in text:
        return f"{provider} is temporarily overloaded. Try again shortly."
    if "is not set" in text or "not configured" in text:
        return "A live-agent provider key is not configured."
    if "workspace" in text:
        return "The server could not set up this run's workspace."
    if any(word in text for word in ("timeout", "timed out", "connection")):
        return f"{provider} could not be reached."
    return "The agent build failed validation."


@dataclass
class Artifact:
    path: str
    agent_id: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "agent_id": self.agent_id, "content": self.content}


@dataclass
class LiveRun:
    run_id: str
    objective: str
    root: Path
    providers: dict[str, Provider]
    trace: TraceSink | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    intentions: dict[str, str] = field(default_factory=dict)
    reports: dict[str, str] = field(default_factory=dict)
    preview_html: str | None = None
    status: str = "queued"
    error: str | None = None
    task: asyncio.Task[None] | None = None

    def emit(self, agent: str, event_type: str, message: str, **extra: Any) -> None:
        event = {
            "agent_id": agent,
            "event_type": event_type,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
            **extra,
        }
        self.events.append(event)
        if self.trace:
            asyncio.create_task(self._trace(event))

    async def _trace(self, event: dict[str, Any]) -> None:
        if self.trace is None:
            return
        try:
            await self.trace.log(TraceEvent(
                trace_id=f"trace-{uuid.uuid4().hex}", source="live_agent",
                event_type=event["event_type"], timestamp=event["timestamp"], run_id=self.run_id,
                agent_id=event["agent_id"], payload={
                    "message": event["message"],
                    **{key: value for key, value in event.items() if key not in {"agent_id", "event_type", "message", "timestamp"}},
                },
            ))
        except Exception as exc:  # noqa: BLE001 - tracing must never stop a run
            log.warning("live trace.log failed: %s", exc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "objective": self.objective,
            "status": self.status,
            "workspace": str(self.root),
            "git_repository": (self.root / ".git").is_dir(),
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "intentions": self.intentions,
            "reports": self.reports,
            "preview_url": (
                f"/api/live/runs/{self.run_id}/preview" if self.preview_html else None
            ),
            # Why a failed run failed, so the dashboard need not guess.
            "error": self.error,
            "failure_title": explain_failure(self.error) if self.error else None,
        }

    async def _init_workspace(self) -> None:
        """Create the run's directory and initialise a Git repository in it."""
        self.root.mkdir(parents=True, exist_ok=False)
        try:
            git = await asyncio.create_subprocess_exec(
                "git", "init", "-b", "main", cwd=self.root,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "git is not installed in this environment, so the run's workspace "
                "could not be created"
            ) from exc
        if await git.wait() != 0:
            raise RuntimeError("could not initialize the generated Git workspace")
        self._persist()

    async def execute(self) -> None:
        self.status = "planning"
        try:
            # Inside the handler on purpose. This ran outside it once, and a missing
            # `git` binary killed the background task with nothing recorded: the run
            # sat in "planning" forever with no events and no error, and the
            # dashboard's buttons stayed disabled behind it.
            await self._init_workspace()
            self.emit("coordinator", "run_started", "Created an isolated project workspace")
            contract = self._contract()
            plans = await asyncio.gather(*(self._plan(role, contract) for role in ROLES))
            self.intentions = {role.id: plan for role, plan in zip(ROLES, plans, strict=True)}
            self.emit(
                "coordinator", "intentions_shared", "Shared all three intentions with every agent"
            )

            self.status = "building"
            backend, frontend = await asyncio.gather(
                self._build(ROLES[0], {"contract": contract}),
                self._build(ROLES[1], {"contract": contract}),
            )
            staged = self._stage(ROLES[0], backend, [])
            staged += self._stage(ROLES[1], frontend, staged)

            integration = await self._build(
                ROLES[2],
                {
                    "contract": contract,
                    "generated_files": [
                        {"path": item.path, "content": item.content[:8_000]} for item in staged
                    ],
                },
            )
            staged += self._stage(ROLES[2], integration, staged)
            self._validate_staged(staged)
            self._commit(contract, staged)
        except Exception as exc:  # noqa: BLE001 - failures belong in the live timeline
            self.status = "failed"
            self.error = str(exc)[:1_000]
            self.emit("coordinator", "run_failed", self.error)
            self._persist()
            return

        self.status = "complete"
        self.emit(
            "coordinator",
            "run_complete",
            f"Committed {len(staged)} agent files after intention and scope validation",
        )
        self._persist()

    async def _plan(self, role: AgentRole, contract: dict[str, Any]) -> str:
        self.emit(role.id, "intention_started", f"{role.title} is planning before coding")
        prompt = f"""You are the {role.title} planning your work before any files are written.
Objective: {self.objective}
Responsibility: {role.responsibility}
Owned directory: {role.allowed_root}/
Shared API contract: {json.dumps(contract)}

Return JSON only: {{"intention":"a concise plan covering approach, files, dependencies, and validation"}}.
Do not write code yet. Your intention will be shared with the other two agents.
"""
        raw = await self.providers[role.id].generate(prompt)
        data = self._json(raw)
        intention = data.get("intention")
        if not isinstance(intention, str) or not intention.strip():
            raise TypeError(f"{role.id} intention response is invalid")
        intention = intention.strip()[:3_000]
        self.emit(role.id, "intention_ready", intention)
        return intention

    async def _build(self, role: AgentRole, context: dict[str, Any]) -> dict[str, Any]:
        self.emit(role.id, "agent_started", f"{role.title} is implementing its intention")
        preview_requirement = ""
        if role.id == "frontend":
            preview_requirement = f"""
You MUST include `{PREVIEW_PATH}` in files. It is the finished application shown to the user,
not a mockup or a description. Return a complete standalone HTML document with inline CSS and
inline vanilla JavaScript. It must visibly respond to mouse/touch and keyboard input and implement
the objective itself. For a game, include clear controls, start/reset behavior, scoring, and a real
play loop using Canvas or the DOM. For another kind of app, make its primary workflow usable.

The preview runs in an isolated browser sandbox with no network access. Do not use imports,
frameworks, CDNs, fetch, XMLHttpRequest, WebSocket, external URLs, iframes, forms, or assets that
are not data URLs. Make it responsive and accessible, and ensure it works by opening this one file.
"""
        prompt = f"""You are the {role.title} in a coordinated coding demo.
Objective: {self.objective}
Responsibility: {role.responsibility}
You may create files only inside the `{role.allowed_root}/` directory.

Intentions agreed before implementation (use these to avoid conflicts):
{json.dumps(self.intentions, indent=2)}

Shared project context:
{json.dumps(context, indent=2)}

Return JSON only, without markdown fences, using this exact shape:
{{"report":"short implementation summary","files":[{{"path":"{role.allowed_root}/relative-name","content":"complete file contents"}}]}}
Create a small, coherent implementation aligned with all three intentions. Do not include secrets,
absolute paths, parent-directory traversal, or files outside your assigned directory.

Hard limits -- a response outside them is rejected:
- Return between 1 and {MAX_FILES_PER_AGENT} files. Merge related code into fewer files instead of splitting it.
- Keep each file under {MAX_FILE_CHARS:,} characters.
{preview_requirement}
"""
        provider = self.providers[role.id]
        proposal, problem = self._read_proposal(await provider.generate(prompt))
        if problem:
            # One corrective retry. A single sloppy answer should not sink a whole
            # three-agent run, and the model can fix a stated problem.
            self.emit(role.id, "proposal_retry", f"{problem}; asking once more")
            proposal, problem = self._read_proposal(await provider.generate(
                f"{prompt}\nYour previous proposal was invalid and rejected: {problem}.\n"
                "Return the complete JSON again, within the limits."
            ))
        if problem or proposal is None:
            raise ValueError(f"{role.id} {problem or 'returned no usable proposal'}")
        if not isinstance(proposal.get("report"), str):
            raise TypeError(f"{role.id} response must contain report and files")
        self.emit(role.id, "proposal_received", proposal["report"][:1_000])
        return proposal

    def _read_proposal(self, raw: str) -> tuple[dict[str, Any] | None, str | None]:
        """Parse a build answer; return (proposal, None) or (None, what is wrong).

        The wording is written for the model to read on the retry, so it says what
        was received and what is allowed rather than only that something failed.
        """
        try:
            proposal = self._json(raw)
        except json.JSONDecodeError as exc:
            return None, (
                f"was not valid JSON ({exc.msg} at line {exc.lineno} column {exc.colno}); "
                "escape newlines, quotes, and backslashes inside every file content string"
            )
        except TypeError:
            return None, "returned something that was not a single JSON object"
        files = proposal.get("files")
        if not isinstance(files, list) or not isinstance(proposal.get("report"), str):
            return None, 'must return JSON with a "report" string and a "files" list'
        if not files:
            return None, f"returned no files; it must return 1-{MAX_FILES_PER_AGENT}"
        if len(files) > MAX_FILES_PER_AGENT:
            return None, (
                f"returned {len(files)} files; it must return 1-{MAX_FILES_PER_AGENT}. "
                "Merge related code into fewer files"
            )
        for entry in files:
            if not isinstance(entry, dict):
                return None, "returned a file entry that was not an object"
            if not isinstance(entry.get("path"), str) or not isinstance(entry.get("content"), str) or not entry["content"].strip():
                return None, "returned a file without a non-empty path and content"
        oversized = [
            str(f.get("path")) for f in files
            if isinstance(f, dict) and len(str(f.get("content", ""))) > MAX_FILE_CHARS
        ]
        if oversized:
            return None, f"{oversized[0]} exceeds the {MAX_FILE_CHARS:,}-character per-file limit"
        return proposal, None

    def _stage(
        self, role: AgentRole, proposal: dict[str, Any], existing: list[Artifact]
    ) -> list[Artifact]:
        files = proposal["files"]
        if not files or len(files) > MAX_FILES_PER_AGENT:
            raise ValueError(
                f"{role.id} returned {len(files)} files; it must return 1-{MAX_FILES_PER_AGENT}"
            )
        pending: list[Artifact] = []
        for value in files:
            if not isinstance(value, dict):
                raise TypeError(f"{role.id} returned an invalid file entry")
            path, content = value.get("path"), value.get("content")
            if not isinstance(path, str) or not isinstance(content, str) or not content.strip():
                raise TypeError(f"{role.id} returned a file without a path or content")
            normalized = PurePosixPath(path)
            if (
                normalized.is_absolute()
                or ".." in normalized.parts
                or not normalized.parts
                or normalized.parts[0] != role.allowed_root
            ):
                raise ValueError(f"{role.id} attempted to write outside {role.allowed_root}/")
            if len(content) > MAX_FILE_CHARS:
                raise ValueError(f"{path} exceeds the per-file size limit")
            if any(item.path == normalized.as_posix() for item in existing + pending):
                raise ValueError(f"duplicate generated path: {normalized.as_posix()}")
            pending.append(Artifact(normalized.as_posix(), role.id, content))
        self.reports[role.id] = proposal["report"][:2_000]
        self.emit(role.id, "proposal_staged", f"Staged {len(pending)} files; nothing written yet")
        return pending

    def _validate_staged(self, staged: list[Artifact]) -> None:
        missing = {role.id for role in ROLES} - {item.agent_id for item in staged}
        if missing:
            raise ValueError(f"missing deliverables from: {', '.join(sorted(missing))}")
        if sum(len(item.content) for item in staged) > MAX_TOTAL_CHARS:
            raise ValueError("generated project exceeds the run size limit")
        self.preview_html = render_agent_preview(self.objective, staged)
        self.emit(
            "coordinator",
            "validation_passed",
            "Intentions, ownership, paths, duplicates, size, and preview checks passed",
        )
        self.emit("coordinator", "preview_ready", "Built the finished application preview")

    def _commit(self, contract: dict[str, Any], staged: list[Artifact]) -> None:
        self._write(
            Artifact(
                "shared/api-contract.json", "coordinator", json.dumps(contract, indent=2) + "\n"
            )
        )
        self.emit(
            "coordinator", "contract_committed", "Committed the coordinator-owned API contract"
        )
        for artifact in staged:
            self._write(artifact)
            self.emit(artifact.agent_id, "file_committed", artifact.path, path=artifact.path)

    def _write(self, artifact: Artifact) -> None:
        destination = self.root.joinpath(*PurePosixPath(artifact.path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(artifact.content, encoding="utf-8")
        self.artifacts.append(artifact)

    def _persist(self) -> None:
        if not self.root.is_dir():
            # A run that failed before its workspace existed has nowhere to save
            # to. Raising here would escape the failure handler and hide the very
            # error that is being recorded.
            return
        data = self.snapshot() | {"preview_html": self.preview_html}
        (self.root / ".synapse-run.json").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, root: Path) -> "LiveRun | None":
        metadata = root / ".synapse-run.json"
        if not metadata.is_file():
            return None
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            run = cls(str(data["run_id"]), str(data.get("objective", "Recovered run")), root, {})
            run.status = str(data.get("status", "complete"))
            run.intentions = dict(data.get("intentions", {}))
            run.reports = dict(data.get("reports", {}))
            run.error = data.get("error")
            run.preview_html = data.get("preview_html")
            run.artifacts = [Artifact(**item) for item in data.get("artifacts", [])]
            return run
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def recover(cls, root: Path) -> "LiveRun | None":
        if not root.is_dir():
            return None
        run = cls(root.name, "Recovered generated project", root, {})
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.parts or path.name == ".synapse-run.json":
                continue
            relative = path.relative_to(root).as_posix()
            owner = relative.split("/", 1)[0]
            run.artifacts.append(Artifact(relative, owner, path.read_text(encoding="utf-8")))
        if not run.artifacts:
            return None
        run.status = "complete"
        run._persist()
        return run

    @staticmethod
    def _contract() -> dict[str, Any]:
        return {
            "endpoint": "GET /api/items",
            "response": {"items": [{"id": "string", "title": "string", "done": "boolean"}]},
            "ownership": {role.id: f"{role.allowed_root}/**" for role in ROLES},
        }

    @staticmethod
    def _json(raw: str) -> dict[str, Any]:
        """Parse a model's answer into a JSON object, tolerating common slips.

        Models embed whole source files as JSON strings and routinely leave a raw
        newline or tab inside one, which the strict parser rejects as "Invalid
        control character". That is a formatting slip, not a wrong answer, so
        control characters are allowed. Fenced blocks and prose around the object
        are tolerated too. Anything genuinely malformed still raises.
        """
        value = raw.strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            data = json.loads(value, strict=False)
        except json.JSONDecodeError:
            start, end = value.find("{"), value.rfind("}")
            if start == -1 or end <= start:
                raise
            data = json.loads(value[start : end + 1], strict=False)
        if not isinstance(data, dict):
            raise TypeError("Agent response must be a JSON object")
        return data


class LiveRuns:
    def __init__(self, providers: dict[str, Provider], root: Path, trace: TraceSink):
        self.providers, self.root, self.trace = providers, root, trace
        self.runs: dict[str, LiveRun] = {}
        if root.is_dir():
            for workspace in sorted(root.glob("run-*"), key=lambda path: path.stat().st_mtime):
                run = LiveRun.load(workspace) or LiveRun.recover(workspace)
                if run is not None:
                    self.runs[run.run_id] = run

    @property
    def configured(self) -> bool:
        return all(role.id in self.providers for role in ROLES)

    @property
    def configured_roles(self) -> set[str]:
        return set(self.providers)

    def start(self, objective: str) -> LiveRun:
        missing = [role.id for role in ROLES if role.id not in self.providers]
        if missing:
            raise RuntimeError(f"Missing agent provider configuration for: {', '.join(missing)}")
        objective = objective.strip()
        if not objective:
            raise ValueError("objective cannot be empty")
        if len(objective) > MAX_OBJECTIVE_CHARS:
            raise ValueError(f"objective must be at most {MAX_OBJECTIVE_CHARS} characters")
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        run = LiveRun(run_id, objective, self.root / run_id, self.providers, self.trace)
        self.runs[run_id] = run
        run.task = asyncio.create_task(run.execute())
        return run

    def get(self, run_id: str) -> LiveRun:
        if run_id not in self.runs:
            raise KeyError(run_id)
        return self.runs[run_id]

    def recent(self, limit: int = 20) -> list[LiveRun]:
        """Return newest in-process runs first for the repository browser."""
        return list(reversed(self.runs.values()))[:limit]
