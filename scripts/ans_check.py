"""Live ANS diagnostic: resolve an ANSName and report what a verifier would decide.

    uv run python -m scripts.ans_check ans://v1.0.0.backend.synapse-vt.us
    uv run python -m scripts.ans_check            # every seeded agent

Read-only. It performs the same DNS and transparency-log steps the coordinator
performs on `join`, and prints the evidence rather than a bare yes/no, so a
failure says which step failed. Cross-check against `ans-cli resolve` and
`ans-cli badge`.
"""

import argparse
import asyncio
import sys

from scripts.database import ans_name_for
from server.app.ans.badge import BadgeError, BadgeVerifier
from server.app.ans.names import ANSName, InvalidANSName
from server.app.ans.resolver import DnsDiscovery, DnsUnavailable, NotAnAnsAgent
from server.app.config import Settings

SEEDED = ["backend-agent", "frontend-agent", "telemetry-agent"]


async def check(raw: str, settings: Settings) -> bool:
    print(f"\n{raw}")
    try:
        name = ANSName.parse(raw)
    except InvalidANSName as exc:
        print(f"  ✗ not a valid ANSName: {exc}")
        return False
    print(f"  host={name.host}  version={name.version}")

    discovery = DnsDiscovery(nameservers=settings.dns_nameservers)

    try:
        for record in await discovery.ans_records(name.host):
            print(f"  ✓ _ans           {record.protocol or '?'} -> {record.url or '?'}")
    except (NotAnAnsAgent, DnsUnavailable) as exc:
        print(f"  ✗ _ans           {exc}")

    try:
        url = await discovery.badge_url_for(name.host, name.version)
        print(f"  ✓ _ans-badge     {url}")
    except (NotAnAnsAgent, DnsUnavailable) as exc:
        print(f"  ✗ _ans-badge     {exc}")
        return False

    badges = BadgeVerifier(trusted_hosts=settings.trusted_tl_hosts)
    try:
        badge = await badges.fetch(url)
    except BadgeError as exc:
        print(f"  ✗ badge          {exc}")
        return False

    print(f"  {'✓' if badge.is_live else '✗'} badge          status={badge.status or 'unknown'}")
    print(f"    ansName        {badge.ans_name or '(absent)'}")
    print(f"    host           {badge.host or '(absent)'}")
    print(f"    identityCerts  {len(badge.identity_cert_fingerprints)} sealed fingerprint(s)")
    if not badge.identity_cert_fingerprints:
        print("    NOTE: no identity certificates in the badge -- every caller will be")
        print("          refused. Pin the field path in server/app/ans/badge.py against")
        print("          `ans-cli badge <agentId> --json` if the shape differs.")
    return badge.is_live


async def main_async(names: list[str]) -> int:
    settings = Settings()
    if not names:
        if not settings.ans_domain:
            sys.exit("Pass an ANSName, or set ANS_DOMAIN in .env.")
        names = [ans_name_for(agent_id, settings.ans_domain) for agent_id in SEEDED]
    print(f"Trusted transparency-log hosts: {', '.join(sorted(settings.trusted_tl_hosts))}")
    results = [await check(name, settings) for name in names]
    ok = sum(results)
    print(f"\n{ok}/{len(results)} live")
    return 0 if ok == len(results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ansname", nargs="*", help="ANSNames to check; default is the seeded set")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args.ansname)))


if __name__ == "__main__":
    main()
