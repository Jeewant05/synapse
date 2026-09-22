import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from server.app.live_agents import ROLES, LiveRuns


class LiveStartRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2_000)


class LiveRunResponse(BaseModel):
    run_id: str
    status: str


def build_live_router(runs: LiveRuns) -> APIRouter:
    router = APIRouter(prefix="/api/live", tags=["live-agents"])

    @router.get("/config")
    async def config():
        return {
            "configured": runs.configured,
            "model": "per-role",
            "roles": [
                {
                    "id": role.id,
                    "title": role.title,
                    "responsibility": role.responsibility,
                    "configured": role.id in runs.configured_roles,
                    "provider": runs.providers[role.id].name if role.id in runs.providers else None,
                    "model": runs.providers[role.id].model if role.id in runs.providers else None,
                }
                for role in ROLES
            ],
        }

    @router.post("/runs", response_model=LiveRunResponse)
    async def start(body: LiveStartRequest):
        try:
            run = runs.start(body.objective)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return LiveRunResponse(run_id=run.run_id, status=run.status)

    @router.get("/runs")
    async def recent_runs():
        return [run.snapshot() for run in runs.recent()]

    @router.get("/runs/{run_id}")
    async def snapshot(run_id: str):
        try:
            return runs.get(run_id).snapshot()
        except KeyError as exc:
            raise HTTPException(404, "live run not found") from exc

    @router.get("/runs/{run_id}/preview", response_class=HTMLResponse)
    async def preview(run_id: str):
        try:
            run = runs.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, "live run not found") from exc
        if run.preview_html is None:
            detail = "preview generation failed" if run.status == "failed" else "preview is still building"
            raise HTTPException(409, detail)
        return HTMLResponse(
            run.preview_html,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "sandbox allow-scripts; default-src 'none'; "
                    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "img-src data: blob:; media-src data: blob:; font-src data:; "
                    "connect-src 'none'; worker-src blob:; base-uri 'none'; form-action 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/runs/{run_id}/events")
    async def events(run_id: str):
        try:
            run = runs.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, "live run not found") from exc

        async def stream():
            sent = 0
            while True:
                while sent < len(run.events):
                    event = run.events[sent]
                    sent += 1
                    yield f"data: {json.dumps(event)}\n\n"
                if run.task is not None and run.task.done():
                    break
                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
