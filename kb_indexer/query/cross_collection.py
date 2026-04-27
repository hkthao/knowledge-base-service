"""Cross-collection retrieval — merges hits from a code collection
(`code_ts` / `code_cs`) and its sibling description collection
(`code_*_desc`) using Reciprocal Rank Fusion (RRF).

Why both: the code collection is best for keyword/identifier queries
(English function names, exception types). The description collection is
best for natural-language Vietnamese queries that don't share vocabulary
with the code. Description hits are dedup'd back to the original code
chunk via `linked_chunk_id`.
"""

from __future__ import annotations

from typing import Any

# Standard RRF constant from Cormack et al.; not very sensitive between 30–100.
RRF_K = 60


def rrf_merge(ranked_lists: list[list[dict[str, Any]]], *, k: int = RRF_K) -> list[dict[str, Any]]:
    """RRF over multiple ranked lists keyed by `chunk_id`.

    For description hits we want to dedupe back to the *code* chunk, so
    callers should map description hits' `chunk_id` to their
    `linked_chunk_id` before calling. See `merge_code_and_desc_hits`.
    """
    scores: dict[str, float] = {}
    seen: dict[str, dict[str, Any]] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            chunk_id = item.get("chunk_id")
            if not chunk_id:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            seen.setdefault(chunk_id, item)

    merged = []
    for chunk_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        item = dict(seen[chunk_id])
        item["score"] = score
        merged.append(item)
    return merged


def merge_code_and_desc_hits(
    code_hits: list[dict[str, Any]],
    desc_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Description hits get rewritten so their `chunk_id` is the LINKED
    code chunk's id. That way RRF dedupes a query that matches both the
    code AND the description back to a single result row."""
    rewritten_desc: list[dict[str, Any]] = []
    for hit in desc_hits:
        linked = hit.get("linked_chunk_id")
        if not linked:
            continue  # description orphan — shouldn't happen but skip safely
        rewritten = dict(hit)
        rewritten["matched_via"] = "description"
        rewritten["chunk_id"] = linked
        rewritten_desc.append(rewritten)

    for hit in code_hits:
        hit.setdefault("matched_via", "code")

    return rrf_merge([code_hits, rewritten_desc])


def merge_collection_hits(
    hits_by_collection: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Generalised version: takes per-collection hit lists and runs RRF.

    A hit from a `_desc` collection gets rewritten to point at its
    linked code chunk's id, so a single result row carries both signals
    (the `matched_via` field tells the caller which path scored).
    """
    ranked_lists: list[list[dict[str, Any]]] = []
    for collection, hits in hits_by_collection.items():
        is_desc = collection.endswith("_desc")
        rewritten: list[dict[str, Any]] = []
        for hit in hits:
            view = dict(hit)
            view["matched_collection"] = collection
            if is_desc:
                linked = hit.get("linked_chunk_id")
                if not linked:
                    continue
                view["chunk_id"] = linked
                view["matched_via"] = "description"
            else:
                view.setdefault("matched_via", "code" if collection.startswith("code_") else collection)
            rewritten.append(view)
        ranked_lists.append(rewritten)
    return rrf_merge(ranked_lists)
