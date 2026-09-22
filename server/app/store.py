"""Minimal durable fixture store. Coordinator persistence follows in milestone two."""

import sqlite3
from contextlib import closing
from pathlib import Path

from server.app.models import OrchestrationState, WorkspaceState


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY, data TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS orchestration (id INTEGER PRIMARY KEY, data TEXT)")
    return connection


def read_state(path: Path) -> WorkspaceState:
    with closing(connect(path)) as connection:
        row = connection.execute("SELECT data FROM workspace WHERE id = 1").fetchone()
    return WorkspaceState.model_validate_json(row[0]) if row else WorkspaceState()


def write_state(path: Path, state: WorkspaceState) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO workspace (id, data) VALUES (1, ?)",
            (state.model_dump_json(),),
        )


def read_orchestration_state(path: Path) -> OrchestrationState:
    with closing(connect(path)) as connection:
        row = connection.execute("SELECT data FROM orchestration WHERE id = 1").fetchone()
    return OrchestrationState.model_validate_json(row[0]) if row else OrchestrationState()


def write_orchestration_state(path: Path, state: OrchestrationState) -> None:
    with closing(connect(path)) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO orchestration (id, data) VALUES (1, ?)",
            (state.model_dump_json(),),
        )
