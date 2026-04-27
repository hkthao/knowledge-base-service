from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(UTC)

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from ..settings import settings
from .models import Base, DocIndex, FileIndex, SyncLog


def _engine_for(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


_engine = _engine_for(settings.state_db_path)
_Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_schema() -> None:
    Base.metadata.create_all(_engine)


@contextmanager
def session() -> Iterator[Session]:
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ── File index helpers ─────────────────────────────────────────────────

def get_file(file_path: str) -> FileIndex | None:
    with session() as s:
        return s.execute(select(FileIndex).where(FileIndex.file_path == file_path)).scalar_one_or_none()


def upsert_file(
    *,
    file_path: str,
    repo: str,
    content_hash: str,
    status: str,
    chunk_ids: list[str],
    neo4j_node_ids: list[Any],
    commit_hash: str | None = None,
    error: str | None = None,
) -> None:
    with session() as s:
        existing = s.get(FileIndex, file_path)
        if existing is None:
            s.add(FileIndex(
                file_path=file_path,
                repo=repo,
                content_hash=content_hash,
                commit_hash=commit_hash,
                status=status,
                chunk_ids=chunk_ids,
                neo4j_node_ids=neo4j_node_ids,
                error=error,
                indexed_at=_now(),
                dirty=0,
            ))
        else:
            existing.repo = repo
            existing.content_hash = content_hash
            existing.commit_hash = commit_hash
            existing.status = status
            existing.chunk_ids = chunk_ids
            existing.neo4j_node_ids = neo4j_node_ids
            existing.error = error
            existing.indexed_at = _now()
            existing.dirty = 0


def mark_dirty(file_path: str) -> None:
    with session() as s:
        existing = s.get(FileIndex, file_path)
        if existing is not None:
            existing.dirty = 1


def query_dirty(limit: int = 200) -> list[FileIndex]:
    with session() as s:
        return list(s.execute(
            select(FileIndex).where(FileIndex.dirty == 1).limit(limit),
        ).scalars())


def query_failed(limit: int = 100) -> list[FileIndex]:
    with session() as s:
        return list(s.execute(
            select(FileIndex).where(FileIndex.status == "failed").limit(limit),
        ).scalars())


# ── Sync log helpers ───────────────────────────────────────────────────

def record_intent(file_path: str, event_type: str, intent: dict) -> str:
    op_id = str(uuid4())
    with session() as s:
        s.add(SyncLog(
            operation_id=op_id,
            event_type=event_type,
            file_path=file_path,
            intent=intent,
            started_at=_now(),
        ))
    return op_id


def mark_sync_done(op_id: str, *, neo4j_status: str = "ok", qdrant_status: str = "ok") -> None:
    with session() as s:
        entry = s.get(SyncLog, op_id)
        if entry is not None:
            entry.neo4j_status = neo4j_status
            entry.qdrant_status = qdrant_status
            entry.completed_at = _now()


def mark_sync_failed(op_id: str, error: str) -> None:
    with session() as s:
        entry = s.get(SyncLog, op_id)
        if entry is not None:
            entry.error = error
            entry.completed_at = _now()


# ── Doc index ──────────────────────────────────────────────────────────

def upsert_doc(
    *,
    file_path: str,
    content_hash: str,
    chunk_ids: list[str],
    neo4j_node_ids: list[Any],
    version: str | None = None,
    is_latest: bool = True,
) -> None:
    with session() as s:
        existing = s.get(DocIndex, file_path)
        if existing is None:
            s.add(DocIndex(
                file_path=file_path,
                content_hash=content_hash,
                chunk_ids=chunk_ids,
                neo4j_node_ids=neo4j_node_ids,
                version=version,
                is_latest=int(is_latest),
            ))
        else:
            existing.content_hash = content_hash
            existing.chunk_ids = chunk_ids
            existing.neo4j_node_ids = neo4j_node_ids
            existing.version = version
            existing.is_latest = int(is_latest)
            existing.indexed_at = _now()
