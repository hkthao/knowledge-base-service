"""Top-level /search orchestration: hybrid → rerank → graph expand → pack."""

from __future__ import annotations

from typing import Any

from ..log import get_logger
from ..stores import neo4j_store
from . import context_packer, graph_expand, hybrid_search, reranker

log = get_logger(__name__)

DEFAULT_COLLECTIONS = (
    "code_ts", "code_ts_desc",
    "code_cs", "code_cs_desc",
    "docs",
)


def run_search(
    query: str,
    *,
    collections: list[str] | None = None,
    top_k: int = 10,
    filters: dict | None = None,
    expand_graph: bool = True,
    rerank: bool = True,
) -> dict:
    used_collections = list(collections) if collections else list(DEFAULT_COLLECTIONS)

    # 1. Hybrid retrieval — pull more than top_k so rerank has a pool.
    candidates_top_k = top_k * 4 if rerank else top_k
    hits = hybrid_search.search(
        query, collections=used_collections,
        top_k=candidates_top_k, filters=filters,
    )

    # 2. Cross-encoder rerank (optional).
    if rerank and hits:
        try:
            hits = reranker.rerank(query, hits, top_k=top_k)
        except Exception as exc:
            log.warning("rerank_failed_falling_back", error=str(exc))
            hits = hits[:top_k]
    else:
        hits = hits[:top_k]

    # 3. Graph expansion per result.
    graph_context: dict[str, dict[str, Any]] = {}
    if expand_graph and hits:
        drv = neo4j_store.driver()
        for hit in hits:
            chunk_id = hit.get("chunk_id")
            if not chunk_id:
                continue
            try:
                graph_context[chunk_id] = graph_expand.expand(
                    drv,
                    chunk_id=chunk_id,
                    file_path=hit.get("file_path"),
                    qualified_name=hit.get("qualified_name"),
                )
            except Exception as exc:
                log.warning("graph_expand_failed", chunk_id=chunk_id, error=str(exc))

    return context_packer.pack(hits, graph_context_by_chunk_id=graph_context, query=query)


def lookup_by_qualified_name(qualified_name: str) -> dict | None:
    """Exact symbol lookup — used by /search/symbol.

    Filters out placeholder nodes (created by relations into not-yet-indexed
    symbols) so the real labeled entity is always returned when both exist.
    """
    drv = neo4j_store.driver()
    cypher = (
        "MATCH (n) WHERE n.qualified_name = $qn "
        "AND n.placeholder IS NULL AND size(labels(n)) > 0 "
        "RETURN n.chunk_id AS chunk_id, n.qualified_name AS qualified_name, "
        "       n.name AS symbol_name, n.file_path AS file_path, "
        "       n.line_start AS line_start, n.line_end AS line_end, "
        "       n.signature AS signature, n.docstring AS docstring, "
        "       labels(n) AS labels "
        "ORDER BY size(labels(n)) DESC LIMIT 1"
    )
    with drv.session() as session:
        record = session.run(cypher, qn=qualified_name).single()
        return dict(record) if record else None
