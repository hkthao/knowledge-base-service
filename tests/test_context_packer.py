from kb_indexer.query.context_packer import pack


def test_pack_drops_none_fields():
    hits = [{
        "chunk_id": "c1",
        "qualified_name": "src/x.ts::foo",
        "content": "function foo() {}",
        "file_path": "src/x.ts",
        "line_start": 1,
        "line_end": 5,
        "score": 0.9,
        "source_reliability": "high",
        "matched_via": "code",
        # symbol_type intentionally omitted -> should not surface
    }]
    out = pack(hits, query="foo")
    assert out["query"] == "foo"
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["qualified_name"] == "src/x.ts::foo"
    assert "symbol_type" not in r


def test_pack_attaches_graph_context():
    hits = [{"chunk_id": "c1", "qualified_name": "X", "score": 0.5}]
    graph = {"c1": {"callers": [{"qualified_name": "caller"}], "callees": []}}
    out = pack(hits, graph_context_by_chunk_id=graph)
    assert out["results"][0]["graph_context"]["callers"][0]["qualified_name"] == "caller"


def test_pack_no_graph_context_for_unmatched_chunk():
    hits = [{"chunk_id": "c1", "qualified_name": "X", "score": 0.5}]
    out = pack(hits, graph_context_by_chunk_id={"other": {}})
    assert "graph_context" not in out["results"][0]
