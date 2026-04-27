"""Handler orchestration test — fakes Neo4j/Qdrant/index_file to verify
the apply_changes control flow without live infra. The detector and
indexing pipelines have their own tests."""

import pytest

from kb_indexer.change.detector import ChangeSet
from kb_indexer.change.handler import apply_changes


class _FakeEmbedder:
    dim = 768

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


@pytest.fixture
def fake_world(monkeypatch):
    """Stub everything the handler reaches outside its own module."""
    state = {
        "neo4j_names_for_file": {},  # path -> current set of names (mutated on index)
        "post_index_names": {},  # path -> names after re-index simulates a change
        "indexed_calls": [],
        "deleted_calls": [],
        "ripgrep_returns": set(),
    }

    from kb_indexer.change import handler as handler_mod
    from kb_indexer.stores import neo4j_store, qdrant_store
    from kb_indexer import indexing

    # Avoid Neo4j/Qdrant connections entirely
    monkeypatch.setattr(neo4j_store, "driver", lambda: None)
    monkeypatch.setattr(qdrant_store, "client", lambda: None)
    monkeypatch.setattr(neo4j_store, "names_for_file",
                        lambda drv, path: set(state["neo4j_names_for_file"].get(path, set())))

    def fake_index_file(*, file_path, repo, embedder, project_path=None,
                       csharp_parser=None, qc=None, drv=None):
        state["indexed_calls"].append(file_path)
        post = state["post_index_names"].get(file_path)
        if post is not None:
            state["neo4j_names_for_file"][file_path] = set(post)
        return {"file_path": file_path, "entities": 1, "chunks": 1}

    def fake_delete(file_path, *, qc=None, drv=None):
        state["deleted_calls"].append(file_path)
        state["neo4j_names_for_file"].pop(file_path, None)

    monkeypatch.setattr(handler_mod, "index_file", fake_index_file)
    monkeypatch.setattr(handler_mod, "delete_file_from_stores", fake_delete)
    monkeypatch.setattr(indexing, "index_file", fake_index_file)
    monkeypatch.setattr(indexing, "delete_file_from_stores", fake_delete)

    # Stub the embedder factory and ripgrep
    monkeypatch.setattr(handler_mod, "make_embedder", lambda: _FakeEmbedder())
    from kb_indexer.change import relinker
    monkeypatch.setattr(relinker, "find_referencers", lambda names, repo_path: state["ripgrep_returns"])

    return state


def test_modified_triggers_index_then_relink(fake_world):
    # Simulate a real change: oldFn was renamed to newFn in a.ts.
    fake_world["neo4j_names_for_file"]["/repo/src/a.ts"] = {"oldFn"}
    fake_world["post_index_names"]["/repo/src/a.ts"] = {"newFn"}
    fake_world["ripgrep_returns"] = {"/repo/src/b.ts", "/repo/src/c.ts"}

    cs = ChangeSet(modified=["/repo/src/a.ts"])
    result = apply_changes(cs, repo="demo", repo_path="/repo")

    # a.ts re-indexed first, then b/c relinked. Order matters.
    assert fake_world["indexed_calls"][0] == "/repo/src/a.ts"
    assert set(fake_world["indexed_calls"][1:]) == {"/repo/src/b.ts", "/repo/src/c.ts"}
    assert "/repo/src/a.ts" in result.indexed
    assert sorted(result.relinked) == ["/repo/src/b.ts", "/repo/src/c.ts"]


def test_deleted_drops_file_then_relinks_old_referencers(fake_world):
    fake_world["neo4j_names_for_file"]["/repo/src/auth.ts"] = {"login", "logout"}
    fake_world["ripgrep_returns"] = {"/repo/src/api.ts"}

    cs = ChangeSet(deleted=["/repo/src/auth.ts"])
    result = apply_changes(cs, repo="demo", repo_path="/repo")

    assert "/repo/src/auth.ts" in fake_world["deleted_calls"]
    # api.ts must be re-indexed so its placeholder edges drop / re-resolve
    assert "/repo/src/api.ts" in fake_world["indexed_calls"]
    assert "/repo/src/auth.ts" in result.deleted


def test_renamed_handled_as_delete_old_plus_index_new(fake_world):
    fake_world["neo4j_names_for_file"]["/repo/old.ts"] = {"foo"}

    cs = ChangeSet(renamed=[("/repo/old.ts", "/repo/new.ts")])
    result = apply_changes(cs, repo="demo", repo_path="/repo")

    assert "/repo/old.ts" in fake_world["deleted_calls"]
    assert "/repo/new.ts" in fake_world["indexed_calls"]
    assert "/repo/old.ts" in result.deleted
    assert "/repo/new.ts" in result.indexed


def test_relinker_does_not_double_index_already_processed_files(fake_world):
    """If the same file appears in both `modified` and ripgrep results,
    the relink pass must skip it — otherwise we'd re-index in a loop."""
    fake_world["neo4j_names_for_file"]["/repo/src/x.ts"] = {"foo"}
    fake_world["post_index_names"]["/repo/src/x.ts"] = {"bar"}  # symbol changed
    fake_world["ripgrep_returns"] = {"/repo/src/x.ts", "/repo/src/y.ts"}

    cs = ChangeSet(modified=["/repo/src/x.ts"])
    apply_changes(cs, repo="demo", repo_path="/repo")

    # x.ts indexed once (by modified handling), y.ts indexed once (by relink)
    counts = {p: fake_world["indexed_calls"].count(p) for p in fake_world["indexed_calls"]}
    assert counts["/repo/src/x.ts"] == 1
    assert counts["/repo/src/y.ts"] == 1


def test_empty_changeset_is_a_noop(fake_world):
    result = apply_changes(ChangeSet(), repo="demo", repo_path="/repo")
    assert result.indexed == []
    assert result.deleted == []
    assert fake_world["indexed_calls"] == []
    assert fake_world["deleted_calls"] == []


def test_failure_is_recorded_not_raised(fake_world, monkeypatch):
    from kb_indexer.change import handler as handler_mod

    def boom(**kwargs):
        raise RuntimeError("parse failed")

    monkeypatch.setattr(handler_mod, "index_file", boom)
    cs = ChangeSet(modified=["/repo/src/broken.ts"])
    result = apply_changes(cs, repo="demo", repo_path="/repo")
    assert result.failures
    assert any("parse failed" in f for f in result.failures)
    # Other files (none here) still process; handler doesn't bail.
