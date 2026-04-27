from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from ..settings import settings

CODE_TS = "code_ts"
CODE_TS_DESC = "code_ts_desc"
CODE_CS = "code_cs"
CODE_CS_DESC = "code_cs_desc"
DOCS = "docs"
ISSUES = "issues"
ALL_COLLECTIONS = (CODE_TS, CODE_TS_DESC, CODE_CS, CODE_CS_DESC, DOCS, ISSUES)


def code_collection_for(language: str) -> str:
    return CODE_CS if language == "csharp" else CODE_TS


def desc_collection_for(language: str) -> str:
    return CODE_CS_DESC if language == "csharp" else CODE_TS_DESC

_KEYWORD_FIELDS = (
    "file_path", "repo", "symbol_type", "is_latest",
    "source_reliability", "qualified_name", "language",
)


def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def create_collection_if_not_exists(qc: QdrantClient, name: str, dense_size: int | None = None) -> None:
    dense_size = dense_size or settings.dense_dim
    existing = {c.name for c in qc.get_collections().collections}
    if name in existing:
        return

    qc.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=dense_size, distance=Distance.COSINE)},
        sparse_vectors_config={"bm25": SparseVectorParams(index=SparseIndexParams(on_disk=False))},
    )
    for field in _KEYWORD_FIELDS:
        qc.create_payload_index(name, field, "keyword")
    qc.create_payload_index(name, "line_start", "integer")
    qc.create_payload_index(name, "confidence", "float")


def upsert_points(
    qc: QdrantClient,
    collection: str,
    points: list[dict[str, Any]],
) -> list[str]:
    """Each point dict must have: id, dense, bm25 (indices+values), payload."""
    structs = [
        PointStruct(
            id=p["id"],
            vector={
                "dense": p["dense"],
                "bm25": SparseVector(indices=p["bm25"]["indices"], values=p["bm25"]["values"]),
            },
            payload=p["payload"],
        )
        for p in points
    ]
    qc.upsert(collection_name=collection, points=structs, wait=True)
    return [p["id"] for p in points]


def delete_by_file(qc: QdrantClient, collection: str, file_path: str) -> None:
    qc.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))],
        ),
    )


def count_by_file(qc: QdrantClient, collection: str, file_path: str) -> int:
    res = qc.count(
        collection_name=collection,
        count_filter=Filter(
            must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))],
        ),
        exact=True,
    )
    return res.count


def hybrid_search(
    qc: QdrantClient,
    collection: str,
    *,
    dense_query: list[float],
    bm25_query: dict,
    top_k: int = 10,
    qfilter: Filter | None = None,
) -> list[dict]:
    response = qc.query_points(
        collection_name=collection,
        prefetch=[
            Prefetch(query=dense_query, using="dense", limit=max(30, top_k * 3), filter=qfilter),
            Prefetch(
                query=SparseVector(indices=bm25_query["indices"], values=bm25_query["values"]),
                using="bm25",
                limit=max(30, top_k * 3),
                filter=qfilter,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
    )
    return [{**point.payload, "score": point.score, "chunk_id": str(point.id)} for point in response.points]
