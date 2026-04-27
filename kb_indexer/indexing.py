from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from .embedder import DenseEmbedder
from .extractors.entity_extractor import extract_from_file
from .extractors.relation_extractor import resolve_intra_file
from .log import get_logger
from .parsers.ts_parser import Entity, ParseResult
from .state import tracker
from .stores import neo4j_store, qdrant_store
from .stores.qdrant_store import CODE_TS

log = get_logger(__name__)

EMBEDDABLE_TYPES = {"function", "method", "class", "interface"}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _embeddable(parsed: ParseResult) -> list[Entity]:
    return [e for e in parsed.entities if e.symbol_type in EMBEDDABLE_TYPES and e.content]


def index_file(
    *,
    file_path: str,
    repo: str,
    embedder: DenseEmbedder,
    qc=None,
    drv=None,
) -> dict:
    """Idempotent re-index of a single file. Re-running yields the same state."""
    qc = qc or qdrant_store.client()
    drv = drv or neo4j_store.driver()

    op_id = tracker.record_intent(file_path, "MODIFIED", {"desired_state": "indexed"})
    try:
        parsed = extract_from_file(file_path)
        parsed.relations = resolve_intra_file(parsed)
        embeddable = _embeddable(parsed)

        # Drop old state for this file
        neo4j_store.delete_by_file(drv, file_path)
        qdrant_store.delete_by_file(qc, CODE_TS, file_path)

        chunk_id_by_qn: dict[str, str] = {e.qualified_name: str(uuid4()) for e in embeddable}
        # Module entity also needs a chunk_id (for graph identity), even if not embedded
        for entity in parsed.entities:
            chunk_id_by_qn.setdefault(entity.qualified_name, str(uuid4()))

        # Write graph (has all entities + relations)
        neo4j_store.insert_parse_result(
            drv,
            entities=parsed.entities,
            relations=parsed.relations,
            chunk_id_by_qn=chunk_id_by_qn,
            repo=repo,
        )

        # Write Qdrant points (only embeddable entities)
        chunk_ids = _write_chunks(qc, repo, embeddable, chunk_id_by_qn, embedder)

        tracker.upsert_file(
            file_path=file_path,
            repo=repo,
            content_hash=sha256_file(file_path),
            status="indexed",
            chunk_ids=chunk_ids,
            neo4j_node_ids=list(chunk_id_by_qn.values()),
        )
        tracker.mark_sync_done(op_id)
        log.info("indexed", file=file_path, entities=len(parsed.entities), chunks=len(chunk_ids))
        return {"file_path": file_path, "entities": len(parsed.entities), "chunks": len(chunk_ids)}
    except Exception as exc:
        tracker.mark_sync_failed(op_id, error=str(exc))
        try:
            tracker.upsert_file(
                file_path=file_path,
                repo=repo,
                content_hash=sha256_file(file_path) if Path(file_path).exists() else "",
                status="failed",
                chunk_ids=[],
                neo4j_node_ids=[],
                error=str(exc),
            )
        except Exception:
            pass
        log.error("index_failed", file=file_path, error=str(exc))
        raise


def _write_chunks(
    qc,
    repo: str,
    entities: list[Entity],
    chunk_id_by_qn: dict[str, str],
    embedder: DenseEmbedder,
) -> list[str]:
    if not entities:
        return []

    from . import bm25_encoder

    contents = [e.content for e in entities]
    dense_vecs = embedder.embed(contents)
    sparse_vecs = bm25_encoder.encode(contents)

    points = []
    for entity, dense, sparse in zip(entities, dense_vecs, sparse_vecs):
        chunk_id = chunk_id_by_qn[entity.qualified_name]
        points.append({
            "id": chunk_id,
            "dense": dense,
            "bm25": sparse,
            "payload": {
                "chunk_id": chunk_id,
                "qualified_name": entity.qualified_name,
                "content": entity.content,
                "source_type": "code",
                "source_reliability": "high",
                "language": entity.language,
                "symbol_name": entity.name,
                "symbol_type": entity.symbol_type,
                "parent_class": entity.parent_class,
                "file_path": entity.file_path,
                "repo": repo,
                "line_start": entity.line_start,
                "line_end": entity.line_end,
                "is_latest": True,
                "description_status": "pending",
            },
        })

    return qdrant_store.upsert_points(qc, CODE_TS, points)


def index_repo(
    *,
    repo: str,
    repo_path: str,
    embedder: DenseEmbedder,
    glob_patterns: Iterable[str] = ("**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"),
) -> dict:
    qc = qdrant_store.client()
    drv = neo4j_store.driver()

    root = Path(repo_path).resolve()
    files: list[str] = []
    for pattern in glob_patterns:
        files.extend(str(p) for p in root.glob(pattern) if p.is_file())
    # de-dup + skip node_modules / dist
    files = sorted({f for f in files if "/node_modules/" not in f and "/dist/" not in f})

    log.info("indexing_repo", repo=repo, files=len(files))
    indexed = 0
    failures: list[str] = []
    for file_path in files:
        try:
            index_file(file_path=file_path, repo=repo, embedder=embedder, qc=qc, drv=drv)
            indexed += 1
        except Exception as exc:
            failures.append(f"{file_path}: {exc}")

    return {"repo": repo, "indexed": indexed, "total": len(files), "failures": failures}
