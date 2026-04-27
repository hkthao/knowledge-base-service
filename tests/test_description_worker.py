"""Worker logic test — fakes Qdrant + Neo4j + LLM so we exercise the
control flow (claim → generate → write → mark done) without live infra."""
import importlib

import pytest


@pytest.fixture
def fresh_state(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    from kb_indexer import settings as settings_module
    settings_module.settings = settings_module.Settings(state_db_path=str(db_path))

    from kb_indexer.state import tracker
    importlib.reload(tracker)
    tracker.init_schema()
    return tracker


class _FakeQdrant:
    def __init__(self, payloads: dict[str, dict]):
        self._payloads = dict(payloads)
        self.upserts: list[tuple[str, list[dict]]] = []
        self.payload_updates: list[tuple[str, str, dict]] = []

    # qdrant_store helpers call these
    def retrieve(self, collection_name, ids, with_payload):
        out = []
        for pid in ids:
            payload = self._payloads.get(pid)
            if payload is not None:
                out.append(_FakePoint(pid, payload))
        return out

    def upsert(self, collection_name, points, wait):
        self.upserts.append((collection_name, list(points)))

    def set_payload(self, collection_name, payload, points, wait):
        for pid in points:
            existing = dict(self._payloads.get(pid, {}))
            existing.update(payload)
            self._payloads[pid] = existing
            self.payload_updates.append((collection_name, pid, payload))


class _FakePoint:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload
        self.score = 0.0


class _FakeNeo4jSession:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *args, **kwargs):
        self._sink.append((args, kwargs))
        class _Cur:
            def consume(self_inner): return None
        return _Cur()


class _FakeNeo4jDriver:
    def __init__(self):
        self.sink: list = []

    def session(self):
        return _FakeNeo4jSession(self.sink)


class _FakeEmbedder:
    dim = 768

    def embed(self, texts):
        return [[0.1] * self.dim for _ in texts]


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return self._text


def test_worker_writes_description_and_marks_done(fresh_state, monkeypatch):
    tracker = fresh_state
    chunk_id = "chunk-credit"
    payload = {
        "chunk_id": chunk_id,
        "qualified_name": "src/credit.ts::checkCreditLimit",
        "symbol_name": "checkCreditLimit",
        "symbol_type": "function",
        "file_path": "src/credit.ts",
        "line_start": 10, "line_end": 25,
        "content": "export function checkCreditLimit(c: Customer) { ... }",
        "signature": "checkCreditLimit(c: Customer): boolean",
        "language": "typescript",
        "repo": "demo",
    }
    fake_qc = _FakeQdrant({chunk_id: payload})
    fake_drv = _FakeNeo4jDriver()

    tracker.enqueue_desc_jobs([{
        "chunk_id": chunk_id,
        "qualified_name": payload["qualified_name"],
        "language": "typescript",
        "repo": "demo",
    }])

    # Stub bm25 encoder to avoid loading the model
    from kb_indexer import description_worker as worker_mod, bm25_encoder
    monkeypatch.setattr(bm25_encoder, "encode_one", lambda t: {"indices": [1], "values": [0.5]})
    monkeypatch.setattr(bm25_encoder, "encode", lambda ts: [{"indices": [1], "values": [0.5]}] * len(ts))

    fake_llm = _FakeLLM("Kiểm tra hạn mức tín dụng của khách hàng.")
    result = worker_mod.process_batch(
        llm=fake_llm,
        desc_embedder=_FakeEmbedder(),
        qc=fake_qc,
        drv=fake_drv,
        batch_size=10,
    )

    assert result == {"processed": 1, "succeeded": 1, "failed": 0}
    assert fake_llm.calls == 1
    # Description was upserted into code_ts_desc
    upsert_collections = [coll for coll, _ in fake_qc.upserts]
    assert upsert_collections == ["code_ts_desc"]
    upserted_payload = fake_qc.upserts[0][1][0].payload  # PointStruct.payload
    assert upserted_payload["linked_chunk_id"] == chunk_id
    assert upserted_payload["content"].startswith("Kiểm tra")
    # Original chunk's description_status flipped to ready
    assert fake_qc._payloads[chunk_id]["description_status"] == "ready"
    # Neo4j set_property got called
    assert fake_drv.sink, "expected at least one Neo4j write"

    counts = tracker.desc_job_counts()
    assert counts.get("done") == 1
    assert counts.get("pending", 0) == 0


def test_worker_failure_marks_pending_for_retry(fresh_state, monkeypatch):
    tracker = fresh_state
    chunk_id = "chunk-fail"
    payload = {
        "chunk_id": chunk_id,
        "qualified_name": "src/x.ts::foo",
        "symbol_name": "foo",
        "symbol_type": "function",
        "content": "x", "language": "typescript", "repo": "demo",
        "file_path": "src/x.ts", "line_start": 1, "line_end": 2,
    }
    fake_qc = _FakeQdrant({chunk_id: payload})
    fake_drv = _FakeNeo4jDriver()

    tracker.enqueue_desc_jobs([{
        "chunk_id": chunk_id,
        "qualified_name": payload["qualified_name"],
        "language": "typescript",
        "repo": "demo",
    }])

    from kb_indexer import description_worker as worker_mod, bm25_encoder
    monkeypatch.setattr(bm25_encoder, "encode_one", lambda t: {"indices": [], "values": []})
    monkeypatch.setattr(bm25_encoder, "encode", lambda ts: [{"indices": [], "values": []}] * len(ts))

    class _BoomLLM:
        def complete(self, prompt):
            raise RuntimeError("rate limit")

    result = worker_mod.process_batch(
        llm=_BoomLLM(),
        desc_embedder=_FakeEmbedder(),
        qc=fake_qc,
        drv=fake_drv,
    )
    assert result["failed"] == 1
    counts = tracker.desc_job_counts()
    # First attempt → still pending for retry
    assert counts.get("pending") == 1
