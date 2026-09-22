"""Drive ANS registration for the Synapse agents through `ans-cli`.

A thin, auditable wrapper: every step shells out to the vendor CLI rather than
reimplementing registration, and each agent's outcome is recorded in
`.local/ans/<agent>/agent.json` so the seed fixture and the registry cannot drift.

The DNS steps are deliberately manual. Records live in the Porkbun zone while the
registry is GoDaddy's, so the script prints exactly what to publish and waits to
be told to continue rather than pretending it can write the zone.

    uv run python scripts/ans_register.py generate     # keys + CSRs
    uv run python scripts/ans_register.py register     # -> agentId + ACME challenge
    uv run python scripts/ans_register.py records      # what to publish, per step
    uv run python scripts/ans_register.py acme         # after _acme-challenge is live
    uv run python scripts/ans_register.py dns          # after _ans/_ans-badge are live
    uv run python scripts/ans_register.py status
    uv run python scripts/ans_register.py certs        # identity cert + chain

Add --agent <id> to act on one agent; the default is all of them.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from server.app.config import ROOT, Settings

MATERIAL_ROOT = ROOT / ".local" / "ans"
VERSION = "1.0.0"
ORG = "Synapse"

# subdomain label, or "" for the zone apex.
AGENTS: dict[str, dict[str, str]] = {
    "backend-agent": {"label": "backend", "name": "Synapse Backend Agent"},
    "frontend-agent": {"label": "frontend", "name": "Synapse Frontend Agent"},
    "telemetry-agent": {"label": "telemetry", "name": "Synapse Telemetry Agent"},
    "coordinator": {"label": "", "name": "Synapse Coordinator"},
}


def host_for(agent_id: str, domain: str) -> str:
    label = AGENTS[agent_id]["label"]
    return f"{label}.{domain}" if label else domain


def ans_name_for(agent_id: str, domain: str) -> str:
    return f"ans://v{VERSION}.{host_for(agent_id, domain)}"


def cli_env(settings: Settings) -> dict[str, str]:
    """ans-cli wants the combined key:secret and an explicit production base URL."""
    credential = settings.ans_credential
    if not credential:
        sys.exit("Set ANS_API_KEY and ANS_API_SECRET in .env first.")
    env = dict(os.environ)
    env["ANS_API_KEY"] = credential
    env["ANS_BASE_URL"] = settings.ans_base_url
    env.pop("ANS_API_SECRET", None)  # the CLI does not read it; avoid confusion
    return env


def run(args: list[str], env: dict[str, str], capture_json: bool = False):
    printable = " ".join(a for a in args)
    print(f"  $ {printable}")
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout.strip())
        print(result.stderr.strip(), file=sys.stderr)
        return None
    if not capture_json:
        print("    " + result.stdout.strip().replace("\n", "\n    "))
        return result.stdout
    try:
        return json.loads(result.stdout)
    except ValueError:
        # --json is best effort; keep the human output so nothing is lost.
        print("    " + result.stdout.strip().replace("\n", "\n    "))
        return None


def material(agent_id: str) -> Path:
    path = MATERIAL_ROOT / agent_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def record(agent_id: str, **fields) -> dict:
    path = material(agent_id) / "agent.json"
    data = json.loads(path.read_text()) if path.is_file() else {}
    data.update({k: v for k, v in fields.items() if v is not None})
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def stored(agent_id: str) -> dict:
    path = MATERIAL_ROOT / agent_id / "agent.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def agent_id_of(agent_id: str) -> str | None:
    value = stored(agent_id).get("agentId")
    if not value:
        print(f"  {agent_id}: no agentId recorded yet -- run `register` first.")
    return value


# --- steps -----------------------------------------------------------------


def step_generate(agent_id: str, domain: str, env: dict[str, str]) -> None:
    out = material(agent_id)
    run(
        [
            "ans-cli", "generate-csr",
            "--host", host_for(agent_id, domain),
            "--org", ORG,
            "--version", VERSION,
            "--out-dir", str(out),
        ],
        env,
    )
    record(agent_id, host=host_for(agent_id, domain), ansName=ans_name_for(agent_id, domain))


def step_register(agent_id: str, domain: str, env: dict[str, str], with_server_cert: bool = False) -> None:
    out = material(agent_id)
    host = host_for(agent_id, domain)
    payload = run(
        [
            "ans-cli", "register",
            "--name", AGENTS[agent_id]["name"],
            "--host", host,
            "--version", VERSION,
            "--description", f"Synapse pre-merge coordination: {agent_id}",
            "--identity-csr", str(out / "identity.csr"),
            # HTTP-API, not MCP: no MCP transport is exposed, so declaring one
            # would seal a false claim into the transparency log.
            "--endpoint-url", f"https://{host}/api",
            "--metadata-url", f"https://{host}/.well-known/agent-card.json",
            "--endpoint-protocol", "HTTP-API",
            "--function", "declare-contract:Declare API Contract:coordination",
            "--function", "submit-changeset:Submit ChangeSet:coordination",
            "--json",
            # Identity certificate only by default. The platform issues and
            # rotates the TLS certificate we actually serve, so registering a
            # serverCerts[] fingerprint we never present would make every callee
            # verification fail -- worse than publishing none. Pass
            # --with-server-cert only where we control TLS termination.
            *(["--server-csr", str(out / "server.csr")] if with_server_cert else []),
        ],
        env,
        capture_json=True,
    )
    if payload is None:
        return
    record(
        agent_id,
        host=host,
        ansName=payload.get("ansName") or ans_name_for(agent_id, domain),
        agentId=payload.get("agentId") or payload.get("id"),
        registration=payload,
    )
    print(f"    recorded agentId={stored(agent_id).get('agentId')}")


def step_records(agent_id: str, domain: str, env: dict[str, str]) -> None:
    """Print the DNS records to publish, straight from the registration response."""
    data = stored(agent_id)
    host = data.get("host") or host_for(agent_id, domain)
    print(f"\n  {agent_id}  ({host})")
    registration = data.get("registration") or {}
    blob = json.dumps(registration)
    if not registration:
        print("    nothing recorded yet -- run `register` first.")
        return
    # The response shape is not pinned yet, so surface anything record-shaped
    # rather than guessing one path and silently printing nothing.
    for key in ("dnsRecords", "records", "challenges", "acmeChallenge", "dnsRecordsToPublish"):
        if key in registration:
            print(f"    {key}: {json.dumps(registration[key], indent=6)}")
    if "_acme-challenge" in blob or "acme" in blob.lower():
        print(f"    (raw registration response saved in .local/ans/{agent_id}/agent.json)")


def step_acme(agent_id: str, domain: str, env: dict[str, str]) -> None:
    ident = agent_id_of(agent_id)
    if ident:
        run(["ans-cli", "verify-acme", ident], env)


def step_dns(agent_id: str, domain: str, env: dict[str, str]) -> None:
    ident = agent_id_of(agent_id)
    if ident:
        run(["ans-cli", "verify-dns", ident], env)


def step_status(agent_id: str, domain: str, env: dict[str, str]) -> None:
    ident = agent_id_of(agent_id)
    if ident:
        run(["ans-cli", "status", ident], env)


def step_certs(agent_id: str, domain: str, env: dict[str, str]) -> None:
    ident = agent_id_of(agent_id)
    if not ident:
        return
    payload = run(["ans-cli", "get-identity-certs", ident, "--json"], env, capture_json=True)
    if payload is None:
        return
    (material(agent_id) / "identity-certs.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"    saved .local/ans/{agent_id}/identity-certs.json")
    print("    write the leaf PEM to identity.crt for the scripted agents to use")


STEPS = {
    "generate": step_generate,
    "register": step_register,
    "records": step_records,
    "acme": step_acme,
    "dns": step_dns,
    "status": step_status,
    "certs": step_certs,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--agent", choices=sorted(AGENTS), action="append")
    parser.add_argument(
        "--with-server-cert", action="store_true",
        help="also submit the server CSR; only where we control TLS termination",
    )
    args = parser.parse_args()

    if shutil.which("ans-cli") is None:
        sys.exit("ans-cli not found. brew install agentnameservice/ans/ans-cli")

    settings = Settings()
    if not settings.ans_domain:
        sys.exit("Set ANS_DOMAIN in .env first.")
    env = cli_env(settings)

    targets = args.agent or list(AGENTS)
    print(f"ANS {args.step}: {', '.join(targets)} under {settings.ans_domain}")
    for agent_id in targets:
        print(f"\n[{agent_id}]")
        if args.step == "register":
            step_register(agent_id, settings.ans_domain, env, args.with_server_cert)
        else:
            STEPS[args.step](agent_id, settings.ans_domain, env)


if __name__ == "__main__":
    main()
