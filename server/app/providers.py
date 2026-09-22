"""LLM providers for live agents. One provider per agent, chosen by settings.

Two wire formats:
  - Gemini REST (generateContent)
  - OpenAI-compatible chat completions (Hugging Face, Cerebras, Groq, GitHub, and OpenAI)

Providers return text only. File changes are applied by the coordinator after validation.
"""

import asyncio
from typing import Any, Protocol

import httpx

from server.app.config import Settings


class ProviderError(RuntimeError):
    """Raised when a provider is misconfigured or rejects a request."""


class Provider(Protocol):
    name: str
    model: str

    async def generate(self, prompt: str) -> str: ...


RETRY_ATTEMPTS = 4
RETRY_MAX_DELAY = 8.0


async def post_with_retry(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str], payload: dict[str, Any]
) -> httpx.Response:
    """POST, retrying the failures that are the provider's load rather than our request.

    429 and 5xx clear on their own -- Gemini's "high demand" 503 lasts seconds --
    while any other 4xx means the request itself is wrong and retrying only spends
    quota. Honours Retry-After, otherwise backs off 1s, 2s, 4s.
    """
    response: httpx.Response | None = None
    for attempt in range(RETRY_ATTEMPTS):
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 429 and response.status_code < 500:
            return response
        if attempt < RETRY_ATTEMPTS - 1:
            retry_after = response.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2**attempt
            await asyncio.sleep(min(delay, RETRY_MAX_DELAY))
    if response is None:  # RETRY_ATTEMPTS is positive, so this is unreachable
        raise ProviderError("provider request was never sent")
    return response


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str):
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not set")
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")

    async def generate(self, prompt: str) -> str:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await post_with_retry(
                client, url, headers={"x-goog-api-key": self.api_key}, payload=payload
            )
        if response.is_error:
            raise ProviderError(f"gemini {response.status_code}: {response.text[:300]}")
        data = response.json()
        try:
            return "".join(p["text"] for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"gemini returned no text: {data}") from exc


class OpenAICompatibleProvider:
    """Providers using the OpenAI-compatible /chat/completions shape."""

    def __init__(self, name: str, api_key: str, model: str, base_url: str):
        if not api_key:
            raise ProviderError(f"{name.upper()}_API_KEY is not set")
        self.name, self.api_key, self.model = name, api_key, model
        self.base_url = base_url.rstrip("/")

    async def generate(self, prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await post_with_retry(client, url, headers=headers, payload=payload)
        if response.is_error:
            raise ProviderError(f"{self.name} {response.status_code}: {response.text[:300]}")
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.name} returned no text: {data}") from exc


# vendor -> (default base_url, default model)
OPENAI_COMPATIBLE = {
    "arc": ("https://llm-api.arc.vt.edu/api/v1", "gpt-oss-120b"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "github": ("https://models.inference.ai.azure.com", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


def build_provider(vendor: str, settings: Settings, role: str = "") -> Provider:
    vendor = vendor.lower()
    if vendor == "gemini":
        # Per-role key (GEMINI_API_KEY_BACKEND etc.) wins; falls back to GEMINI_API_KEY.
        key = getattr(settings, f"gemini_api_key_{role}", None) or settings.gemini_api_key or ""
        return GeminiProvider(key, settings.gemini_model, settings.gemini_base_url)
    if vendor == "huggingface":
        key = settings.huggingface_api_key or settings.hf_token or ""
        role_model = getattr(settings, f"huggingface_model_{role}", None)
        model = role_model or settings.huggingface_model
        return OpenAICompatibleProvider("huggingface", key, model, settings.huggingface_base_url)
    if vendor in OPENAI_COMPATIBLE:
        default_url, default_model = OPENAI_COMPATIBLE[vendor]
        key = getattr(settings, f"{vendor}_api_key_{role}", None) or getattr(settings, f"{vendor}_api_key", "") or ""
        model = getattr(settings, f"{vendor}_model_{role}", None) or getattr(settings, f"{vendor}_model", "") or default_model
        base_url = getattr(settings, f"{vendor}_base_url", "") or default_url
        return OpenAICompatibleProvider(vendor, key, model, base_url)
    raise ProviderError(f"unknown provider {vendor!r}")


def build_agent_providers(settings: Settings) -> dict[str, Provider]:
    """Build configured role providers; invalid or incomplete roles remain unavailable."""
    wanted = {
        "backend": settings.backend_provider,
        "frontend": settings.frontend_provider,
        "qa": settings.qa_provider,
    }
    built: dict[str, Provider] = {}
    for role, vendor in wanted.items():
        if not vendor or vendor == "none":
            continue
        try:
            built[role] = build_provider(vendor, settings, role)
        except ProviderError:
            continue
    return built
