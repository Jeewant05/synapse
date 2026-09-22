import json

from pydantic import TypeAdapter

from scripts.database import demo_state
from server.app.config import ROOT
from server.app.main import app
from server.app.models import OrchestrationState, VerificationResult, WorkspaceState, WriteReceipt


def main() -> None:
    directory = ROOT / "contracts"
    directory.mkdir(exist_ok=True)
    documents = {
        "openapi.json": app.openapi(),
        "schema.json": TypeAdapter(
            WorkspaceState | OrchestrationState | VerificationResult | WriteReceipt
        ).json_schema(),
        "example-state.json": demo_state().model_dump(mode="json"),
    }
    for name, document in documents.items():
        (directory / name).write_text(json.dumps(document, indent=2) + "\n")
    frozen_orchestration = directory / "orchestration-api-v1.json"
    if not frozen_orchestration.exists():
        orchestration_paths = {
            path: operation for path, operation in app.openapi()["paths"].items()
            if path.startswith("/api/")
        }
        frozen_orchestration.write_text(
            json.dumps({"version": "v1", "paths": orchestration_paths}, indent=2) + "\n"
        )
    print("Exported API, shared model schemas, and fixture payloads.")


if __name__ == "__main__":
    main()
