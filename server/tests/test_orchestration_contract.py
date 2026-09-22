"""Keep the frontend-facing orchestration response contract stable during the demo."""

import json

from server.app.config import ROOT
from server.app.main import app


def test_orchestration_api_v1_snapshot_is_unchanged():
    snapshot = json.loads((ROOT / "contracts" / "orchestration-api-v1.json").read_text())
    # Selected by tag, not by the /api prefix: the coordinator router now shares
    # that prefix, and this snapshot freezes the orchestration contract only.
    # Filtering by tag still fails if an orchestration path is added or changed.
    current = {
        path: operations
        for path, operations in app.openapi()["paths"].items()
        if any("orchestration" in op.get("tags", []) for op in operations.values())
    }
    assert snapshot == {"version": "v1", "paths": current}
