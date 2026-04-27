"""Graph context expansion for search hits.

For each retrieval hit we attach a `graph_context` payload with:
- callers / callees    (1-2 hops along CALLS, with confidence)
- co_changed           (Module-level pairs from git history)
- recent_commits       (last N commits TOUCHED_BY the file)
- related_issues       (Issue → REFERENCES → this symbol)

Symbol-level expansions key off `chunk_id`; file-level expansions
(co-change, recent commits) key off `file_path`. We do one Cypher query
per expansion type per hit — round-trip cost is fine for top_k=10 and
keeps the query strings simple.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from ..stores import neo4j_store


def expand(
    drv: Driver,
    *,
    chunk_id: str,
    file_path: str | None,
    qualified_name: str | None = None,
    repo_root: str | None = None,
    max_hops: int = 2,
    co_change_limit: int = 8,
    commit_limit: int = 5,
) -> dict[str, Any]:
    return {
        "callers": _callers(drv, chunk_id, max_hops),
        "callees": _callees(drv, chunk_id, max_hops),
        "co_changed": _co_changed(drv, file_path, co_change_limit) if file_path else [],
        "recent_commits": _recent_commits(drv, file_path, commit_limit) if file_path else [],
        "related_issues": _related_issues(drv, chunk_id),
    }


def _callers(drv: Driver, chunk_id: str, max_hops: int) -> list[dict]:
    cypher = (
        f"MATCH path = (caller)-[r:CALLS*1..{max_hops}]->(fn) "
        "WHERE fn.chunk_id = $cid AND caller.chunk_id <> $cid "
        "AND caller.placeholder IS NULL "
        "WITH caller, length(path) AS hops, "
        "     reduce(c = 1.0, rel IN relationships(path) | c * coalesce(rel.confidence, 0.5)) AS confidence "
        "RETURN DISTINCT caller.qualified_name AS qualified_name, "
        "       caller.file_path AS file_path, "
        "       caller.line_start AS line_start, "
        "       hops, confidence "
        "ORDER BY hops, confidence DESC "
        "LIMIT 25"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, cid=chunk_id)]


def _callees(drv: Driver, chunk_id: str, max_hops: int) -> list[dict]:
    cypher = (
        f"MATCH path = (fn)-[r:CALLS*1..{max_hops}]->(callee) "
        "WHERE fn.chunk_id = $cid AND callee.chunk_id <> $cid "
        "AND callee.placeholder IS NULL "
        "WITH callee, length(path) AS hops, "
        "     reduce(c = 1.0, rel IN relationships(path) | c * coalesce(rel.confidence, 0.5)) AS confidence "
        "RETURN DISTINCT callee.qualified_name AS qualified_name, "
        "       callee.file_path AS file_path, "
        "       callee.line_start AS line_start, "
        "       hops, confidence "
        "ORDER BY hops, confidence DESC "
        "LIMIT 25"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, cid=chunk_id)]


def _co_changed(drv: Driver, file_path: str, limit: int) -> list[dict]:
    cypher = (
        "MATCH (m:Module {qualified_name: $file_path})-[r:CO_CHANGED]-(other:Module) "
        "RETURN other.qualified_name AS qualified_name, "
        "       other.file_path AS file_path, "
        "       r.count AS count, r.last_seen AS last_seen "
        "ORDER BY r.count DESC "
        "LIMIT $limit"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, file_path=file_path, limit=limit)]


def _recent_commits(drv: Driver, file_path: str, limit: int) -> list[dict]:
    cypher = (
        "MATCH (c:Commit)-[:TOUCHED_BY]->(m:Module {qualified_name: $file_path}) "
        "RETURN c.commit_hash AS commit_hash, c.message AS message, "
        "       c.author AS author, c.date AS date "
        "ORDER BY c.date DESC "
        "LIMIT $limit"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, file_path=file_path, limit=limit)]


def _related_issues(drv: Driver, chunk_id: str) -> list[dict]:
    cypher = (
        "MATCH (i:Issue)-[:REFERENCES]->(n) WHERE n.chunk_id = $cid "
        "RETURN i.chunk_id AS chunk_id, i.issue_id AS issue_id, "
        "       i.title AS title, i.status AS status, "
        "       coalesce(i.source_reliability, 'low') AS source_reliability "
        "LIMIT 20"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, cid=chunk_id)]


# ── Graph-only entry points (called by /search/{callers,callees,co_changed}) ──

def callers_for(chunk_id: str, max_hops: int = 2) -> list[dict]:
    drv = neo4j_store.driver()
    return _callers(drv, chunk_id, max_hops)


def callees_for(chunk_id: str, max_hops: int = 2) -> list[dict]:
    drv = neo4j_store.driver()
    return _callees(drv, chunk_id, max_hops)


def co_changed_for(file_path: str, min_count: int = 3, limit: int = 8) -> list[dict]:
    drv = neo4j_store.driver()
    cypher = (
        "MATCH (m:Module {qualified_name: $file_path})-[r:CO_CHANGED]-(other:Module) "
        "WHERE r.count >= $min_count "
        "RETURN other.qualified_name AS qualified_name, "
        "       other.file_path AS file_path, "
        "       r.count AS count, r.last_seen AS last_seen "
        "ORDER BY r.count DESC LIMIT $limit"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, file_path=file_path, min_count=min_count, limit=limit)]
