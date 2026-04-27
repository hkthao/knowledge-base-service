from __future__ import annotations

from functools import lru_cache

from fastembed import SparseTextEmbedding


@lru_cache(maxsize=1)
def _model() -> SparseTextEmbedding:
    return SparseTextEmbedding(model_name="Qdrant/bm25")


def encode(texts: list[str]) -> list[dict]:
    out: list[dict] = []
    for emb in _model().embed(texts):
        out.append({"indices": emb.indices.tolist(), "values": emb.values.tolist()})
    return out


def encode_one(text: str) -> dict:
    return encode([text])[0]
