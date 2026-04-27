from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from .embedder import DenseEmbedder, OllamaEmbedder
from .extractors.entity_extractor import extract_from_file
from .extractors.relation_extractor import resolve_intra_file
from .log import get_logger
from .parsers import doc_parser
from .parsers.csharp_parser import CSharpParser
from .parsers.csproj_resolver import CsprojResolver
from .parsers.ts_parser import Entity, ParseResult
from .settings import settings
from .state import tracker
from .stores import neo4j_store, qdrant_store
from .stores.qdrant_store import DOCS, code_collection_for

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
    project_path: str | None = None,
    csharp_parser: CSharpParser | None = None,
    qc=None,
    drv=None,
) -> dict:
    """Idempotent re-index of a single file. Re-running yields the same state."""
    qc = qc or qdrant_store.client()
    drv = drv or neo4j_store.driver()

    op_id = tracker.record_intent(file_path, "MODIFIED", {"desired_state": "indexed"})
    try:
        parsed = extract_from_file(
            file_path,
            project_path=project_path,
            csharp_parser=csharp_parser,
        )
        parsed.relations = resolve_intra_file(parsed)
        embeddable = _embeddable(parsed)

        collection = code_collection_for(parsed.language)

        # Drop old state for this file
        neo4j_store.delete_by_file(drv, file_path)
        qdrant_store.delete_by_file(qc, collection, file_path)

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
        chunk_ids = _write_chunks(qc, collection, repo, embeddable, chunk_id_by_qn, embedder)

        # Enqueue Vietnamese description jobs — worker drains async (§8).
        tracker.enqueue_desc_jobs([
            {
                "chunk_id": chunk_id_by_qn[e.qualified_name],
                "qualified_name": e.qualified_name,
                "language": parsed.language,
                "repo": repo,
            }
            for e in embeddable
        ])

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
    collection: str,
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

    return qdrant_store.upsert_points(qc, collection, points)


_DEFAULT_TS_GLOBS = ("**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx")
_DEFAULT_CS_GLOBS = ("**/*.cs",)
_SKIP_DIR_FRAGMENTS = ("/node_modules/", "/dist/", "/bin/", "/obj/", "/.git/")


def index_repo(
    *,
    repo: str,
    repo_path: str,
    embedder: DenseEmbedder,
    glob_patterns: Iterable[str] = _DEFAULT_TS_GLOBS + _DEFAULT_CS_GLOBS,
    csharp_parser: CSharpParser | None = None,
) -> dict:
    qc = qdrant_store.client()
    drv = neo4j_store.driver()

    root = Path(repo_path).resolve()
    files: list[str] = []
    for pattern in glob_patterns:
        files.extend(str(p) for p in root.glob(pattern) if p.is_file())
    files = sorted({
        f for f in files
        if not any(frag in f for frag in _SKIP_DIR_FRAGMENTS)
    })

    cs_files = [f for f in files if f.endswith(".cs")]
    other_files = [f for f in files if not f.endswith(".cs")]

    csproj_resolver: CsprojResolver | None = None
    parser = csharp_parser
    if cs_files:
        csproj_resolver = CsprojResolver(repo_path)
        parser = parser or CSharpParser()

    log.info(
        "indexing_repo",
        repo=repo, ts_files=len(other_files), cs_files=len(cs_files),
    )

    indexed = 0
    failures: list[str] = []

    for file_path in other_files:
        try:
            index_file(file_path=file_path, repo=repo, embedder=embedder, qc=qc, drv=drv)
            indexed += 1
        except Exception as exc:
            failures.append(f"{file_path}: {exc}")

    for file_path in cs_files:
        try:
            assert csproj_resolver is not None
            project_path = csproj_resolver.resolve(file_path)
            index_file(
                file_path=file_path,
                repo=repo,
                embedder=embedder,
                project_path=project_path,
                csharp_parser=parser,
                qc=qc,
                drv=drv,
            )
            indexed += 1
        except Exception as exc:
            failures.append(f"{file_path}: {exc}")

    return {"repo": repo, "indexed": indexed, "total": len(files), "failures": failures}


# ── Docs ──────────────────────────────────────────────────────────────

def index_doc(
    *,
    file_path: str,
    repo: str,
    text_embedder: DenseEmbedder | None = None,
    qc=None,
    drv=None,
) -> dict:
    """Idempotent re-index of a single doc (Markdown / PDF / DOCX)."""
    qc = qc or qdrant_store.client()
    drv = drv or neo4j_store.driver()
    text_embedder = text_embedder or OllamaEmbedder(model=settings.ollama_text_model)

    op_id = tracker.record_intent(file_path, "DOC_MODIFIED", {"desired_state": "indexed"})
    try:
        chunks = doc_parser.parse_file(file_path)

        # Drop old state before insert (idempotent re-index per §10.3).
        qdrant_store.delete_by_file(qc, DOCS, file_path)
        neo4j_store.delete_by_file(drv, file_path)

        chunk_ids = _write_doc_chunks(qc, drv, file_path, repo, chunks, text_embedder)
        tracker.upsert_doc(
            file_path=file_path,
            content_hash=sha256_file(file_path),
            chunk_ids=chunk_ids,
            neo4j_node_ids=chunk_ids,
            is_latest=True,
        )
        tracker.mark_sync_done(op_id)
        log.info("doc_indexed", file=file_path, chunks=len(chunks))
        return {"file_path": file_path, "chunks": len(chunks)}
    except Exception as exc:
        tracker.mark_sync_failed(op_id, error=str(exc))
        log.error("doc_index_failed", file=file_path, error=str(exc))
        raise


def index_docs_dir(
    *,
    repo: str,
    docs_path: str,
    text_embedder: DenseEmbedder | None = None,
    glob_patterns: tuple[str, ...] = ("**/*.md", "**/*.markdown", "**/*.txt"),
) -> dict:
    qc = qdrant_store.client()
    drv = neo4j_store.driver()
    text_embedder = text_embedder or OllamaEmbedder(model=settings.ollama_text_model)
    root = Path(docs_path).resolve()

    files: list[str] = []
    for pattern in glob_patterns:
        files.extend(str(p) for p in root.glob(pattern) if p.is_file())
    files = sorted(set(files))

    indexed = 0
    failures: list[str] = []
    for file_path in files:
        try:
            index_doc(file_path=file_path, repo=repo, text_embedder=text_embedder, qc=qc, drv=drv)
            indexed += 1
        except Exception as exc:
            failures.append(f"{file_path}: {exc}")
    return {"repo": repo, "indexed": indexed, "total": len(files), "failures": failures}


def _write_doc_chunks(qc, drv, file_path: str, repo: str, chunks, text_embedder: DenseEmbedder) -> list[str]:
    if not chunks:
        return []

    from . import bm25_encoder

    contents = [c.content for c in chunks]
    dense_vecs = text_embedder.embed(contents)
    sparse_vecs = bm25_encoder.encode(contents)

    ids: list[str] = []
    points = []
    for chunk, dense, sparse in zip(chunks, dense_vecs, sparse_vecs):
        chunk_id = str(uuid4())
        ids.append(chunk_id)
        points.append({
            "id": chunk_id,
            "dense": dense,
            "bm25": sparse,
            "payload": {
                "chunk_id": chunk_id,
                "title": chunk.title,
                "content": chunk.content,
                "source_type": "doc",
                "source_reliability": "high",
                "language": "vi",  # docs assumed Vietnamese in this project
                "file_path": file_path,
                "repo": repo,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "is_latest": True,
                "chunk_index": chunk.chunk_index,
            },
        })

    qdrant_store.upsert_points(qc, DOCS, points)

    # Document nodes in Neo4j — gives the graph a hook for future
    # references (RFC links, design-doc citations, etc.).
    with drv.session() as s:
        for chunk, chunk_id in zip(chunks, ids):
            s.run(
                """
                MERGE (d:Document {chunk_id: $chunk_id})
                SET d.title = $title,
                    d.file_path = $file_path,
                    d.repo = $repo,
                    d.line_start = $line_start,
                    d.line_end = $line_end,
                    d.is_latest = true
                """,
                chunk_id=chunk_id, title=chunk.title, file_path=file_path,
                repo=repo, line_start=chunk.line_start, line_end=chunk.line_end,
            )

    return ids
