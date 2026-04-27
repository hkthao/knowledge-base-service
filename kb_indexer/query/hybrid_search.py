"""Hybrid (dense + BM25 + RRF) search across one or more Qdrant collections.

This is the retrieval layer that `/search` sits on top of. It does NOT
do graph expansion or reranking — those live in their own modules. Keep
each layer single-responsibility so they're easy to test in isolation.

Embedding choice:
- `code_*` and `code_*_desc` collections were embedded with different models
  (code vs. text). When searching them simultaneously we can't use a single
  query vector dimension if those dims differ. They DO differ — nomic-embed-code
  outputs different vectors than nomic-embed-text. We sidestep this by
  embedding the user query with each collection's embedder.
- For now we use the code embedder for code_* and the text embedder for
  description / docs collections. Both models output 768-dim by default.
"""

from __future__ import annotations

from typing import Any

from .. import bm25_encoder
from ..embedder import OllamaEmbedder, make_embedder
from ..settings import settings
from ..stores import qdrant_store
from .cross_collection import merge_collection_hits
from .filters import build_filter

# Description and doc collections embed natural language — text model.
_TEXT_COLLECTIONS = {"code_ts_desc", "code_cs_desc", "docs", "issues"}


def search(
    query: str,
    *,
    collections: list[str],
    top_k: int = 10,
    filters: dict | None = None,
    qc=None,
) -> list[dict[str, Any]]:
    """Run hybrid search on each collection then RRF-merge across them."""
    qc = qc or qdrant_store.client()
    qfilter = build_filter(filters)

    # Embedders — code embedder for code collections, text for desc/docs.
    code_embedder = None
    text_embedder = None
    sparse = bm25_encoder.encode_one(query)

    hits_by_collection: dict[str, list[dict[str, Any]]] = {}
    # Per-collection hit count: we ask for more than top_k since RRF merge
    # benefits from having tail signal; final cut happens after merge.
    fetch_k = max(30, top_k * 3)

    for collection in collections:
        if collection in _TEXT_COLLECTIONS:
            if text_embedder is None:
                text_embedder = OllamaEmbedder(model=settings.ollama_text_model)
            dense = text_embedder.embed([query])[0]
        else:
            if code_embedder is None:
                code_embedder = make_embedder()
            dense = code_embedder.embed([query])[0]

        try:
            hits = qdrant_store.hybrid_search(
                qc, collection,
                dense_query=dense,
                bm25_query=sparse,
                top_k=fetch_k,
                qfilter=qfilter,
            )
        except Exception:
            # Collection might not exist yet (e.g. C# repo not indexed) —
            # skip rather than fail the whole search.
            continue
        hits_by_collection[collection] = hits

    merged = merge_collection_hits(hits_by_collection)
    return merged[:top_k]
