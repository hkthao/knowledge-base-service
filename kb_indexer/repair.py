"""Background repair: retry failed indexing, re-process dirty files,
random-sample for silent drift between Neo4j and Qdrant.

Per plan §10.3 (revised): the 15-minute loop should NOT be O(files) —
that's why we have the dirty flag (set by webhooks/failed ops) and a
small random sample. The full sweep belongs in a daily off-peak cron.
"""

from __future__ import annotations

from .embedder import DenseEmbedder, make_embedder
from .indexing import index_file
from .log import get_logger
from .parsers.csharp_parser import CSharpParser
from .parsers.csproj_resolver import CsprojResolver
from .state import tracker
from .stores import neo4j_store, qdrant_store
from .stores.qdrant_store import code_collection_for

log = get_logger(__name__)


def run_repair_pass(
    *,
    repo_path: str | None = None,
    failed_limit: int = 100,
    dirty_limit: int = 200,
    sample_fraction: float = 0.01,
    embedder: DenseEmbedder | None = None,
    csharp_parser: CSharpParser | None = None,
) -> dict:
    """One sweep. Designed to fit inside a 15-minute schedule even at
    50k files because work is bounded: failed_limit + dirty_limit + a
    small percentage of indexed files."""
    embedder = embedder or make_embedder()
    qc = qdrant_store.client()
    drv = neo4j_store.driver()

    # Lazy resolver / parser — only initialized if a .cs file is touched.
    state = {"resolver": None, "parser": csharp_parser}

    def reindex(file_path: str) -> None:
        project_path = None
        if file_path.endswith(".cs"):
            if state["resolver"] is None:
                if not repo_path:
                    raise RuntimeError("repo_path required to resolve .csproj for repair")
                state["resolver"] = CsprojResolver(repo_path)
                state["parser"] = state["parser"] or CSharpParser()
            project_path = state["resolver"].resolve(file_path)
        existing = tracker.get_file(file_path)
        repo_name = existing.repo if existing is not None else "unknown"
        index_file(
            file_path=file_path,
            repo=repo_name,
            embedder=embedder,
            project_path=project_path,
            csharp_parser=state["parser"],
            qc=qc,
            drv=drv,
        )

    summary = {"failed_retried": 0, "dirty_processed": 0, "sample_marked_dirty": 0, "errors": []}

    # 1. Retry hard failures.
    for record in tracker.query_failed(limit=failed_limit):
        try:
            reindex(record.file_path)
            summary["failed_retried"] += 1
        except Exception as exc:
            summary["errors"].append(f"failed-retry {record.file_path}: {exc}")

    # 2. Drain dirty queue. A row is dirty when something hinted that it
    #    drifted (failed health check, partial write, etc.). Verify and fix.
    for record in tracker.query_dirty(limit=dirty_limit):
        try:
            collection = code_collection_for("csharp" if record.file_path.endswith(".cs") else "typescript")
            actual = qdrant_store.count_by_file(qc, collection, record.file_path)
            if actual != len(record.chunk_ids or []):
                reindex(record.file_path)
            summary["dirty_processed"] += 1
        except Exception as exc:
            summary["errors"].append(f"dirty {record.file_path}: {exc}")

    # 3. Sampling sweep — catch silent drift without scanning every file.
    sample = tracker.random_sample_indexed(fraction=sample_fraction, limit=200)
    for record in sample:
        try:
            collection = code_collection_for("csharp" if record.file_path.endswith(".cs") else "typescript")
            actual = qdrant_store.count_by_file(qc, collection, record.file_path)
            if actual != len(record.chunk_ids or []):
                tracker.mark_dirty(record.file_path)
                summary["sample_marked_dirty"] += 1
        except Exception as exc:
            summary["errors"].append(f"sample {record.file_path}: {exc}")

    log.info("repair_pass_done", **{k: v for k, v in summary.items() if k != "errors"})
    if summary["errors"]:
        log.warning("repair_pass_errors", count=len(summary["errors"]))
    return summary
