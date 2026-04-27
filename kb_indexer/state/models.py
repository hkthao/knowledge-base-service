from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class FileIndex(Base):
    __tablename__ = "file_index"

    file_path = Column(String, primary_key=True)
    repo = Column(String, nullable=False, index=True)
    content_hash = Column(String, nullable=False)
    commit_hash = Column(String, nullable=True)
    indexed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)
    dirty = Column(Integer, nullable=False, default=0, index=True)
    neo4j_node_ids = Column(JSON, nullable=False, default=list)
    chunk_ids = Column(JSON, nullable=False, default=list)
    error = Column(Text, nullable=True)


class DocIndex(Base):
    __tablename__ = "doc_index"

    file_path = Column(String, primary_key=True)
    content_hash = Column(String, nullable=False)
    version = Column(String, nullable=True)
    is_latest = Column(Integer, nullable=False, default=1)
    chunk_ids = Column(JSON, nullable=False, default=list)
    neo4j_node_ids = Column(JSON, nullable=False, default=list)
    indexed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SyncLog(Base):
    __tablename__ = "sync_log"

    operation_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    file_path = Column(String, nullable=False, index=True)
    intent = Column(JSON, nullable=False, default=dict)
    neo4j_status = Column(String, nullable=True)
    qdrant_status = Column(String, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
