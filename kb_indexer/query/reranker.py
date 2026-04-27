"""Cross-encoder reranking on top of hybrid-search hits.

`ms-marco-MiniLM-L-6-v2` is small (~80MB) and fast on CPU. Lazy-import +
process-singleton so the import cost is paid once and only when reranking
is actually requested.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

_MODEL = None
_MODEL_LOCK = Lock()


def _model():
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                from sentence_transformers import CrossEncoder
                _MODEL = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _MODEL


def rerank(query: str, hits: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    if not hits:
        return hits
    pairs = [[query, hit.get("content", "") or ""] for hit in hits]
    scores = _model().predict(pairs, show_progress_bar=False)
    for hit, score in zip(hits, scores):
        hit["rerank_score"] = float(score)
    hits.sort(key=lambda h: h.get("rerank_score", 0.0), reverse=True)
    return hits[:top_k]
