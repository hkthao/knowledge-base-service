from fastapi import APIRouter
from pydantic import BaseModel

from ...repair import run_repair_pass
from ...state import tracker
from ...stores import qdrant_store
from ...stores.qdrant_store import (
    CODE_CS,
    CODE_CS_DESC,
    CODE_TS,
    CODE_TS_DESC,
    DOCS,
    ISSUES,
)

router = APIRouter()

_TRACKED = (CODE_TS, CODE_TS_DESC, CODE_CS, CODE_CS_DESC, DOCS, ISSUES)


class RepairRequest(BaseModel):
    repo_path: str | None = None
    failed_limit: int = 100
    dirty_limit: int = 200
    sample_fraction: float = 0.01


@router.post("/repair")
def repair_endpoint(req: RepairRequest) -> dict:
    return run_repair_pass(
        repo_path=req.repo_path,
        failed_limit=req.failed_limit,
        dirty_limit=req.dirty_limit,
        sample_fraction=req.sample_fraction,
    )


@router.get("/stats")
def stats_endpoint() -> dict:
    qc = qdrant_store.client()
    collections: dict[str, int | str] = {}
    for name in _TRACKED:
        try:
            info = qc.get_collection(name)
            collections[name] = info.points_count or 0
        except Exception as exc:
            collections[name] = f"error: {exc}"

    return {
        "qdrant": collections,
        "desc_jobs": tracker.desc_job_counts(),
    }


@router.get("/stats/consistency")
def consistency_endpoint() -> dict:
    """Per-file Qdrant chunk count vs. tracker chunk_ids length.

    Lists files where they disagree — useful for spotting silent drift
    before the sampling sweep finds it. Bounded to a sensible page size."""
    qc = qdrant_store.client()
    mismatches: list[dict] = []
    checked = 0

    for record in tracker.random_sample_indexed(fraction=1.0, limit=200):
        collection = (
            CODE_CS if record.file_path.endswith(".cs") else CODE_TS
        )
        try:
            actual = qdrant_store.count_by_file(qc, collection, record.file_path)
        except Exception as exc:
            mismatches.append({"file": record.file_path, "error": str(exc)})
            continue
        expected = len(record.chunk_ids or [])
        checked += 1
        if actual != expected:
            mismatches.append({
                "file": record.file_path,
                "expected": expected,
                "actual": actual,
                "collection": collection,
            })

    return {"checked": checked, "mismatches": mismatches}
