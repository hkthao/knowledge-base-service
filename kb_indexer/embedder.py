from __future__ import annotations

from typing import Protocol

import httpx

from .settings import settings


class DenseEmbedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Local embedder via Ollama (`/api/embeddings`)."""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.model = model or settings.ollama_code_model
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self._client = httpx.Client(timeout=120.0)
        self.dim = settings.dense_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            resp.raise_for_status()
            out.append(resp.json()["embedding"])
        return out


class VoyageEmbedder:
    """Initial-index embedder — much faster than Ollama on CPU."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or settings.voyage_api_key
        if not api_key:
            raise RuntimeError("VOYAGE_API_KEY is not set")
        import voyageai  # noqa: WPS433  (lazy import — optional dep at runtime)
        self._client = voyageai.Client(api_key=api_key)
        self.model = model or settings.voyage_code_model
        self.dim = settings.dense_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        # voyage limits batch to 128
        out: list[list[float]] = []
        for i in range(0, len(texts), 128):
            batch = texts[i:i + 128]
            res = self._client.embed(batch, model=self.model, input_type="document")
            out.extend(res.embeddings)
        return out


def make_embedder(prefer: str = "auto") -> DenseEmbedder:
    """`prefer`: 'auto' | 'voyage' | 'ollama'.

    'auto' picks voyage if API key set, else Ollama.
    """
    if prefer == "voyage":
        return VoyageEmbedder()
    if prefer == "ollama":
        return OllamaEmbedder()
    if settings.voyage_api_key:
        return VoyageEmbedder()
    return OllamaEmbedder()
