"""End-to-end test cho MCP server.

Spawn server qua stdio, initialize, list tools, gọi kb_stats. Dùng để
verify nhanh MCP đã chạy và đang nói chuyện được với Neo4j + Qdrant.
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent

    params = StdioServerParameters(
        command=str(repo_root / ".venv/bin/python"),
        args=["-m", "kb_indexer.mcp_server"],
        cwd=str(repo_root),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("── tools/list ──")
            tools = await session.list_tools()
            for t in tools.tools:
                first_line = (t.description or "").splitlines()[0][:70]
                print(f"  {t.name:<20} {first_line}")

            print()
            print("── kb_stats ──")
            res = await session.call_tool("kb_stats", {})
            for block in res.content:
                if hasattr(block, "text"):
                    print(block.text)

            print()
            print("── search ──")
            res = await session.call_tool("search", {
                "query": "kiểm tra hạn mức tín dụng",
                "top_k": 3,
                "expand_graph": False,
                "rerank": False,
            })
            for block in res.content:
                if hasattr(block, "text"):
                    text = block.text
                    print(text[:600] + ("…" if len(text) > 600 else ""))

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
