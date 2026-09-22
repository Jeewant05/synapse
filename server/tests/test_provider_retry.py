"""Providers retry the failures that are the provider's load, and only those.

Gemini's "high demand" 503 lasts seconds. Without a retry, one such answer failed a
whole three-agent run on the live site even though the very next request worked.
"""

import asyncio

import httpx
import pytest

from server.app import providers
from server.app.providers import GeminiProvider, OpenAICompatibleProvider, ProviderError

GOOD_GEMINI = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}


class Scripted:
    """Stands in for the network: answers with a queued status per request."""

    def __init__(self, statuses, body=None):
        self.statuses = list(statuses)
        self.body = body if body is not None else GOOD_GEMINI
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        if status == 200:
            return httpx.Response(200, json=self.body)
        return httpx.Response(status, json={"error": {"message": "high demand"}})


@pytest.fixture
def no_sleep(monkeypatch):
    waits: list[float] = []

    async def fake(delay):
        waits.append(delay)

    monkeypatch.setattr(providers.asyncio, "sleep", fake)
    return waits


def patch_transport(monkeypatch, script: Scripted):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(script.handler), **kw),
    )


def gemini():
    return GeminiProvider("key", "gemini-3.6-flash", "https://example.test/v1beta")


def test_a_transient_503_is_retried_and_then_succeeds(monkeypatch, no_sleep):
    script = Scripted([503, 503])
    patch_transport(monkeypatch, script)

    assert asyncio.run(gemini().generate("hi")) == '{"ok": true}'
    assert script.calls == 3
    assert no_sleep == [1, 2]  # backs off rather than hammering


def test_a_persistent_503_gives_up_after_a_bounded_number_of_attempts(monkeypatch, no_sleep):
    script = Scripted([503] * 10)
    patch_transport(monkeypatch, script)

    with pytest.raises(ProviderError, match="gemini 503"):
        asyncio.run(gemini().generate("hi"))
    assert script.calls == providers.RETRY_ATTEMPTS


def test_a_bad_request_is_not_retried(monkeypatch, no_sleep):
    """A 4xx means the request is wrong; retrying only spends quota."""
    script = Scripted([400])
    patch_transport(monkeypatch, script)

    with pytest.raises(ProviderError, match="gemini 400"):
        asyncio.run(gemini().generate("hi"))
    assert script.calls == 1 and no_sleep == []


def test_rate_limits_are_retried_too(monkeypatch, no_sleep):
    script = Scripted([429])
    patch_transport(monkeypatch, script)
    assert asyncio.run(gemini().generate("hi")) == '{"ok": true}'
    assert script.calls == 2


def test_the_openai_compatible_provider_shares_the_same_retry(monkeypatch, no_sleep):
    body = {"choices": [{"message": {"content": "done"}}]}
    script = Scripted([502], body=body)
    patch_transport(monkeypatch, script)

    provider = OpenAICompatibleProvider("huggingface", "k", "m", "https://example.test/v1")
    assert asyncio.run(provider.generate("hi")) == "done"
    assert script.calls == 2
