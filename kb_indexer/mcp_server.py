"""MCP server for the Knowledge Base Service.

Exposes the KB's retrieval surface as MCP tools so any MCP-compatible
client (Claude Desktop, Claude Code, etc.) can query the indexed
codebase + docs without going through HTTP. Functions call into the
same Python modules the FastAPI router uses.

Run via stdio (mặc định cho Claude Desktop / Claude Code):

    python -m kb_indexer.mcp_server

Hoặc qua entrypoint:

    kb-mcp
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .log import configure_logging
from .query import graph_expand, search_pipeline
from .state import tracker
from .stores import qdrant_store
from .stores.qdrant_store import (
    CODE_CS,
    CODE_CS_DESC,
    CODE_TS,
    CODE_TS_DESC,
    DOCS,
    ISSUES,
)

configure_logging()

mcp = FastMCP(
    "knowledge-base",
    instructions=(
        "Tra cứu codebase + docs đã được index. Dùng `search` cho query "
        "theo ngôn ngữ tự nhiên / từ khoá, `lookup_symbol` khi đã biết "
        "qualified_name, và các tool graph (`find_callers` / "
        "`find_callees` / `find_co_changed`) để khám phá quan hệ giữa "
        "các symbol. Mỗi result row có `chunk_id` ổn định để chain các "
        "tool với nhau."
    ),
)


# ── Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def search(
    query: str,
    top_k: int = 10,
    collections: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    expand_graph: bool = True,
    rerank: bool = True,
) -> dict:
    """Hybrid search (dense + BM25 + RRF) trên KB, kèm graph context.

    Trả về tối đa `top_k` kết quả. Mỗi hit có `chunk_id`, `qualified_name`,
    `file_path`, `line_start..line_end`, `content`, `source_reliability`
    (`high|medium|low`), `matched_via` (`code` | `description` | `docs`).
    Khi `expand_graph=True` mỗi hit kèm `graph_context` với callers,
    callees, co_changed, recent_commits, related_issues.

    Args:
        query: Tự nhiên (tiếng Việt OK) hoặc từ khoá kỹ thuật.
        top_k: Số kết quả tối đa, 1–100. Default 10.
        collections: Subset trong
            ["code_ts","code_ts_desc","code_cs","code_cs_desc","docs","issues"].
            Default = all (5 collection ngoại trừ issues).
        filters: Map giá trị bắt buộc khớp trên payload, vd
            {"repo": "my-app", "source_reliability": "high",
             "language": "csharp"}. Value có thể là scalar hoặc list.
        expand_graph: Có attach `graph_context` không. Tốn thêm vài
            Cypher query nhưng đủ rẻ ở top_k=10.
        rerank: Có chạy cross-encoder ms-marco-MiniLM-L-6-v2 không. Lần
            đầu sẽ load ~80MB model.
    """
    return search_pipeline.run_search(
        query,
        collections=collections,
        top_k=top_k,
        filters=filters,
        expand_graph=expand_graph,
        rerank=rerank,
    )


@mcp.tool()
def lookup_symbol(qualified_name: str) -> dict | None:
    """Tra chính xác một symbol theo `qualified_name`.

    `qualified_name` là canonical key:
    - TS/JS: `src/path/file.ts::SymbolName` hoặc `...::Class.method`.
    - C#:    `Namespace::ClassName.MethodName` (không phụ thuộc file
              path vì partial class có thể split nhiều file).

    Trả về metadata của entity (chunk_id, file_path, line range,
    signature, docstring, labels). Trả `null` nếu không có.
    """
    return search_pipeline.lookup_by_qualified_name(qualified_name)


@mcp.tool()
def find_callers(chunk_id: str, max_hops: int = 2) -> list[dict]:
    """Liệt kê các symbol gọi tới symbol đã cho qua CALLS edge trong
    Neo4j, đi tối đa `max_hops` cấp.

    Mỗi caller có `qualified_name`, `file_path`, `line_start`, `hops`,
    và `confidence` (product-of-confidence dọc đường). C# semantic
    resolution có confidence=1.0; TS heuristic 0.5–0.9.

    `chunk_id` lấy từ result `search` hoặc `lookup_symbol`.
    """
    return graph_expand.callers_for(chunk_id, max_hops=max_hops)


@mcp.tool()
def find_callees(chunk_id: str, max_hops: int = 2) -> list[dict]:
    """Liệt kê các symbol mà symbol đã cho gọi đến qua CALLS edge,
    đi tối đa `max_hops` cấp. Format giống `find_callers`."""
    return graph_expand.callees_for(chunk_id, max_hops=max_hops)


@mcp.tool()
def find_co_changed(
    file_path: str,
    min_count: int = 3,
    limit: int = 8,
) -> list[dict]:
    """Liệt kê các file (Module) hay thay đổi cùng `file_path` trong
    lịch sử git.

    CO_CHANGED edge ở mức module — git diff không cho granularity
    symbol. Mass-format / mass-rename commit (>30 file) bị filter
    sẵn để không spam tín hiệu.

    Mỗi neighbour có `qualified_name`, `file_path`, `count` (số commit
    chung), `last_seen`. Sắp xếp theo `count` giảm dần.
    """
    return graph_expand.co_changed_for(file_path, min_count=min_count, limit=limit)


@mcp.tool()
def kb_stats() -> dict:
    """Trả tổng quan trạng thái KB: số points trong từng Qdrant
    collection và breakdown của desc_jobs queue
    (`pending|processing|done|failed`).

    Hữu ích để biết:
    - Repo đã index chưa (collection rỗng = chưa).
    - Description coverage (`done` ÷ tổng).
    - Có job stuck `failed` cần troubleshoot không.
    """
    qc = qdrant_store.client()
    collections: dict[str, int | str] = {}
    for name in (CODE_TS, CODE_TS_DESC, CODE_CS, CODE_CS_DESC, DOCS, ISSUES):
        try:
            info = qc.get_collection(name)
            collections[name] = info.points_count or 0
        except Exception as exc:
            collections[name] = f"error: {exc}"
    return {
        "qdrant": collections,
        "desc_jobs": tracker.desc_job_counts(),
    }


# ── Entrypoint ────────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server over stdio (default transport for desktop clients)."""
    mcp.run()


if __name__ == "__main__":
    main()
