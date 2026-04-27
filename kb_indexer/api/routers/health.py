from fastapi import APIRouter
from neo4j.exceptions import ServiceUnavailable

from ...stores import neo4j_store, qdrant_store

router = APIRouter()


@router.get("/health")
def health() -> dict:
    status: dict[str, str] = {}

    try:
        qc = qdrant_store.client()
        qc.get_collections()
        status["qdrant"] = "ok"
    except Exception as exc:
        status["qdrant"] = f"error: {exc}"

    try:
        drv = neo4j_store.driver()
        with drv.session() as session:
            session.run("RETURN 1").consume()
        status["neo4j"] = "ok"
    except (ServiceUnavailable, Exception) as exc:
        status["neo4j"] = f"error: {exc}"

    overall = "ok" if all(v == "ok" for v in status.values()) else "degraded"
    return {"status": overall, "deps": status}
