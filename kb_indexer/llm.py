from __future__ import annotations

import os
from typing import Protocol

import httpx

from .settings import settings


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class AnthropicClient:
    """Haiku 4.5 via the Messages API. Lazy import keeps anthropic an
    optional dependency for users running fully local."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key)
        self._model = model or settings.description_llm_model

    def complete(self, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


class OllamaLLMClient:
    """Generic Ollama text generation (e.g. Qwen2.5-7B local)."""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self._model = model or settings.description_llm_model
        self._base_url = (base_url or settings.ollama_url).rstrip("/")
        self._http = httpx.Client(timeout=120.0)

    def complete(self, prompt: str) -> str:
        resp = self._http.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


def make_llm() -> LLMClient:
    backend = settings.description_llm_backend
    if backend == "anthropic":
        return AnthropicClient()
    if backend == "ollama":
        return OllamaLLMClient()
    raise ValueError(f"Unknown description_llm_backend: {backend}")
