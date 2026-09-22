"""Collision rules. Pure functions, fixed rules, no LLM. Owner: P1.

Rule 1 (file):     two workstreams own an overlapping path pattern.
Rule 2 (contract): same method + path, one provides and one consumes, response field names differ.
"""

from itertools import combinations
from pathlib import PurePosixPath

from server.app.models import Conflict, Workstream


def paths_overlap(a: str, b: str) -> bool:
    """Glob-aware overlap: 'src/api/**' overlaps 'src/api/auth/**' and 'src/api/x.py'."""
    if a == b:
        return True
    a_base, b_base = a.rstrip("*/"), b.rstrip("*/")
    if a.endswith("**") and b_base.startswith(a_base):
        return True
    if b.endswith("**") and a_base.startswith(b_base):
        return True
    return PurePosixPath(a).match(b) or PurePosixPath(b).match(a)


def file_conflicts(workstreams: list[Workstream]) -> list[Conflict]:
    found: list[Conflict] = []
    for x, y in combinations(workstreams, 2):
        hits = [
            (p, q) for p in x.owned_paths for q in y.owned_paths if paths_overlap(p, q)
        ]
        if hits:
            p, q = hits[0]
            found.append(
                Conflict(
                    id=f"file:{x.id}:{y.id}",
                    type="file",
                    workstream_ids=[x.id, y.id],
                    explanation=f"{x.id} owns {p} and {y.id} owns {q}; these overlap.",
                    conflicting_field=p,
                    recommendation=f"Split ownership so only one workstream touches {p}.",
                )
            )
    return found


def contract_conflicts(workstreams: list[Workstream]) -> list[Conflict]:
    found: list[Conflict] = []
    for x, y in combinations(workstreams, 2):
        cx, cy = x.contract, y.contract
        if (cx.method, cx.path) != (cy.method, cy.path):
            continue
        if {cx.role, cy.role} != {"provides", "consumes"}:
            continue
        provider, consumer = (x, y) if cx.role == "provides" else (y, x)
        missing = sorted(
            set(consumer.contract.response_fields) - set(provider.contract.response_fields)
        )
        if not missing:
            continue
        field = missing[0]
        found.append(
            Conflict(
                id=f"contract:{provider.id}:{consumer.id}",
                type="contract",
                workstream_ids=[provider.id, consumer.id],
                explanation=(
                    f"{consumer.id} expects {cx.method} {cx.path} to return "
                    f"{sorted(consumer.contract.response_fields)} but {provider.id} "
                    f"provides {sorted(provider.contract.response_fields)}."
                ),
                conflicting_field=field,
                recommendation=(
                    f"{consumer.id} must redeclare to consume "
                    f"{sorted(provider.contract.response_fields)}, "
                    f"or {provider.id} must add {field}."
                ),
            )
        )
    return found


def find_conflicts(workstreams: list[Workstream]) -> list[Conflict]:
    return file_conflicts(workstreams) + contract_conflicts(workstreams)
