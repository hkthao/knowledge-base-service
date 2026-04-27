"""Search pipeline integration test — fakes Qdrant and Neo4j to verify
the orchestration without live infra. Reranker is skipped (it loads a
~80MB model), graph expansion is mocked."""

import pytest

from kb_indexer.query import search_pipeline


class _FakeNeo4jSession:
    def __init__(self, query_handler):
        self._handler = query_handler

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, cypher, **params):
        return self._handler(cypher, params)


class _FakeNeo4jDriver:
    def __init__(self, query_handler):
        self._handler = query_handler

    def session(self):
        return _FakeNeo4jSession(self._handler)


@pytest.fixture
def fake_world(monkeypatch):
    state = {"hits_by_collection": {}, "graph_calls": []}

    # Stub hybrid_search → returns the test-supplied per-collection results
    def fake_hybrid_search(query, *, collections, top_k, filters=None, qc=None):
        from kb_indexer.query.cross_collection import merge_collection_hits
        hits = {c: state["hits_by_collection"].get(c, []) for c in collections}
        return merge_collection_hits(hits)[:top_k]

    from kb_indexer.query import hybrid_search as hs_mod
    monkeypatch.setattr(hs_mod, "search", fake_hybrid_search)
    monkeypatch.setattr(search_pipeline.hybrid_search, "search", fake_hybrid_search)

    # Mock Neo4j driver — graph_expand uses it for callers/callees/etc.
    def query_handler(cypher, params):
        state["graph_calls"].append((cypher.split()[0:5], params))
        return iter([])  # no graph rows for these tests

    monkeypatch.setattr(
        "kb_indexer.stores.neo4j_store.driver",
        lambda: _FakeNeo4jDriver(query_handler),
    )

    return state


def test_search_returns_packed_response(fake_world):
    fake_world["hits_by_collection"]["code_ts"] = [{
        "chunk_id": "code-1",
        "qualified_name": "src/auth.ts::validateUser",
        "symbol_name": "validateUser",
        "symbol_type": "function",
        "content": "function validateUser() {}",
        "file_path": "src/auth.ts",
        "line_start": 10, "line_end": 20,
        "source_reliability": "high",
        "language": "typescript",
        "score": 0.5,
    }]
    out = search_pipeline.run_search(
        "validateUser",
        collections=["code_ts"],
        top_k=5,
        rerank=False,
        expand_graph=True,
    )
    assert out["query"] == "validateUser"
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["chunk_id"] == "code-1"
    assert r["qualified_name"] == "src/auth.ts::validateUser"
    # graph_context attached even when empty (no rows from fake Neo4j)
    assert "graph_context" in r
    assert r["graph_context"]["callers"] == []
    assert r["graph_context"]["callees"] == []


def test_description_match_surfaces_code_chunk(fake_world):
    """Plan §14 done criterion: 'kiểm tra hạn mức tín dụng' → returns the
    English-named function. Description hit dedupes back to code chunk."""
    fake_world["hits_by_collection"]["code_ts_desc"] = [{
        "chunk_id": "desc-77",
        "linked_chunk_id": "code-77",
        "qualified_name": "src/credit.ts::checkCreditLimit",
        "symbol_name": "checkCreditLimit",
        "content": "Kiểm tra hạn mức tín dụng của khách hàng.",
        "file_path": "src/credit.ts",
        "line_start": 5, "line_end": 15,
        "source_reliability": "high",
    }]
    out = search_pipeline.run_search(
        "kiểm tra hạn mức tín dụng",
        collections=["code_ts", "code_ts_desc"],
        top_k=5,
        rerank=False,
        expand_graph=False,
    )
    assert len(out["results"]) == 1
    r = out["results"][0]
    # The result should point at the CODE chunk so callers can fetch source.
    assert r["chunk_id"] == "code-77"
    assert r["matched_via"] == "description"
    assert r["qualified_name"] == "src/credit.ts::checkCreditLimit"


def test_filters_pass_through_to_hybrid_search(fake_world, monkeypatch):
    captured = {}

    def fake(query, *, collections, top_k, filters=None, qc=None):
        captured["filters"] = filters
        return []

    monkeypatch.setattr(search_pipeline.hybrid_search, "search", fake)
    search_pipeline.run_search(
        "x",
        collections=["code_ts"],
        filters={"repo": "my-app", "source_reliability": "high"},
        top_k=5,
        rerank=False,
        expand_graph=False,
    )
    assert captured["filters"] == {"repo": "my-app", "source_reliability": "high"}


def test_rerank_failure_falls_back_to_truncated_hits(fake_world, monkeypatch):
    fake_world["hits_by_collection"]["code_ts"] = [
        {"chunk_id": f"c-{i}", "qualified_name": f"X{i}", "content": f"x {i}", "score": 1.0}
        for i in range(8)
    ]
    from kb_indexer.query import reranker
    def boom(query, hits, *, top_k):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(reranker, "rerank", boom)

    out = search_pipeline.run_search(
        "x", collections=["code_ts"], top_k=3, rerank=True, expand_graph=False,
    )
    # Falls back gracefully: returns top_k from the unreranked pool.
    assert len(out["results"]) == 3
