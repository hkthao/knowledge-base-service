from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...query import graph_expand, search_pipeline
from ...tracing import trace_search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    collections: list[str] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    expand_graph: bool = True
    rerank: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)


@router.post("/search")
def search_endpoint(req: SearchRequest) -> dict:
    with trace_search(req.query, top_k=req.top_k, collections=req.collections or [], filters=req.filters) as trace:
        result = search_pipeline.run_search(
            req.query,
            collections=req.collections,
            top_k=req.top_k,
            filters=req.filters or None,
            expand_graph=req.expand_graph,
            rerank=req.rerank,
        )
        try:
            trace.update(output={"result_count": len(result.get("results", []))})
        except Exception:
            pass
        return result


class SymbolLookupRequest(BaseModel):
    qualified_name: str


@router.post("/search/symbol")
def search_symbol_endpoint(req: SymbolLookupRequest) -> dict:
    found = search_pipeline.lookup_by_qualified_name(req.qualified_name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"qualified_name not found: {req.qualified_name}")
    return found


class CallersRequest(BaseModel):
    chunk_id: str
    max_hops: int = Field(default=2, ge=1, le=4)


@router.post("/search/callers")
def search_callers_endpoint(req: CallersRequest) -> dict:
    return {"chunk_id": req.chunk_id, "callers": graph_expand.callers_for(req.chunk_id, req.max_hops)}


@router.post("/search/callees")
def search_callees_endpoint(req: CallersRequest) -> dict:
    return {"chunk_id": req.chunk_id, "callees": graph_expand.callees_for(req.chunk_id, req.max_hops)}


class CoChangedRequest(BaseModel):
    file_path: str
    min_count: int = Field(default=3, ge=1)
    limit: int = Field(default=8, ge=1, le=50)


@router.post("/search/co_changed")
def search_co_changed_endpoint(req: CoChangedRequest) -> dict:
    return {
        "file_path": req.file_path,
        "co_changed": graph_expand.co_changed_for(req.file_path, req.min_count, req.limit),
    }
