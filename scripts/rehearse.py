"""Foundation smoke check, not the final three-minute product rehearsal."""

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=5) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        state = client.get("/api/state")
        state.raise_for_status()
        assert health.json()["status"] == "ok"
        assert len(state.json()["workstreams"]) == 2, "Run npm run seed first."
    print("Foundation smoke check passed. Coordination and live integrations are not built yet.")


if __name__ == "__main__":
    main()
