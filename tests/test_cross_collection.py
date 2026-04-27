from kb_indexer.query.cross_collection import (
    merge_code_and_desc_hits,
    merge_collection_hits,
    rrf_merge,
)


def test_rrf_merge_ranks_overlap_higher():
    list_a = [{"chunk_id": "x"}, {"chunk_id": "y"}, {"chunk_id": "z"}]
    list_b = [{"chunk_id": "y"}, {"chunk_id": "x"}, {"chunk_id": "w"}]
    merged = rrf_merge([list_a, list_b])
    ids = [m["chunk_id"] for m in merged]
    # x and y appear in both lists near the top — they should outrank singletons
    assert ids.index("x") < ids.index("z")
    assert ids.index("y") < ids.index("w")


def test_merge_dedupes_via_linked_chunk_id():
    code_hits = [
        {"chunk_id": "code-1", "qualified_name": "checkCreditLimit"},
    ]
    desc_hits = [
        {
            "chunk_id": "desc-1",
            "linked_chunk_id": "code-1",
            "qualified_name": "checkCreditLimit",
            "content": "Kiểm tra hạn mức tín dụng",
        },
    ]
    merged = merge_code_and_desc_hits(code_hits, desc_hits)
    # Code chunk dedup'd against description hit linking to it — single result
    assert len(merged) == 1
    assert merged[0]["chunk_id"] == "code-1"


def test_description_only_match_surfaces_code_chunk_id():
    """Vietnamese query matches only the description; result should still
    point at the original code chunk so callers can fetch the source."""
    desc_only = [{
        "chunk_id": "desc-7",
        "linked_chunk_id": "code-7",
        "qualified_name": "checkCreditLimit",
        "matched_via": "description",
    }]
    merged = merge_code_and_desc_hits(code_hits=[], desc_hits=desc_only)
    assert len(merged) == 1
    assert merged[0]["chunk_id"] == "code-7"
    assert merged[0]["matched_via"] == "description"


def test_merge_collection_hits_dedupes_desc_via_linked_chunk_id():
    out = merge_collection_hits({
        "code_ts": [{"chunk_id": "code-9", "qualified_name": "checkLimit"}],
        "code_ts_desc": [{
            "chunk_id": "desc-9",
            "linked_chunk_id": "code-9",
            "qualified_name": "checkLimit",
            "content": "Kiểm tra hạn mức",
        }],
    })
    assert len(out) == 1
    assert out[0]["chunk_id"] == "code-9"
    # Both signals contributed to RRF — score is the sum, so > single-list
    assert out[0]["score"] > 1 / (60 + 1)


def test_merge_collection_hits_includes_docs_unchanged():
    out = merge_collection_hits({
        "code_ts": [{"chunk_id": "code-1"}],
        "docs": [{"chunk_id": "doc-1", "title": "Auth flow"}],
    })
    chunk_ids = {h["chunk_id"] for h in out}
    assert chunk_ids == {"code-1", "doc-1"}
    doc_hit = next(h for h in out if h["chunk_id"] == "doc-1")
    assert doc_hit["matched_via"] == "docs"
