"""Worker that drains the description-job queue.

Indexing writes structural code chunks synchronously and enqueues a
DescJob per chunk. This worker pulls a batch, generates a Vietnamese
description for each, upserts into `code_*_desc`, and updates the source
chunk's `description_status` to `ready`.

Fail-open: if generation fails, code chunks remain searchable in
`code_*`; only the description collection lacks the entry. After
`MAX_ATTEMPTS` the job is marked `failed` so it stops blocking the queue.
"""

from __future__ import annotations

from time import sleep
from uuid import uuid4

from . import bm25_encoder
from .description_generator import generate_description
from .embedder import DenseEmbedder, OllamaEmbedder
from .llm import LLMClient, make_llm
from .log import get_logger
from .parsers.ts_parser import Entity
from .settings import settings
from .state import tracker
from .stores import neo4j_store, qdrant_store
from .stores.qdrant_store import code_collection_for, desc_collection_for

log = get_logger(__name__)

MAX_ATTEMPTS = 3


def process_batch(
    *,
    llm: LLMClient | None = None,
    desc_embedder: DenseEmbedder | None = None,
    qc=None,
    drv=None,
    batch_size: int | None = None,
) -> dict:
    """Process up to `batch_size` pending jobs. Returns counts."""
    llm = llm or make_llm()
    # Description collections embed natural-language Vietnamese — use the
    # text model, not the code model.
    desc_embedder = desc_embedder or OllamaEmbedder(model=settings.ollama_text_model)
    qc = qc or qdrant_store.client()
    drv = drv or neo4j_store.driver()
    batch_size = batch_size or settings.description_worker_batch

    jobs = tracker.claim_pending_desc_jobs(limit=batch_size)
    if not jobs:
        return {"processed": 0, "succeeded": 0, "failed": 0}

    succeeded = 0
    failed = 0
    for job in jobs:
        code_collection = code_collection_for(job.language)
        desc_collection = desc_collection_for(job.language)
        try:
            payload = qdrant_store.retrieve_payload(qc, code_collection, job.chunk_id)
            if payload is None:
                # The original code chunk vanished (file deleted / re-indexed).
                # Mark the job done so the queue moves on.
                tracker.mark_desc_done(job.chunk_id)
                continue

            entity = _payload_to_entity(payload)
            description = generate_description(entity, llm)
            if not description:
                raise RuntimeError("empty description from LLM")

            _write_description(
                qc, drv,
                code_collection=code_collection,
                desc_collection=desc_collection,
                chunk_id=job.chunk_id,
                payload=payload,
                description=description,
                desc_embedder=desc_embedder,
            )

            tracker.mark_desc_done(job.chunk_id)
            succeeded += 1
        except Exception as exc:
            retry = job.attempts < MAX_ATTEMPTS
            tracker.mark_desc_failed(job.chunk_id, error=str(exc), retry=retry)
            failed += 1
            log.warning(
                "desc_job_failed",
                chunk_id=job.chunk_id, attempts=job.attempts, retry=retry,
                error=str(exc),
            )

    log.info("desc_batch_done", processed=len(jobs), succeeded=succeeded, failed=failed)
    return {"processed": len(jobs), "succeeded": succeeded, "failed": failed}


def run_forever(*, idle_sleep: float = 5.0) -> None:
    """Long-running loop suitable for `python -m scripts.desc_worker`."""
    while True:
        result = process_batch()
        if result["processed"] == 0:
            sleep(idle_sleep)


# ── Helpers ───────────────────────────────────────────────────────────

def _payload_to_entity(payload: dict) -> Entity:
    return Entity(
        qualified_name=payload.get("qualified_name", ""),
        name=payload.get("symbol_name", ""),
        symbol_type=payload.get("symbol_type", "function"),
        file_path=payload.get("file_path", ""),
        line_start=int(payload.get("line_start", 1)),
        line_end=int(payload.get("line_end", 1)),
        content=payload.get("content", ""),
        signature=payload.get("signature"),
        docstring=payload.get("docstring"),
        parent_class=payload.get("parent_class"),
        language=payload.get("language", "typescript"),
    )


def _write_description(
    qc,
    drv,
    *,
    code_collection: str,
    desc_collection: str,
    chunk_id: str,
    payload: dict,
    description: str,
    desc_embedder: DenseEmbedder,
) -> None:
    desc_id = str(uuid4())
    dense = desc_embedder.embed([description])[0]
    sparse = bm25_encoder.encode_one(description)

    # Description points share the *original* chunk's qualified_name + file_path
    # so RRF cross-collection merge can dedupe on chunk_id (the linked-by id).
    desc_payload = {
        **payload,
        "chunk_id": desc_id,
        "linked_chunk_id": chunk_id,  # points back to the code chunk
        "content": description,
        "source_type": "code_description",
        "description_status": "ready",
    }

    qdrant_store.upsert_points(qc, desc_collection, [{
        "id": desc_id,
        "dense": dense,
        "bm25": sparse,
        "payload": desc_payload,
    }])

    qdrant_store.set_payload(qc, code_collection, chunk_id, {
        "description_status": "ready",
        "description_chunk_id": desc_id,
    })

    neo4j_store.set_property_by_chunk_id(drv, chunk_id, "synthetic_description_vi", description)
