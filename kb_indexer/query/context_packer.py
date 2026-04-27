"""Format the final /search response.

Centralises the schema so endpoints + reranker + graph-expand don't each
shape the JSON differently. Schema follows plan §9: each result row has
the chunk identity + score + provenance + optional `graph_context`.
"""

from __future__ import annotations

from typing import Any


def pack(
    hits: list[dict[str, Any]],
    *,
    graph_context_by_chunk_id: dict[str, dict] | None = None,
    query: str | None = None,
) -> dict:
    graph_context_by_chunk_id = graph_context_by_chunk_id or {}
    results = []
    for hit in hits:
        chunk_id = hit.get("chunk_id")
        result = {
            "chunk_id": chunk_id,
            "qualified_name": hit.get("qualified_name"),
            "symbol_name": hit.get("symbol_name"),
            "symbol_type": hit.get("symbol_type"),
            "language": hit.get("language"),
            "content": hit.get("content"),
            "title": hit.get("title"),  # docs hits
            "file_path": hit.get("file_path"),
            "line_start": hit.get("line_start"),
            "line_end": hit.get("line_end"),
            "repo": hit.get("repo"),
            "source_type": hit.get("source_type"),
            "source_reliability": hit.get("source_reliability"),
            "matched_via": hit.get("matched_via"),
            "matched_collection": hit.get("matched_collection"),
            "score": hit.get("score"),
            "rerank_score": hit.get("rerank_score"),
        }
        if chunk_id and chunk_id in graph_context_by_chunk_id:
            result["graph_context"] = graph_context_by_chunk_id[chunk_id]
        # Drop None fields so the JSON is tight.
        results.append({k: v for k, v in result.items() if v is not None})

    return {"query": query, "results": results}
