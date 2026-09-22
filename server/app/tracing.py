"""Trace persistence for debugging coordinated agent activity.

The cache sink keeps the demo self-contained. The Databricks sink uses a SQL
warehouse only when explicit environment configuration is present.
"""

import asyncio
import json
import logging
import re
from collections import deque
from datetime import UTC, datetime
from typing import Protocol

from databricks.sdk import WorkspaceClient

from server.app.config import Settings
from server.app.models import TraceEvent, WriteReceipt

log = logging.getLogger("synapse")
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){2}$")


def _as_utc_iso(value: str) -> str:
    """Return a timezone-aware ISO string for a Databricks TIMESTAMP cast.

    `CAST(ts AS STRING)` yields "2026-09-20 07:17:11.611507" in the session
    zone (UTC) with no offset, which a browser then reads as local time. Any
    string that already carries an offset is returned as-is.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


class TraceSink(Protocol):
    source: str

    async def log(self, event: TraceEvent) -> WriteReceipt: ...

    async def recent(self, limit: int = 100, run_id: str | None = None) -> list[TraceEvent]: ...


class CacheTraceSink:
    source = "cache"

    def __init__(self) -> None:
        self._events: deque[TraceEvent] = deque(maxlen=500)

    async def log(self, event: TraceEvent) -> WriteReceipt:
        self._events.append(event)
        return WriteReceipt(event_id=event.trace_id, source="cache", status="pending")

    async def recent(self, limit: int = 100, run_id: str | None = None) -> list[TraceEvent]:
        events = reversed(self._events)
        if run_id:
            events = (event for event in events if event.run_id == run_id)
        return list(events)[:limit]


class DatabricksTraceSink:
    source = "databricks"

    def __init__(self, host: str, token: str, warehouse_id: str, table_name: str) -> None:
        if not _TABLE_NAME.fullmatch(table_name):
            raise ValueError("Databricks trace table must be a three-part catalog.schema.table name")
        self._client = WorkspaceClient(host=host, token=token)
        self._warehouse_id = warehouse_id
        self._table_name = table_name

    @staticmethod
    def _literal(value: object | None) -> str:
        if value is None:
            return "NULL"
        return "'" + str(value).replace("'", "''") + "'"

    async def _execute(self, statement: str):
        return await asyncio.to_thread(
            self._client.statement_execution.execute_statement,
            statement=statement,
            warehouse_id=self._warehouse_id,
            wait_timeout="30s",
        )

    async def log(self, event: TraceEvent) -> WriteReceipt:
        payload = json.dumps(event.payload, separators=(",", ":"), sort_keys=True)
        values = ", ".join(self._literal(value) for value in (
            event.trace_id, event.source, event.event_type, event.timestamp,
            event.run_id, event.objective_id, event.workstream_id, event.agent_id, payload,
        ))
        await self._execute(
            f"INSERT INTO {self._table_name} "
            "(trace_id, source, event_type, event_timestamp, run_id, objective_id, "
            "workstream_id, agent_id, payload_json) VALUES ("
            f"{values})"
        )
        return WriteReceipt(event_id=event.trace_id, source="databricks", status="written")

    async def recent(self, limit: int = 100, run_id: str | None = None) -> list[TraceEvent]:
        bounded_limit = min(max(limit, 1), 200)
        where = "" if run_id is None else f" WHERE run_id = {self._literal(run_id)}"
        response = await self._execute(
            "SELECT trace_id, source, event_type, CAST(event_timestamp AS STRING), run_id, "
            "objective_id, workstream_id, agent_id, payload_json "
            f"FROM {self._table_name}{where} ORDER BY event_timestamp DESC LIMIT {bounded_limit}"
        )
        result = getattr(response, "result", None)
        rows = getattr(result, "data_array", None) or []
        events: list[TraceEvent] = []
        for row in rows:
            payload = {}
            if row[8]:
                try:
                    payload = json.loads(row[8])
                except (TypeError, json.JSONDecodeError):
                    # Older externally-written rows can contain raw newlines or
                    # other non-JSON text. Keep the timeline readable instead of
                    # making the entire trace endpoint unavailable.
                    log.warning("Skipping malformed Databricks trace payload for %s", row[0])
                    payload = {"raw_payload": str(row[8]), "payload_parse_error": True}
            events.append(TraceEvent(
                trace_id=row[0], source=row[1], event_type=row[2], timestamp=_as_utc_iso(row[3]),
                run_id=row[4], objective_id=row[5], workstream_id=row[6], agent_id=row[7], payload=payload,
            ))
        return events


def build_trace_sink(settings: Settings) -> TraceSink:
    """Return Databricks only with complete explicit configuration; otherwise cache."""
    required = (
        settings.databricks_host,
        settings.databricks_token,
        settings.databricks_warehouse_id,
        settings.databricks_catalog,
        settings.databricks_schema,
    )
    if settings.trace_mode == "databricks" and all(required):
        return DatabricksTraceSink(
            settings.databricks_host,
            settings.databricks_token,
            settings.databricks_warehouse_id,
            settings.databricks_trace_table_name,
        )
    if settings.trace_mode == "databricks":
        log.warning("Databricks tracing requested without complete configuration; using cache traces")
    return CacheTraceSink()
