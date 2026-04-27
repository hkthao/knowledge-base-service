import importlib

import pytest


@pytest.fixture
def fresh_tracker(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("STATE_DB_PATH", str(db_path))

    from kb_indexer import settings as settings_module
    settings_module.settings = settings_module.Settings(state_db_path=str(db_path))

    from kb_indexer.state import tracker as tracker_module
    importlib.reload(tracker_module)
    tracker_module.init_schema()
    return tracker_module


def test_upsert_then_get_file(fresh_tracker):
    fresh_tracker.upsert_file(
        file_path="src/a.ts",
        repo="demo",
        content_hash="abc",
        status="indexed",
        chunk_ids=["c1", "c2"],
        neo4j_node_ids=["c1", "c2"],
    )
    rec = fresh_tracker.get_file("src/a.ts")
    assert rec is not None
    assert rec.repo == "demo"
    assert rec.status == "indexed"
    assert rec.chunk_ids == ["c1", "c2"]
    assert rec.dirty == 0


def test_mark_dirty_then_query(fresh_tracker):
    fresh_tracker.upsert_file(
        file_path="src/b.ts",
        repo="demo",
        content_hash="x",
        status="indexed",
        chunk_ids=[],
        neo4j_node_ids=[],
    )
    fresh_tracker.mark_dirty("src/b.ts")
    dirty = fresh_tracker.query_dirty()
    assert any(r.file_path == "src/b.ts" for r in dirty)


def test_sync_log_lifecycle(fresh_tracker):
    op_id = fresh_tracker.record_intent("src/c.ts", "MODIFIED", {"k": "v"})
    fresh_tracker.mark_sync_done(op_id)
