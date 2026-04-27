"""MCP server smoke test — verifies tools are registered with sane
schemas and that they delegate to the underlying search pipeline.
The actual Neo4j/Qdrant calls are stubbed."""

import pytest


@pytest.mark.asyncio
async def test_all_tools_registered():
    from kb_indexer import mcp_server

    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "search",
        "lookup_symbol",
        "find_callers",
        "find_callees",
        "find_co_changed",
        "kb_stats",
    } <= names


@pytest.mark.asyncio
async def test_search_tool_schema_has_required_query():
    from kb_indexer import mcp_server

    tools = await mcp_server.mcp.list_tools()
    search_tool = next(t for t in tools if t.name == "search")
    schema = search_tool.inputSchema
    # Pydantic-derived schema; query is required, top_k default 10
    assert "query" in schema["properties"]
    assert "query" in schema.get("required", [])
    assert schema["properties"]["top_k"]["default"] == 10


@pytest.mark.asyncio
async def test_lookup_symbol_delegates_to_pipeline(monkeypatch):
    from kb_indexer import mcp_server

    captured = {}

    def fake_lookup(qn):
        captured["qn"] = qn
        return {"qualified_name": qn, "labels": ["Method"]}

    monkeypatch.setattr(mcp_server.search_pipeline, "lookup_by_qualified_name", fake_lookup)

    out = mcp_server.lookup_symbol("MyApp.Auth::AuthService.Login")
    assert captured["qn"] == "MyApp.Auth::AuthService.Login"
    assert out["qualified_name"] == "MyApp.Auth::AuthService.Login"


@pytest.mark.asyncio
async def test_find_callers_delegates_with_max_hops(monkeypatch):
    from kb_indexer import mcp_server

    captured = {}

    def fake_callers(chunk_id, max_hops):
        captured["args"] = (chunk_id, max_hops)
        return [{"qualified_name": "Caller.Foo"}]

    monkeypatch.setattr(mcp_server.graph_expand, "callers_for", fake_callers)

    out = mcp_server.find_callers("chunk-1", max_hops=3)
    assert captured["args"] == ("chunk-1", 3)
    assert out[0]["qualified_name"] == "Caller.Foo"


@pytest.mark.asyncio
async def test_search_passes_through_filters_and_flags(monkeypatch):
    from kb_indexer import mcp_server

    captured = {}

    def fake_run(query, *, collections, top_k, filters, expand_graph, rerank):
        captured.update(
            query=query, collections=collections, top_k=top_k,
            filters=filters, expand_graph=expand_graph, rerank=rerank,
        )
        return {"query": query, "results": []}

    monkeypatch.setattr(mcp_server.search_pipeline, "run_search", fake_run)

    out = mcp_server.search(
        query="kiểm tra hạn mức",
        top_k=5,
        collections=["code_cs"],
        filters={"repo": "demo"},
        expand_graph=False,
        rerank=False,
    )
    assert captured == {
        "query": "kiểm tra hạn mức",
        "collections": ["code_cs"],
        "top_k": 5,
        "filters": {"repo": "demo"},
        "expand_graph": False,
        "rerank": False,
    }
    assert out["results"] == []
