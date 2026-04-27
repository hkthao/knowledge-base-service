# Knowledge Base Service

Index a polyglot codebase (TypeScript + C#) plus its docs and issues into
a graph database (Neo4j) and a hybrid vector store (Qdrant), then expose
a structured `/search` endpoint that n8n agent flows consume to assemble
bug analyses, RCA documents, and triage decisions.

KB Service = data plane. n8n = agent / control plane. They never bleed
into each other.

## Contents

- [Status](#status)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Boundary with n8n](#boundary-with-n8n)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Data sources and reliability](#data-sources-and-reliability)
- [Neo4j graph schema](#neo4j-graph-schema)
- [Qdrant collections and payload](#qdrant-collections-and-payload)
- [Roslyn service](#roslyn-service)
- [Synthetic Vietnamese descriptions](#synthetic-vietnamese-descriptions)
- [Change management](#change-management)
- [Search pipeline](#search-pipeline)
- [Repair and maintenance](#repair-and-maintenance)
- [API reference](#api-reference)
- [Eval methodology](#eval-methodology)
- [Layout](#layout)
- [Tests](#tests)
- [Timeline](#timeline)
- [Scope v1 vs v2](#scope-v1-vs-v2)
- [Risks and mitigations](#risks-and-mitigations)

---

## Status

Weeks 1–6 of the 11-week plan are implemented. The service has end-to-end
indexing for both languages, async description generation, change
detection from git, and the full `/search` pipeline.

- ✅ Tuần 1–2 — Foundation TS/JS + dual stores (Neo4j + Qdrant hybrid)
- ✅ Tuần 3 — Roslyn .NET service + C# semantic analysis
- ✅ Tuần 4 — Vietnamese descriptions (async queue) + Markdown doc indexing
- ✅ Tuần 5 — Change management (git diff, ripgrep relink, repair, /stats)
- ✅ Tuần 6 — `/search` + graph expansion + cross-encoder rerank + Langfuse
- ⏳ Tuần 7–8 — Eval (golden set + iterate)
- ⏳ Tuần 9 — Hardening
- ⏳ Tuần 10–11 — n8n integration polish

---

## Quick start

```bash
cp .env.example .env

# Bring up infra: Neo4j 5 + Qdrant + Ollama + Roslyn .NET service + Langfuse
docker compose up -d neo4j qdrant ollama roslyn-service

# Pull embedding models (first run only)
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull nomic-embed-code   # ~2GB

# Bootstrap stores: Neo4j constraints + Qdrant collections + SQLite state
python -m scripts.setup_neo4j
python -m scripts.setup_collections

# Index a repo (auto-detects .ts/.tsx/.js/.jsx and .cs files)
python -m scripts.initial_index --repo my-project --path /path/to/repo

# Drain the Vietnamese description queue (long-running)
python -m scripts.desc_worker

# Build module-level CO_CHANGED edges from git history
python -m scripts.build_co_change --path /path/to/repo

# Cron every 15 minutes — bounded sweep, never O(n)
python -m scripts.repair --repo-path /path/to/repo
```

For C# repos KB auto-discovers each `.cs` file's owning `.csproj` via
`CsprojResolver` — no extra config required.

---

## Configuration

Set these in `.env` (see `.env.example` for the full list):

| Var | Purpose | Default |
|---|---|---|
| `NEO4J_PASSWORD` | Neo4j auth | `changeme-please` |
| `VOYAGE_API_KEY` | If set, initial index uses voyage-code-2 (~5–10× faster than Ollama on CPU); otherwise falls back to local Ollama nomic-embed-code | unset |
| `ANTHROPIC_API_KEY` | Vietnamese description generator (Haiku 4.5) | unset |
| `DESCRIPTION_LLM_BACKEND` | `anthropic` or `ollama` | `anthropic` |
| `INTERNAL_NS_PREFIXES` | Comma-separated C# namespace prefixes whose calls Roslyn keeps even when the symbol's source lives in NuGet metadata only | unset |
| `LANGFUSE_HOST`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` | If all three set, every `/search` call is traced; otherwise tracing is a no-op | unset |

If `ANTHROPIC_API_KEY` is unset and the backend is `anthropic`, the
description worker fails fast on each job and marks them for retry —
code chunks remain searchable in `code_*` either way. Switch to
`DESCRIPTION_LLM_BACKEND=ollama` to use a local model instead.

---

## Boundary with n8n

```text
┌─────────────────────────────────┐     ┌──────────────────────────────┐
│         KB SERVICE              │     │         n8n                  │
│                                 │     │                              │
│  - Index codebase               │     │  - Triage bug description    │
│  - Index docs / issues          │     │  - Decide what to query      │
│  - Hybrid search (dense+BM25)   │◄────│  - Call /search multiple     │
│  - Graph expand (callers...)    │────►│    times                     │
│  - Incremental update           │     │  - Compose bug analysis doc  │
│  - Return structured JSON       │     │  - Self-check output         │
│                                 │     │  - Severity gating           │
│                                 │     │  - Create Redmine task       │
└─────────────────────────────────┘     └──────────────────────────────┘
```

KB Service has **no `/analyze` endpoint**. KB Service knows nothing about
the bug-doc schema or the agent flow. It indexes data and returns context.
That's it.

---

## Architecture

```text
┌──────────────────────────────────────────────────────────┐
│                  KB SERVICE  :8000                       │
│  Python — FastAPI                                        │
│  parsers (tree-sitter) → extractors → embedder           │
│  state tracker (SQLite WAL, desc_jobs queue)             │
└──┬─────────────────┬──────────────────┬──────────────────┘
   │                 │                  │
   │  ┌──────────────▼─────┐   ┌────────▼──────────┐
   │  │  roslyn-service    │   │  desc-worker      │
   │  │  :5000  (.NET 8)   │   │  drains desc_jobs │
   │  │  C# semantic       │   │  → code_*_desc    │
   │  │  analysis          │   │  (Haiku / Ollama) │
   │  └────────────────────┘   └───────────────────┘
   │
┌──▼────────┐                    ┌────────────┐
│  Neo4j 5  │     chunk_id       │  Qdrant    │
│  Graph DB │◄──────────────────►│  Vector DB │
│ :7474/7687│                    │  :6333     │
└───────────┘                    └────────────┘
```

n8n calls `POST /search` and receives a structured JSON context. Whatever
n8n does with that context — agent loops, severity gating, Redmine
creation — is outside this service.

---

## Tech stack

### Parsing

| Tool | Purpose | Runtime | License |
|---|---|---|---|
| `tree-sitter` + `tree-sitter-typescript` | Parse AST for TS/JS | Python | MIT |
| `Roslyn` (`Microsoft.CodeAnalysis.CSharp`) | Semantic analysis for C# | **.NET 8** | MIT |
| `Microsoft.Build.Locator` | Load `.csproj` workspace for Roslyn | .NET 8 | MIT |
| `Docling` (IBM, optional) | Parse PDF / DOCX / HTML | Python | MIT |
| `ripgrep` | Cross-file symbol lookup | Any | MIT |

### Storage

| Tool | Purpose | License |
|---|---|---|
| `Neo4j Community 5` + APOC | Graph database | GPL-3.0 |
| `neo4j-driver` (Python) | Neo4j client | Apache 2.0 |
| `Qdrant` | Vector DB — dense + BM25 sparse | Apache 2.0 |
| `SQLite` (WAL mode) | Index state tracker | Public Domain |

If GPL-3.0 is a blocker on Neo4j, **Memgraph** (BSL) and **FalkorDB**
(MIT) speak the same Cypher and are drop-in alternatives.

### Embedding & search

| Tool | Purpose | License |
|---|---|---|
| `Ollama` + `nomic-embed-code` | Local code embeddings (incremental) | Apache 2.0 |
| `voyage-code-2` API | Fast initial full index (~5–10× faster on CPU) | Commercial |
| `Ollama` + `nomic-embed-text` | Local text embeddings (descriptions, docs) | Apache 2.0 |
| `fastembed` + `Qdrant/bm25` | BM25 sparse encoding | Apache 2.0 |
| `sentence-transformers` + `ms-marco-MiniLM-L-6-v2` | Cross-encoder rerank | Apache 2.0 |

Throughput on CPU: Ollama embeds at ~100 files/min. Use `voyage-code-2`
for initial full index, then run incremental updates against local Ollama.

### API & observability

| Tool | Purpose | License |
|---|---|---|
| `FastAPI` | REST API | MIT |
| `SQLAlchemy` | ORM for SQLite state | MIT |
| `Anthropic SDK` | Haiku 4.5 for Vietnamese descriptions | Commercial |
| `Langfuse` | Trace every `/search` call | MIT |

---

## Data sources and reliability

`source_reliability` rides in every chunk's payload — n8n picks which
tier to trust at query time via the `filters` parameter.

| Source | Role | `source_reliability` | Notes |
|---|---|---|---|
| C# codebase | ~60% of code | `high` | Source of truth — Roslyn semantic analysis |
| TS / JS codebase | ~40% of code | `high` | Source of truth — tree-sitter parsing |
| Technical docs (Markdown / PDF) | Authoritative behavior | `high` | What the system *should* do |
| Git history / commits | Change context | `medium` | Quality scales with commit-message hygiene |
| Tickets (Redmine) | Weak signal | `low` | Only useful for "has this been mentioned before" + terminology |

---

## Neo4j graph schema

### Node labels

| Label | Key properties |
|---|---|
| `Function` | `chunk_id`*, `qualified_name`*, `name`, `file_path`, `line_start`, `line_end`, `signature`, `docstring`, `synthetic_description_vi` |
| `Method` | `chunk_id`*, `qualified_name`*, `name`, `class_name`, `file_path`, `line_start`, `line_end`, `visibility` |
| `Class` | `chunk_id`*, `qualified_name`*, `name`, `file_path`, `is_abstract`, `is_interface` |
| `Module` | `chunk_id`*, `qualified_name`*, `name`, `file_path` |
| `Document` | `chunk_id`*, `title`, `file_path`, `repo`, `is_latest` |
| `Issue` | `chunk_id`*, `issue_id`, `title`, `status`, `source_reliability: "low"` |
| `Commit` | `commit_hash`*, `message`, `author`, `date` |

`*` = unique constraint. `qualified_name` is the canonical lookup key —
**never query by `{name: $name}`**, that collides whenever two modules
expose a same-named symbol.

### `qualified_name` convention

**TypeScript / JavaScript** — file path scoped:

```text
Function:  src/auth/validator.ts::validateUser
Method:    src/auth/AuthService.ts::AuthService.login
Class:     src/auth/AuthService.ts::AuthService
Module:    src/auth/validator.ts
```

**C#** — namespace scoped (because partial classes can split across
multiple files; namespace + class + member is the only unambiguous key):

```text
Method:    MyProject.Auth::AuthService.Login
Class:     MyProject.Auth::AuthService
Interface: MyProject.Auth::IAuthService
```

### Relationship types

| Relationship | From → To | Properties |
|---|---|---|
| `CALLS` | Function/Method → Function/Method | `confidence: float` |
| `IMPORTS` | Module → Module/Class | `confidence: float` |
| `EXTENDS` | Class → Class | `confidence: float` (1.0 C# semantic, 0.7 TS heuristic) |
| `IMPLEMENTS` | Class → Class | `confidence: float` (same rules as EXTENDS) |
| `DEFINES` | Module → Function/Class/Method | — |
| `USES_TYPE` | Function/Method → Class | `confidence: float` |
| `REFERENCES` | Issue → Function/Class | `source_reliability: "low"` |
| `CO_CHANGED` | Module ↔ Module | `count: int, last_seen: date` |
| `TOUCHED_BY` | Commit → Module | — |

**CO_CHANGED is module-level, not symbol-level** — git diff doesn't
expose per-symbol granularity cheaply. Module signal is enough; if a
symbol-level view is later needed, build it from `git blame` rather than
`git log`.

### CALLS confidence

| Resolution | Confidence |
|---|---|
| C# semantic (Roslyn) — any call | 1.0 |
| TS same-file, name resolved | 0.9 |
| TS cross-file, import resolved | 0.7 |
| TS cross-file, name match heuristic | 0.5 |
| Dynamic dispatch (`this[method]()`) | (no edge — known limitation) |

n8n receives `confidence` in every relation payload and decides whether
to verify further or downweight low-confidence edges.

### Sample Cypher queries

```cypher
-- Caller chain: who calls this symbol?
MATCH (caller)-[:CALLS*1..3]->(fn)
WHERE fn.chunk_id = $chunk_id
RETURN caller.qualified_name, caller.file_path, caller.line_start

-- Callee chain: what does this symbol call?
MATCH (fn)-[:CALLS*1..3]->(callee)
WHERE fn.chunk_id = $chunk_id
RETURN callee.qualified_name, callee.file_path

-- Co-changed modules
MATCH (m:Module {qualified_name: $file_path})-[r:CO_CHANGED]-(other:Module)
WHERE r.count >= 3
RETURN other.qualified_name, r.count
ORDER BY r.count DESC

-- Recent commits touching a file
MATCH (c:Commit)-[:TOUCHED_BY]->(m:Module {qualified_name: $file_path})
RETURN c.message, c.author, c.date
ORDER BY c.date DESC LIMIT 5

-- Class hierarchy
MATCH (cls)-[:EXTENDS|IMPLEMENTS*]->(parent)
WHERE cls.chunk_id = $chunk_id
RETURN parent.qualified_name, labels(parent)
```

---

## Qdrant collections and payload

### Six collections

| Collection | Content | Dense model |
|---|---|---|
| `code_ts` | TS/JS functions / classes — actual source code | `nomic-embed-code` |
| `code_ts_desc` | Vietnamese business descriptions of TS/JS symbols | `nomic-embed-text` |
| `code_cs` | C# methods / classes — actual source code | `nomic-embed-code` |
| `code_cs_desc` | Vietnamese business descriptions of C# symbols | `nomic-embed-text` |
| `docs` | README, technical docs (Markdown, optional PDF/DOCX via Docling) | `nomic-embed-text` |
| `issues` | Redmine tickets — flagged `source_reliability: low` | `nomic-embed-text` |

**Why split `code_*` and `code_*_desc`** — search on raw code is best
for keyword-style queries (function names, exception types). Search on
Vietnamese descriptions is best for business-language queries that
don't share vocabulary with the code (e.g. "kiểm tra hạn mức tín dụng"
matches `checkCreditLimit`). Both share `linked_chunk_id` so RRF merge
dedupes back to a single result row.

### Payload schema

```json
{
  "chunk_id": "uuid-v4",
  "qualified_name": "src/auth/validator.ts::validateUser",
  "content": "export async function validateUser(user: User)...",
  "source_type": "code | code_description | doc | issue",
  "source_reliability": "high | medium | low",
  "language": "typescript | csharp | vi",
  "symbol_name": "validateUser",
  "symbol_type": "function | method | class | interface | module",
  "parent_class": null,
  "file_path": "src/auth/validator.ts",
  "repo": "my-project",
  "line_start": 45,
  "line_end": 62,
  "is_latest": true,
  "indexed_at": "2026-04-27T08:00:00Z",
  "description_status": "pending | ready | failed",
  "linked_chunk_id": null
}
```

Payload indexes: `file_path`, `repo`, `symbol_type`, `is_latest`,
`source_reliability`, `qualified_name`, `language`, `line_start` (int),
`confidence` (float). All keyword-typed except the last two.

### Hybrid search (dense + BM25 + RRF)

Each collection runs RRF fusion over a dense pre-fetch (Cosine) and a
sparse pre-fetch (BM25). Across collections, the search pipeline runs
this on each requested collection, then RRF-merges across collections —
description hits get rewritten so their `chunk_id` points at the linked
code chunk's id, dedup'ing back to a single result row.

---

## Roslyn service

### Why a separate service

| | tree-sitter (Python) | Roslyn (.NET) |
|---|---|---|
| Parse syntax | ✅ | ✅ |
| Resolve type of a variable | ❌ | ✅ |
| Know what class `foo.Bar()` calls | ❌ | ✅ |
| Cross-file call resolution | ❌ | ✅ |
| Generic type instantiation | ❌ | ✅ |
| Interface → implementation | ❌ | ✅ |
| CALLS confidence | 0.5 (heuristic) | **1.0 (semantic)** |

Roslyn is .NET-only. The solution is a `.NET 8` microservice that
`kb-indexer` calls over HTTP whenever it sees a `.cs` file.

### Endpoints

```http
POST /analyze/project        # Full project — initial index
POST /analyze/file           # Single file — incremental update
POST /cache/invalidate       # Drop cached MSBuildWorkspace for a project
GET  /health                 # { status, msbuild_loaded }
```

Project mode loads the whole `.csproj` once (slow first call, ~2–10
minutes depending on project size) and caches the workspace per
`project_path`. Subsequent file mode calls are 1–3 seconds against the
warm cache.

### Cache safety (the two bugs that have to be right)

1. **Concurrent loads** — Two simultaneous requests for the same
   uncached project would both call `MSBuildWorkspace.Create()` and
   `OpenProjectAsync` — slow and memory-heavy. We use
   `ConcurrentDictionary<string, Lazy<Task<ProjectCacheEntry>>>` so the
   first request triggers the load and the second waits on the same task.

2. **Stale text** — After a file changes on disk, the cached
   `Compilation` still reflects old text. `AnalyzeFileAsync` reads the
   current text from disk and applies it via
   `workspace.TryApplyChanges(updatedSolution)` before re-extracting,
   so semantic relations always reflect the file's current state.

### project_path discovery

Files don't carry their `.csproj` in the path. KB owns this resolution:
`CsprojResolver` walks up from each `.cs` file to the deepest owning
`.csproj`. n8n never sends `project_path` in webhooks unless it
explicitly wants to override.

### Internal namespace allowlist

By default the analyzer drops calls into BCL / public NuGet (the symbol
location is `IsInMetadata`). For internal NuGet packages the
organisation publishes (whose source still lives outside the repo),
set `INTERNAL_NS_PREFIXES=MyOrg.Internal,MyOrg.Shared` and those calls
become CALLS edges anyway.

---

## Synthetic Vietnamese descriptions

### Why

Tickets are noisy and only sometimes Vietnamese. To bridge a Vietnamese
query like `"kiểm tra hạn mức tín dụng"` to an English-named function
like `validateConstraints`, we generate a 1–2 sentence Vietnamese
business description per symbol and embed it into a sibling
`code_*_desc` collection.

### Cost & throughput

For ~50k symbols (a 60/40 C#/TS codebase):

| Backend | Throughput | Time | Notes |
|---|---|---|---|
| Haiku 4.5 (API, 5 concurrent) | ~10–15 req/s | ~1 hour | a few tens of USD |
| Qwen2.5-7B local (1 GPU) | ~20 req/s | ~40 min | free if you have the GPU |
| Qwen2.5-7B CPU | ~1 req/s | ~14 hours | bottleneck — don't use for initial |

### Async pipeline

```text
index_file ──► Qdrant (code_ts | code_cs)
            ─► Neo4j (Function/Method/Class)
            ─► enqueue DescJob (SQLite, status=pending)

desc_worker ──► claim batch
              ─► generate_description(entity, llm)
              ─► Qdrant (code_*_desc)  with linked_chunk_id → code chunk
              ─► Neo4j set synthetic_description_vi
              ─► flip code chunk's description_status=ready
              ─► up to 3 attempts; failed jobs marked, not retried
```

`/index/file` returns in under a second; description generation never
blocks indexing. If a description fails permanently, the code chunk
stays searchable in `code_*` — only `code_*_desc` lacks it. `/stats`
reports the queue counts so coverage gaps are visible.

### Prompt

```text
Viết 1-2 câu tiếng Việt mô tả nghiệp vụ (business semantics) của
hàm/lớp dưới đây.
Tập trung vào: làm gì theo góc nhìn nghiệp vụ, khi nào dùng đến.
Không giải thích kỹ thuật, không nhắc tên biến/tên hàm.
Chỉ trả về 1-2 câu tiếng Việt.

Tên: {symbol_name}
Loại: {symbol_type}
Signature: {signature}
Docstring: {docstring}
Code: {first 500 chars}
```

---

## Change management

### Detector

`POST /index/changes` runs `git diff --name-status -M` between two
commits and produces a `ChangeSet` with four buckets: modified, added,
deleted, renamed. Extension filter keeps non-source out of the pipeline.

### Handler — idempotent flows

| Event | Handling |
|---|---|
| MODIFIED | Capture old symbol names → re-index → diff old vs new names → ripgrep relink referencers |
| ADDED | Re-index → relink referencers (resolves placeholder edges that pointed at the not-yet-indexed symbol) |
| DELETED | Capture old names → DETACH DELETE from Neo4j + delete from every Qdrant collection → mark `status=deleted` in tracker → relink old referencers |
| RENAMED | Treat as DELETE old + ADD new (correctness over cleverness; works even when content also changed) |

The handler captures the **pre-change** symbol set from Neo4j
(`names_for_file`) before re-indexing, so it can compute the symmetric
difference of names and only relink files that actually reference an
appeared / disappeared name. Stable names trigger no relink.

### Cross-file relink (ripgrep)

```python
def find_referencers(symbol_names: set[str], repo_path: str) -> set[str]:
    pattern = r"\b(?:" + "|".join(re.escape(n) for n in symbol_names) + r")\b"
    args = ["rg", "--files-with-matches", "--regexp", pattern,
            "--type", "ts", "--type", "js", "--type", "cs"]
    ...
```

Word-boundary alternation pattern across all symbol names in one
ripgrep call. Built-in ripgrep types `ts`/`js`/`cs` already cover
`.tsx`/`.jsx`/`.cjs`/`.mjs`/`.cts`/`.mts`. Single-pass — the relinker
re-indexes referencers but does not trigger another relink, so the
pass terminates.

### Idempotent re-index (replaces 2-phase commit)

```python
async def reindex_file_idempotent(file_path: str):
    op_id = sync_log.record_intent(file_path, desired_state="indexed")
    try:
        entities, relations = parse_file(file_path)

        # Drop old state by file_path — no need to remember IDs
        neo4j_store.delete_by_file(file_path)
        qdrant_store.delete_by_file(file_path)

        # Insert new state
        neo4j_ids = neo4j_store.insert(entities, relations)
        chunk_ids = qdrant_store.upsert_batch(entities)

        file_index.update(file_path, status="indexed", ...)
        sync_log.mark_done(op_id)
    except Exception as e:
        sync_log.mark_failed(op_id, error=str(e))
        file_index.update(file_path, status="failed")
```

Re-running the same change yields the same state. `DETACH DELETE` can't
be rolled back, so the design avoids a 2-phase commit entirely — the
repair job below catches anything that gets out of sync.

### Co-change graph from git history

Module-level edges, written in batch. A per-commit `max_files=30`
threshold drops mass-format / mass-rename commits that would otherwise
spam co-occurrence counts.

```bash
python -m scripts.build_co_change --path /path/to/repo --min-count 3
```

---

## Search pipeline

```text
POST /search
  └─► hybrid_search        (per collection: dense + BM25 + RRF)
      └─► merge_collection_hits  (RRF across collections; desc → code dedup)
          └─► reranker     (cross-encoder ms-marco-MiniLM-L-6-v2, optional)
              └─► graph_expand
                  ├─ callers (1-2 hops, product-of-confidence)
                  ├─ callees
                  ├─ co_changed  (Module-level)
                  ├─ recent_commits (TOUCHED_BY)
                  └─ related_issues (REFERENCES, source_reliability=low)
                      └─► context_packer  (tight JSON per plan §9)
```

Description hits get rewritten so their `chunk_id` is the linked code
chunk's id, dedup'ing back to a single result row whether the match
came via code or via Vietnamese description (`matched_via` field
indicates which).

### Response shape

```json
{
  "query": "validateUser throw exception",
  "results": [
    {
      "chunk_id": "uuid",
      "qualified_name": "src/auth/validator.ts::validateUser",
      "symbol_name": "validateUser",
      "symbol_type": "function",
      "language": "typescript",
      "content": "...",
      "file_path": "src/auth/validator.ts",
      "line_start": 45,
      "line_end": 62,
      "repo": "my-project",
      "source_reliability": "high",
      "matched_via": "code",
      "matched_collection": "code_ts",
      "score": 0.92,
      "rerank_score": 0.81,
      "graph_context": {
        "callers": [
          {"qualified_name": "...", "file_path": "...", "line_start": 12,
           "hops": 1, "confidence": 0.9}
        ],
        "callees": [...],
        "co_changed": [
          {"qualified_name": "...", "file_path": "...",
           "count": 7, "last_seen": "2026-04-12T10:30:00Z"}
        ],
        "recent_commits": [
          {"commit_hash": "...", "message": "fix: ...",
           "author": "...", "date": "..."}
        ],
        "related_issues": [
          {"issue_id": "...", "title": "...",
           "source_reliability": "low"}
        ]
      }
    }
  ]
}
```

### Langfuse tracing

When `LANGFUSE_HOST` + `LANGFUSE_SECRET_KEY` + `LANGFUSE_PUBLIC_KEY` are
all set, every `/search` call writes a trace with input (query, top_k,
collections, filters) and output (result_count). When any are unset the
trace becomes a stub — call sites use the same context manager either way.

---

## Repair and maintenance

The repair pass is bounded — it never scans every file. Run it on a
15-minute cron:

```python
def repair():
    # 1. Retry failed (capped at failed_limit)
    for f in tracker.query_failed(limit=100):
        reindex_file_idempotent(f.file_path)

    # 2. Drain dirty queue (set by webhooks / failed ops, capped)
    for f in tracker.query_dirty(limit=200):
        actual = qdrant_store.count_by_file(f.file_path)
        if actual != len(f.chunk_ids):
            reindex_file_idempotent(f.file_path)

    # 3. Sample 1% of indexed records — catch silent drift
    sample = tracker.random_sample_indexed(fraction=0.01)
    for f in sample:
        if qdrant_store.count_by_file(f.file_path) != len(f.chunk_ids):
            tracker.mark_dirty(f.file_path)  # next pass handles it
```

`/stats/consistency` does the bigger sweep when you want to look at
divergence directly. The 15-minute loop trusts the dirty flag and the
1% sample to find drift over time.

---

## API reference

### Indexing

```http
POST /index/repo
Body: {"path": "/repos/my-project", "repo": "my-project"}
→ Full background index of a repo (TS + C# auto-detected)

POST /index/changes
Body: {"repo": "...", "repo_path": "/repos/my-project",
       "since_commit": "abc123", "current_commit": "HEAD"}
→ Run git diff between the two commits, apply M/A/D/R + cross-file relink

POST /index/file
Body: {"file_path": "src/auth/validator.ts", "repo": "my-project"}
      {"file_path": "src/Auth/Validator.cs", "repo": "my-project",
       "repo_root": "/repos/my-project"}    # required for .cs auto-resolve
      # `project_path` is an optional override for either
→ Idempotent re-index of one file

POST /index/rename
Body: {"old_path": "src/old.ts", "new_path": "src/new.ts", "repo": "..."}

POST /index/delete
Body: {"file_path": "src/old.ts", "repo": "..."}
# POST instead of DELETE-with-body — many proxies / clients strip
# DELETE bodies (RFC 7231 leaves the semantics undefined).

POST /index/docs
Body: {"path": "/docs/", "repo": "..."}
→ Background index of all Markdown / PDF / DOCX under a directory

POST /index/doc/file
Body: {"file_path": "/docs/architecture.md", "repo": "..."}
```

### Search (n8n calls these)

```http
POST /search
Body: {
  "query": "validateUser throw exception",
  "collections": ["code_ts", "code_ts_desc", "docs"],   # optional, default = all
  "top_k": 10,
  "expand_graph": true,
  "rerank": true,
  "filters": {
    "repo": "my-project",
    "source_reliability": "high"
  }
}

POST /search/symbol
Body: {"qualified_name": "src/auth/validator.ts::validateUser"}
→ Exact lookup (Neo4j) — useful when n8n already has a name

POST /search/callers
Body: {"chunk_id": "...", "max_hops": 2}

POST /search/callees
Body: {"chunk_id": "...", "max_hops": 2}

POST /search/co_changed
Body: {"file_path": "src/auth/validator.ts", "min_count": 3, "limit": 8}
```

### Maintenance

```http
GET  /health                  -- Neo4j + Qdrant + Roslyn liveness
GET  /stats                   -- per-collection counts + desc-job summary
GET  /stats/consistency       -- list files where Qdrant ↔ tracker drift
POST /repair                  -- retry failed, drain dirty, sample sweep
```

---

## Eval methodology

### Golden set from commits (not tickets)

Tickets are noisy. Commits with messages like `fix:` / `bug:` and clear
PR descriptions are stronger ground truth — the diff tells us exactly
which files / functions the fix touched.

```text
Pick 30–50 commits with clear "fix:" / "bug:" messages.
For each commit:
  - Ground truth: files / functions changed (from git diff).
  - Query:        commit message as input.
  - Expected:     /search returns those files / functions.
```

### Metrics

| Metric | Target |
|---|---|
| Precision@5 | ≥ 0.75 |
| MRR | ≥ 0.70 |
| Hybrid vs dense-only (keyword queries) | ≥ +15% |
| Query latency p95 | ≤ 2 seconds |
| Indexing throughput | ≥ 100 files/min (CPU) |
| Incremental re-index 1 file | ≤ 15 seconds |
| `chunk_id` consistency Neo4j ↔ Qdrant | 100% |

### Failure mode catalog

```text
For each /search miss:
  failure_type: retrieval_miss | wrong_file | graph_wrong | bm25_miss | desc_miss
  notes: specific reason

End-of-week review → top 3 failure modes inform next week's fixes.
```

---

## Layout

```text
kb_indexer/                 Python service (port 8000)
  parsers/
    ts_parser.py            tree-sitter for TS/TSX/JS
    csharp_parser.py        HTTP bridge → roslyn-service
    csproj_resolver.py      .cs file → owning .csproj (deepest wins)
    doc_parser.py           Markdown native; Docling for PDF/DOCX (optional)
  extractors/
    entity_extractor.py     dispatch by extension
    relation_extractor.py   intra-file resolution; preserves Roslyn to_qn
    commit_extractor.py     git log → Commit nodes + TOUCHED_BY edges
    co_change_builder.py    module-level CO_CHANGED edges (mass-format filtered)
  change/
    detector.py             git diff → ChangeSet (M/A/D/R)
    handler.py              idempotent apply_changes + cross-file relink
    relinker.py             ripgrep finds referencer files for re-index
  query/
    hybrid_search.py        dense + BM25 + RRF across multiple collections
    cross_collection.py     RRF merge code + description; dedupe via linked_chunk_id
    graph_expand.py         callers, callees, co_changed, recent_commits, issues
    reranker.py             ms-marco-MiniLM-L-6-v2 cross-encoder (lazy-loaded)
    context_packer.py       formats /search response per the schema above
    search_pipeline.py      orchestrates retrieval → rerank → graph → pack
    filters.py              dict → Qdrant Filter
  stores/
    neo4j_store.py          unique constraints on qualified_name, idempotent MERGE
    qdrant_store.py         dense + BM25, RRF fusion, 6 collections
  state/
    models.py               file_index, doc_index, sync_log, desc_jobs
    tracker.py              SQLite WAL session, dirty queue, sampling
  embedder.py               Ollama (incremental) + voyage-code-2 (initial)
  bm25_encoder.py           fastembed Qdrant/bm25
  llm.py                    Anthropic Haiku / Ollama backends for descriptions
  description_generator.py  Vietnamese prompt → 1-2 câu mô tả nghiệp vụ
  description_worker.py     drains the desc_jobs queue
  indexing.py               idempotent index_file / index_doc pipelines
  repair.py                 failed retry + dirty drain + sample sweep
  tracing.py                Langfuse trace_search context manager (no-op if unset)
  api/                      FastAPI routers (health, index, maintenance, search)

roslyn-service/             .NET 8 minimal API (port 5000)
  Program.cs                snake_case JSON, MSBuildLocator.RegisterDefaults
  CSharpAnalyzer.cs         per-project MSBuildWorkspace cache, TryApplyChanges
                            on incremental file analysis, internal-NS allowlist
  Models/                   EntityDto, RelationDto, AnalysisResult

scripts/                    setup + initial index utilities
  setup_neo4j.py            constraints + SQLite schema
  setup_collections.py      idempotent Qdrant collections
  initial_index.py          full repo index (auto-detects TS + C#)
  desc_worker.py            long-running queue drainer (--once for batch)
  build_co_change.py        write CO_CHANGED edges from git history
  repair.py                 cron-friendly one-shot repair pass

tests/                      67 unit tests (pytest)
```

---

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/
```

67 unit tests covering:

- `ts_parser` — TS/JS entity extraction, qualified names, CALLS / IMPORTS / EXTENDS edges
- `csharp_parser_bridge` — Roslyn HTTP bridge (mocked via `httpx.MockTransport`)
- `csproj_resolver` — file → owning `.csproj` (deepest wins) + caching
- `entity_extractor_dispatch` — extension routing
- `relation_extractor` — same-file resolution, preserves Roslyn-resolved `to_qn`
- `state_tracker` — SQLite WAL, dirty flag, sync log
- `doc_parser` — Markdown heading split, windowed chunking
- `description_generator` — prompt construction + output cleanup
- `description_worker` — claim → generate → write → mark done; retry on failure
- `cross_collection` — RRF fusion, dedupe via `linked_chunk_id`, multi-collection merge
- `change_detector` — `git diff --name-status -M` → ChangeSet (M/A/D/R), extension filter
- `change_handler` — idempotent apply + relink, no double-indexing, failure isolation
- `relinker` — ripgrep word-boundary referencer search across `ts/js/cs`
- `commit_extractor` — `git log` → Commits with touched files
- `co_change_builder` — module-level pairs ≥ min_count, drops mass-format commits
- `filters` — flat dict → Qdrant `Filter` (scalar, list, None handling)
- `context_packer` — drops None fields, attaches `graph_context` per `chunk_id`
- `search_pipeline` — packed response shape, description→code dedup, filter pass-through, rerank fallback

The `relinker` and git-based tests need `rg` and `git` on PATH; they
auto-skip when those aren't available.

---

## Timeline

11 weeks total. Week 7–8 split is deliberate — eval week 7 typically
exposes a chunking-strategy or description-prompt issue, and fixing
those requires re-indexing part of the codebase, which doesn't fit in a
single week alongside running eval.

| Week | Focus | Done criterion |
|---|---|---|
| 1–2 | Foundation TS + dual stores | graph has unique `qualified_name`, CALLS edges have confidence, `chunk_id` matches both stores |
| 3 | Roslyn service + C# indexing | `MyProject.Auth::AuthService.Login`-style names; CALLS edges confidence=1.0 (semantic) |
| 4 | Vietnamese descriptions + docs | "kiểm tra hạn mức tín dụng" → returns the English-named function |
| 5 | Change management | Rename class → `qualified_name` updated in both stores in <30s; no orphan nodes |
| 6 | Search & context packing | n8n receives caller chain + co_changed + related_issues (flagged low) in one call |
| 7 | Eval — build & baseline | Golden set + eval harness running, baseline P@5 / MRR recorded |
| 8 | Eval — iterate | P@5 ≥ 0.75 on commit-based golden set |
| 9 | Hardening | p95 ≤ 2s, throughput ≥ 100 files/min, repair stress-tested |
| 10–11 | n8n integration polish | n8n workflow Git webhook → `/index/changes`, runbook for re-index / repair |

---

## Scope v1 vs v2

| Feature | v1 | v2 |
|---|---|---|
| TypeScript / JavaScript | ✅ | |
| C# with Roslyn semantic analysis | ✅ | |
| Docs + Issues indexing | ✅ | |
| Synthetic Vietnamese descriptions | ✅ | |
| BM25 hybrid search | ✅ | |
| CO_CHANGED edges | ✅ | |
| Idempotent change management | ✅ | |
| AnythingLLM UI | | ✅ |
| Multi-version doc tracking | | ✅ |
| Dynamic dispatch resolution (C#) | | ✅ |

---

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Neo4j GPL-3.0 incompatibility | Low | Memgraph (BSL) or FalkorDB (MIT) — same Cypher |
| Ollama too slow for initial index | High (CPU) | voyage-code-2 for initial; Ollama for incremental only |
| Synthetic descriptions low quality | Medium | Few-shot prompt examples; eval `desc_miss` failure mode separately |
| ripgrep misses dynamic references | Medium | Known limitation, surfaced in response metadata |
| SQLite contention under webhook bursts | Low | WAL mode + dirty-flag queue; SQLAlchemy session-per-op |
| `chunk_id` divergence Neo4j ↔ Qdrant | Medium | Repair job + `/stats/consistency` catch drift |
| Roslyn service RAM pressure | High | 2 GB container limit, LRU per-project cache, max 2 concurrent |
| MSBuildWorkspace can't load `.csproj` | Medium | Need `dotnet sdk` + restored NuGet before analyze |
| Partial classes split across files | Medium | Roslyn merges via semantic model; namespace-based qualified_name handles it |
| Roslyn cold start (15–30s) | High | Service stays running; `/health` checks `msbuild_loaded` |
| Roslyn cache stale after edit | High | `AnalyzeFileAsync` reads disk + `TryApplyChanges`; `/cache/invalidate` for big re-indexes |
| Concurrent requests racing to load workspace | Medium | `ConcurrentDictionary<Lazy<Task<…>>>` collapses to single load |
| Description backlog stalls for days | Medium | Async queue + `description_status`; `/stats` shows coverage; code search keeps working |
| n8n forced to know `.csproj` per file | Medium | KB resolves via `CsprojResolver`; override via repo-config if needed |
| Mass-format commits poison CO_CHANGED | Medium | Drop commits touching >30 source files |
