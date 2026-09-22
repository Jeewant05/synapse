"""Read-only debugging endpoints for persisted agent traces."""

from fastapi import APIRouter, Query

from server.app.models import TraceEvent
from server.app.tracing import TraceSink


def build_trace_router(trace: TraceSink) -> APIRouter:
    router = APIRouter(prefix="/api/traces", tags=["tracing"])

    @router.get("", response_model=list[TraceEvent])
    async def recent(limit: int = Query(100, ge=1, le=200), run_id: str | None = None):
        return await trace.recent(limit=limit, run_id=run_id)

    return router
