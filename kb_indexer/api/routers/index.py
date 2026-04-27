from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ...change.detector import detect_code_changes
from ...change.handler import apply_changes
from ...embedder import make_embedder
from ...indexing import (
    delete_file_from_stores,
    index_doc,
    index_docs_dir,
    index_file,
    index_repo,
)
from ...parsers.csproj_resolver import CsprojResolver

router = APIRouter(prefix="/index")


class IndexFileRequest(BaseModel):
    file_path: str
    repo: str
    # Optional override. KB auto-resolves via CsprojResolver (§7.7) if needed.
    project_path: str | None = None
    repo_root: str | None = None  # required for .cs auto-resolution


class IndexRepoRequest(BaseModel):
    path: str
    repo: str


class DeleteFileRequest(BaseModel):
    file_path: str
    repo: str


@router.post("/file")
def index_file_endpoint(req: IndexFileRequest) -> dict:
    embedder = make_embedder()
    project_path = req.project_path
    if req.file_path.endswith(".cs") and project_path is None:
        if req.repo_root is None:
            return {"error": "repo_root is required for .cs files when project_path is omitted"}
        project_path = CsprojResolver(req.repo_root).resolve(req.file_path)
    return index_file(
        file_path=req.file_path,
        repo=req.repo,
        embedder=embedder,
        project_path=project_path,
    )


@router.post("/repo")
def index_repo_endpoint(req: IndexRepoRequest, background: BackgroundTasks) -> dict:
    embedder = make_embedder()
    background.add_task(index_repo, repo=req.repo, repo_path=req.path, embedder=embedder)
    return {"status": "scheduled", "repo": req.repo, "path": req.path}


class IndexDocsRequest(BaseModel):
    path: str
    repo: str


class IndexDocFileRequest(BaseModel):
    file_path: str
    repo: str


@router.post("/docs")
def index_docs_endpoint(req: IndexDocsRequest, background: BackgroundTasks) -> dict:
    background.add_task(index_docs_dir, repo=req.repo, docs_path=req.path)
    return {"status": "scheduled", "repo": req.repo, "path": req.path}


@router.post("/doc/file")
def index_doc_file_endpoint(req: IndexDocFileRequest) -> dict:
    return index_doc(file_path=req.file_path, repo=req.repo)


class IndexChangesRequest(BaseModel):
    repo: str
    repo_path: str
    since_commit: str
    current_commit: str = "HEAD"


@router.post("/changes")
def index_changes_endpoint(req: IndexChangesRequest) -> dict:
    changes = detect_code_changes(req.repo_path, req.since_commit, req.current_commit)
    result = apply_changes(changes, repo=req.repo, repo_path=req.repo_path)
    return {
        "since_commit": req.since_commit,
        "current_commit": req.current_commit,
        **result.summary(),
    }


class RenameRequest(BaseModel):
    old_path: str
    new_path: str
    repo: str


@router.post("/rename")
def rename_file_endpoint(req: RenameRequest) -> dict:
    """Treat as delete-old + index-new — works correctly even if the
    rename also changed file content."""
    embedder = make_embedder()
    delete_file_from_stores(req.old_path)
    project_path = None
    if req.new_path.endswith(".cs"):
        # Heuristic: derive repo_root by walking up from the new path
        # until we find a .csproj. For tighter control callers can use
        # /index/file directly with project_path.
        from pathlib import Path
        parent = Path(req.new_path).parent
        while parent != parent.parent:
            if any(parent.glob("*.csproj")):
                project_path = CsprojResolver(str(parent)).resolve(req.new_path)
                break
            parent = parent.parent
    index_file(
        file_path=req.new_path,
        repo=req.repo,
        embedder=embedder,
        project_path=project_path,
    )
    return {"old_path": req.old_path, "new_path": req.new_path, "status": "renamed"}


@router.post("/delete")
def delete_file_endpoint(req: DeleteFileRequest) -> dict:
    from ...stores import neo4j_store, qdrant_store
    from ...stores.qdrant_store import CODE_CS, CODE_CS_DESC, CODE_TS, CODE_TS_DESC, DOCS

    drv = neo4j_store.driver()
    qc = qdrant_store.client()
    neo4j_store.delete_by_file(drv, req.file_path)
    # Delete from whichever collection holds it (idempotent; no-op if absent).
    for collection in (CODE_TS, CODE_TS_DESC, CODE_CS, CODE_CS_DESC, DOCS):
        qdrant_store.delete_by_file(qc, collection, req.file_path)
    return {"status": "deleted", "file_path": req.file_path}
