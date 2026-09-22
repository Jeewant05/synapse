"""Minimal Porkbun DNS client for publishing ANS records.

ANS registration needs two TXT records per agent, published after domain
validation and before `verify-dns`. Doing that by hand across four agents is
eight records in separate rounds, with values containing semicolons and equals
signs that are easy to mistype -- so it is scripted.

    uv run python -m scripts.porkbun list
    uv run python -m scripts.porkbun upsert TXT _ans.backend "v=ans1; ..."
    uv run python -m scripts.porkbun delete TXT _ans.backend

Credentials come from PORKBUN_API_KEY and PORKBUN_SECRET_KEY in .env. Names are
given relative to the zone, exactly as Porkbun's own editor expects.
"""

import argparse
import sys
from typing import Any

import httpx

from server.app.config import Settings

BASE = "https://api.porkbun.com/api/json/v3"


class PorkbunError(Exception):
    """The API rejected the call."""


def _auth(settings: Settings) -> dict[str, str]:
    key, secret = settings.porkbun_api_key, settings.porkbun_secret_key
    if not key or not secret:
        raise PorkbunError("set PORKBUN_API_KEY and PORKBUN_SECRET_KEY in .env")
    return {"apikey": key, "secretapikey": secret}


def _post(settings: Settings, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {**_auth(settings), **(payload or {})}
    response = httpx.post(f"{BASE}/{path.lstrip('/')}", json=body, timeout=30)
    data = response.json()
    if data.get("status") != "SUCCESS":
        raise PorkbunError(data.get("message") or f"HTTP {response.status_code}")
    return data


def records(settings: Settings, domain: str) -> list[dict[str, Any]]:
    return _post(settings, f"dns/retrieve/{domain}")["records"]


def upsert(settings: Settings, domain: str, rtype: str, name: str, content: str, ttl: int = 600):
    """Create the record, replacing any existing one of the same type and name."""
    fqdn = f"{name}.{domain}" if name else domain
    for existing in records(settings, domain):
        if existing["type"] == rtype and existing["name"] == fqdn:
            _post(settings, f"dns/delete/{domain}/{existing['id']}")
            print(f"  replaced existing {rtype} {fqdn}")
    payload = {"type": rtype, "content": content, "ttl": str(ttl)}
    if name:
        payload["name"] = name
    result = _post(settings, f"dns/create/{domain}", payload)
    print(f"  + {rtype} {fqdn} (id {result.get('id')})")
    return result


def delete(settings: Settings, domain: str, rtype: str, name: str) -> int:
    fqdn = f"{name}.{domain}" if name else domain
    removed = 0
    for existing in records(settings, domain):
        if existing["type"] == rtype and existing["name"] == fqdn:
            _post(settings, f"dns/delete/{domain}/{existing['id']}")
            print(f"  - {rtype} {fqdn}")
            removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["list", "upsert", "delete"])
    parser.add_argument("type", nargs="?")
    parser.add_argument("name", nargs="?", default="")
    parser.add_argument("content", nargs="?")
    parser.add_argument("--domain")
    parser.add_argument("--ttl", type=int, default=600)
    args = parser.parse_args()

    settings = Settings()
    domain = args.domain or settings.ans_domain
    if not domain:
        sys.exit("set ANS_DOMAIN in .env or pass --domain")

    try:
        if args.action == "list":
            for r in records(settings, domain):
                print(f"  {r['type']:6} {r['name']:44} {r['content']}")
        elif args.action == "upsert":
            if not args.type or args.content is None:
                sys.exit("usage: upsert <TYPE> <name> <content>")
            upsert(settings, domain, args.type, args.name, args.content, args.ttl)
        else:
            if not delete(settings, domain, args.type, args.name):
                print("  (nothing matched)")
    except PorkbunError as exc:
        sys.exit(f"Porkbun: {exc}")


if __name__ == "__main__":
    main()
