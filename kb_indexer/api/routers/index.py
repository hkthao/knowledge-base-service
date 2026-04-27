from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ...embedder import make_embedder
from ...indexing import index_file, index_repo
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


@router.post("/delete")
def delete_file_endpoint(req: DeleteFileRequest) -> dict:
    from ...stores import neo4j_store, qdrant_store
    from ...stores.qdrant_store import CODE_CS, CODE_TS

    drv = neo4j_store.driver()
    qc = qdrant_store.client()
    neo4j_store.delete_by_file(drv, req.file_path)
    # Delete from whichever code collection holds it (idempotent; no-op if absent).
    qdrant_store.delete_by_file(qc, CODE_TS, req.file_path)
    qdrant_store.delete_by_file(qc, CODE_CS, req.file_path)
    return {"status": "deleted", "file_path": req.file_path}
