"""Apply a `ChangeSet` to the index idempotently.

Per plan §10.4:
- MODIFIED → re-index, then relink referencers
- ADDED    → index, then relink referencers (resolves placeholder edges)
- DELETED  → drop from Neo4j + every Qdrant collection
- RENAMED  → delete old + index new (correctness over cleverness; rename
              with content change still produces correct state)

Cross-file relink uses ripgrep to find files that reference any name
that appeared/disappeared from a changed file's symbol set, then
re-indexes them. Single-pass — relinker-triggered re-indexes don't
recurse into another relink.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..embedder import DenseEmbedder, make_embedder
from ..indexing import delete_file_from_stores, index_file
from ..log import get_logger
from ..parsers.csharp_parser import CSharpParser
from ..parsers.csproj_resolver import CsprojResolver
from ..stores import neo4j_store, qdrant_store
from . import relinker
from .detector import ChangeSet

log = get_logger(__name__)


@dataclass
class ChangeApplyResult:
    indexed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    relinked: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "indexed": len(self.indexed),
            "deleted": len(self.deleted),
            "relinked": len(self.relinked),
            "failures": self.failures,
        }


def apply_changes(
    changes: ChangeSet,
    *,
    repo: str,
    repo_path: str,
    embedder: DenseEmbedder | None = None,
    csharp_parser: CSharpParser | None = None,
) -> ChangeApplyResult:
    if changes.is_empty():
        return ChangeApplyResult()

    embedder = embedder or make_embedder()
    qc = qdrant_store.client()
    drv = neo4j_store.driver()

    # Lazy: only build a CsprojResolver if we actually see a .cs file.
    csproj_resolver: CsprojResolver | None = None

    def resolver() -> CsprojResolver:
        nonlocal csproj_resolver
        if csproj_resolver is None:
            csproj_resolver = CsprojResolver(repo_path)
        return csproj_resolver

    parser = csharp_parser
    if any(p.endswith(".cs") for p in changes.all_changed_paths()):
        parser = parser or CSharpParser()

    result = ChangeApplyResult()
    affected_names: set[str] = set()
    processed: set[str] = set()  # files already (re-)indexed in this pass

    # ── Process renames as delete-old + add-new ──────────────────────
    for old_path, new_path in changes.renamed:
        try:
            old_names = neo4j_store.names_for_file(drv, old_path)
            delete_file_from_stores(old_path, qc=qc, drv=drv)
            affected_names.update(old_names)
            result.deleted.append(old_path)
            processed.add(old_path)

            _index_one(
                new_path, repo=repo, embedder=embedder, parser=parser,
                resolver=resolver, qc=qc, drv=drv,
            )
            new_names = neo4j_store.names_for_file(drv, new_path)
            affected_names.update(new_names)
            result.indexed.append(new_path)
            processed.add(new_path)
        except Exception as exc:
            result.failures.append(f"renamed {old_path}->{new_path}: {exc}")

    # ── DELETED ──────────────────────────────────────────────────────
    for path in changes.deleted:
        try:
            old_names = neo4j_store.names_for_file(drv, path)
            delete_file_from_stores(path, qc=qc, drv=drv)
            affected_names.update(old_names)
            result.deleted.append(path)
            processed.add(path)
        except Exception as exc:
            result.failures.append(f"deleted {path}: {exc}")

    # ── ADDED + MODIFIED ─────────────────────────────────────────────
    for path in changes.added + changes.modified:
        try:
            old_names = neo4j_store.names_for_file(drv, path)
            _index_one(
                path, repo=repo, embedder=embedder, parser=parser,
                resolver=resolver, qc=qc, drv=drv,
            )
            new_names = neo4j_store.names_for_file(drv, path)
            # Symmetric difference: only names that appeared or disappeared
            # affect referencers. Stable names don't.
            affected_names.update(old_names ^ new_names)
            result.indexed.append(path)
            processed.add(path)
        except Exception as exc:
            result.failures.append(f"added/modified {path}: {exc}")

    # ── Cross-file relink via ripgrep ────────────────────────────────
    if affected_names:
        candidates = relinker.find_referencers(affected_names, repo_path)
        # Skip files we already touched (avoids redundant work AND prevents
        # the relink pass from cascading into another relink pass).
        for f in candidates - processed:
            try:
                _index_one(
                    f, repo=repo, embedder=embedder, parser=parser,
                    resolver=resolver, qc=qc, drv=drv,
                )
                result.relinked.append(f)
            except Exception as exc:
                result.failures.append(f"relink {f}: {exc}")

    log.info("changes_applied", repo=repo, **result.summary())
    return result


def _index_one(
    file_path: str,
    *,
    repo: str,
    embedder: DenseEmbedder,
    parser: CSharpParser | None,
    resolver,
    qc,
    drv,
) -> None:
    project_path: str | None = None
    if file_path.endswith(".cs"):
        project_path = resolver().resolve(file_path)
    index_file(
        file_path=file_path,
        repo=repo,
        embedder=embedder,
        project_path=project_path,
        csharp_parser=parser,
        qc=qc,
        drv=drv,
    )
