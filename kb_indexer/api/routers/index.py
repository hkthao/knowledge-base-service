from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ...embedder import make_embedder
from ...indexing import index_doc, index_docs_dir, index_file, index_repo
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
