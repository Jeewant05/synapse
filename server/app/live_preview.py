"""Validation for the frontend agent's self-contained interactive preview."""

import re
from collections.abc import Iterable
from typing import Any

PREVIEW_PATH = "frontend/preview.html"
_BLOCKED_ELEMENTS = re.compile(r"<\s*(?:base|embed|iframe|object)\b", re.IGNORECASE)
_META_REFRESH = re.compile(
    r"<\s*meta\b[^>]*http-equiv\s*=\s*['\"]?refresh\b",
    re.IGNORECASE,
)


def render_agent_preview(_objective: str, artifacts: Iterable[Any]) -> str:
    """Return a validated agent-owned app for the sandboxed preview endpoint."""
    preview = next((item for item in artifacts if item.path == PREVIEW_PATH), None)
    if preview is None:
        raise ValueError(f"frontend agent must generate {PREVIEW_PATH}")

    html = preview.content.strip()
    lowered = html.lower()
    if not html or "<html" not in lowered or "<body" not in lowered:
        raise ValueError(f"{PREVIEW_PATH} must contain a complete HTML document")
    if "<script" not in lowered:
        raise ValueError(f"{PREVIEW_PATH} must include inline JavaScript for interaction")
    if _BLOCKED_ELEMENTS.search(html) or _META_REFRESH.search(html):
        raise ValueError(f"{PREVIEW_PATH} contains a blocked embedding or redirect element")

    return html
