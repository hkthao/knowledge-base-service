# Knowledge Base Service

Index codebase đa ngôn ngữ (TypeScript + C#) cùng với docs và issues vào
graph database (Neo4j) và hybrid vector store (Qdrant), sau đó expose
endpoint `/search` có cấu trúc cho n8n agent flow tiêu thụ để compose
bug analysis, RCA document và triage decision.

KB Service = data plane. n8n = agent / control plane. Hai lớp tách bạch,
không lẫn vào nhau.

## Mục lục

- [Trạng thái](#trạng-thái)
- [Quick start](#quick-start)
- [Cấu hình](#cấu-hình)
- [Boundary với n8n](#boundary-với-n8n)
- [Kiến trúc](#kiến-trúc)
- [Tech stack](#tech-stack)
- [Nguồn dữ liệu và độ tin cậy](#nguồn-dữ-liệu-và-độ-tin-cậy)
- [Neo4j graph schema](#neo4j-graph-schema)
- [Qdrant collections và payload](#qdrant-collections-và-payload)
- [Roslyn service](#roslyn-service)
- [Synthetic description tiếng Việt](#synthetic-description-tiếng-việt)
- [Change management](#change-management)
- [Search pipeline](#search-pipeline)
- [Repair và maintenance](#repair-và-maintenance)
- [API reference](#api-reference)
- [Eval methodology](#eval-methodology)
- [Layout](#layout)
- [Tests](#tests)
- [Timeline](#timeline)
- [Scope v1 vs v2](#scope-v1-vs-v2)
- [Rủi ro và phương án](#rủi-ro-và-phương-án)

---

## Trạng thái

Tuần 1–6 trong kế hoạch 11 tuần đã hoàn thành. Service đã có pipeline
indexing đầy đủ cho cả hai ngôn ngữ, async description generation,
change detection từ git, và toàn bộ pipeline `/search`.

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

# Bật infra: Neo4j 5 + Qdrant + Ollama + Roslyn .NET service + Langfuse
docker compose up -d neo4j qdrant ollama roslyn-service

# Pull embedding models (chỉ chạy lần đầu)
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull nomic-embed-code   # ~2GB

# Bootstrap stores: Neo4j constraints + Qdrant collections + SQLite state
python -m scripts.setup_neo4j
python -m scripts.setup_collections

# Index một repo (auto-detect .ts/.tsx/.js/.jsx và .cs)
python -m scripts.initial_index --repo my-project --path /path/to/repo

# Drain hàng đợi sinh description tiếng Việt (chạy lâu dài)
python -m scripts.desc_worker

# Build CO_CHANGED edges ở mức module từ lịch sử git
python -m scripts.build_co_change --path /path/to/repo

# Cron mỗi 15 phút — sweep có giới hạn, không bao giờ O(n)
python -m scripts.repair --repo-path /path/to/repo
```

Với repo C#, KB tự discover `.csproj` chủ sở hữu cho từng file `.cs` qua
`CsprojResolver` — không cần config thêm.

---

## Cấu hình

Đặt các biến sau trong `.env` (xem `.env.example` cho danh sách đầy đủ):

| Biến | Mục đích | Mặc định |
|---|---|---|
| `NEO4J_PASSWORD` | Neo4j auth | `changeme-please` |
| `VOYAGE_API_KEY` | Nếu set, initial index dùng voyage-code-2 (~5–10× nhanh hơn Ollama trên CPU); nếu không thì fallback về Ollama nomic-embed-code | unset |
| `ANTHROPIC_API_KEY` | LLM cho description tiếng Việt (Haiku 4.5) | unset |
| `DESCRIPTION_LLM_BACKEND` | `anthropic` hoặc `ollama` | `anthropic` |
| `INTERNAL_NS_PREFIXES` | Comma-separated C# namespace prefix mà Roslyn vẫn giữ CALLS edge dù symbol nằm trong NuGet metadata | unset |
| `LANGFUSE_HOST`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` | Nếu cả 3 đều set, mỗi `/search` được trace; ngược lại trace là no-op | unset |

Nếu `ANTHROPIC_API_KEY` không set và backend đang là `anthropic`,
description worker fail-fast cho từng job và mark để retry — code chunks
vẫn search được trong `code_*`. Đổi sang `DESCRIPTION_LLM_BACKEND=ollama`
nếu muốn dùng model local.

---

## Boundary với n8n

```text
┌─────────────────────────────────┐     ┌──────────────────────────────┐
│         KB SERVICE              │     │         n8n                  │
│                                 │     │                              │
│  - Index codebase               │     │  - Triage bug description    │
│  - Index docs / issues          │     │  - Quyết định query gì       │
│  - Hybrid search (dense+BM25)   │◄────│  - Gọi /search nhiều lần    │
│  - Graph expand (callers...)    │────►│  - Compose bug analysis doc  │
│  - Incremental update           │     │  - Self-check output         │
│  - Trả structured context JSON  │     │  - Severity gating           │
│                                 │     │  - Tạo Redmine task          │
└─────────────────────────────────┘     └──────────────────────────────┘
```

---

## Kiến trúc

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
   │  │  :5000  (.NET 8)   │   │  drain desc_jobs  │
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

n8n gọi `POST /search` và nhận structured JSON context. Việc n8n làm gì
với context đó — agent loop, severity gate, tạo Redmine — nằm ngoài phạm
vi service này.

---

## Tech stack

### Parsing

| Tool | Mục đích | Runtime | License |
|---|---|---|---|
| `tree-sitter` + `tree-sitter-typescript` | Parse AST cho TS/JS | Python | MIT |
| `Roslyn` (`Microsoft.CodeAnalysis.CSharp`) | Semantic analysis cho C# | **.NET 8** | MIT |
| `Microsoft.Build.Locator` | Load workspace `.csproj` cho Roslyn | .NET 8 | MIT |
| `Docling` (IBM, optional) | Parse PDF / DOCX / HTML | Python | MIT |
| `ripgrep` | Cross-file symbol lookup | Any | MIT |

### Storage

| Tool | Mục đích | License |
|---|---|---|
| `Neo4j Community 5` + APOC | Graph database | GPL-3.0 |
| `neo4j-driver` (Python) | Neo4j client | Apache 2.0 |
| `Qdrant` | Vector DB — dense + BM25 sparse | Apache 2.0 |
| `SQLite` (WAL mode) | Index state tracker | Public Domain |

Nếu GPL-3.0 của Neo4j là vấn đề, **Memgraph** (BSL) và **FalkorDB** (MIT)
dùng cùng Cypher syntax và là drop-in alternative.

### Embedding & search

| Tool | Mục đích | License |
|---|---|---|
| `Ollama` + `nomic-embed-code` | Embedding code local (incremental) | Apache 2.0 |
| `voyage-code-2` API | Initial full index nhanh (~5–10× trên CPU) | Commercial |
| `Ollama` + `nomic-embed-text` | Embedding text local (descriptions, docs) | Apache 2.0 |
| `fastembed` + `Qdrant/bm25` | BM25 sparse encoding | Apache 2.0 |
| `sentence-transformers` + `ms-marco-MiniLM-L-6-v2` | Cross-encoder rerank | Apache 2.0 |

Throughput trên CPU: Ollama embed ~100 file/phút. Dùng `voyage-code-2`
cho initial full index, sau đó incremental dùng Ollama local là đủ.

### API & observability

| Tool | Mục đích | License |
|---|---|---|
| `FastAPI` | REST API | MIT |
| `SQLAlchemy` | ORM cho SQLite state | MIT |
| `Anthropic SDK` | Haiku 4.5 cho description tiếng Việt | Commercial |
| `Langfuse` | Trace mọi `/search` call | MIT |

---

## Nguồn dữ liệu và độ tin cậy

`source_reliability` đi kèm trong payload của mọi chunk — n8n tự chọn
tier nào để tin cậy ở thời điểm query qua tham số `filters`.

| Nguồn | Vai trò | `source_reliability` | Ghi chú |
|---|---|---|---|
| Codebase C# | Code thực tế (~60%) | `high` | Source of truth — Roslyn semantic analysis |
| Codebase TS / JS | Code thực tế (~40%) | `high` | Source of truth — tree-sitter |
| Tài liệu kỹ thuật (Markdown / PDF) | Hành vi đúng | `high` | Mô tả "phải làm gì" |
| Git history / commits | Change context | `medium` | Chất lượng phụ thuộc commit message |
| Tickets (Redmine) | Weak signal | `low` | Chỉ hữu ích để check "đã từng nhắc chưa" + lấy terminology |

---

## Neo4j graph schema

### Node labels

| Label | Thuộc tính chính |
|---|---|
| `Function` | `chunk_id`*, `qualified_name`*, `name`, `file_path`, `line_start`, `line_end`, `signature`, `docstring`, `synthetic_description_vi` |
| `Method` | `chunk_id`*, `qualified_name`*, `name`, `class_name`, `file_path`, `line_start`, `line_end`, `visibility` |
| `Class` | `chunk_id`*, `qualified_name`*, `name`, `file_path`, `is_abstract`, `is_interface` |
| `Module` | `chunk_id`*, `qualified_name`*, `name`, `file_path` |
| `Document` | `chunk_id`*, `title`, `file_path`, `repo`, `is_latest` |
| `Issue` | `chunk_id`*, `issue_id`, `title`, `status`, `source_reliability: "low"` |
| `Commit` | `commit_hash`*, `message`, `author`, `date` |

`*` = unique constraint. `qualified_name` là canonical lookup key —
**không bao giờ query bằng `{name: $name}`**, dễ collision khi hai module
expose symbol cùng tên.

### Convention `qualified_name`

**TypeScript / JavaScript** — scope theo file path:

```text
Function:  src/auth/validator.ts::validateUser
Method:    src/auth/AuthService.ts::AuthService.login
Class:     src/auth/AuthService.ts::AuthService
Module:    src/auth/validator.ts
```

**C#** — scope theo namespace (vì partial class có thể tách qua nhiều
file; namespace + class + member là key duy nhất không mâu thuẫn):

```text
Method:    MyProject.Auth::AuthService.Login
Class:     MyProject.Auth::AuthService
Interface: MyProject.Auth::IAuthService
```

### Relationship types

| Relationship | Từ → Đến | Thuộc tính |
|---|---|---|
| `CALLS` | Function/Method → Function/Method | `confidence: float` |
| `IMPORTS` | Module → Module/Class | `confidence: float` |
| `EXTENDS` | Class → Class | `confidence: float` (1.0 C# semantic, 0.7 TS heuristic) |
| `IMPLEMENTS` | Class → Class | `confidence: float` (cùng rule với EXTENDS) |
| `DEFINES` | Module → Function/Class/Method | — |
| `USES_TYPE` | Function/Method → Class | `confidence: float` |
| `REFERENCES` | Issue → Function/Class | `source_reliability: "low"` |
| `CO_CHANGED` | Module ↔ Module | `count: int, last_seen: date` |
| `TOUCHED_BY` | Commit → Module | — |

**CO_CHANGED ở mức Module, không phải symbol** — git diff không cho
granularity ở mức symbol rẻ tiền. Tín hiệu Module-level đã đủ; nếu sau
này cần view symbol-level, build từ `git blame` thay vì `git log`.

### CALLS confidence

| Resolution | Confidence |
|---|---|
| C# semantic (Roslyn) — bất kỳ call nào | 1.0 |
| TS same-file, name resolved | 0.9 |
| TS cross-file, import resolved | 0.7 |
| TS cross-file, name match heuristic | 0.5 |
| Dynamic dispatch (`this[method]()`) | (không tạo edge — known limitation) |

n8n nhận `confidence` trong payload mọi relation và tự quyết định verify
thêm hay downweight các edge confidence thấp.

### Sample Cypher queries

```cypher
-- Caller chain: ai gọi symbol này?
MATCH (caller)-[:CALLS*1..3]->(fn)
WHERE fn.chunk_id = $chunk_id
RETURN caller.qualified_name, caller.file_path, caller.line_start

-- Callee chain: symbol này gọi gì?
MATCH (fn)-[:CALLS*1..3]->(callee)
WHERE fn.chunk_id = $chunk_id
RETURN callee.qualified_name, callee.file_path

-- Module hay sửa cùng nhau
MATCH (m:Module {qualified_name: $file_path})-[r:CO_CHANGED]-(other:Module)
WHERE r.count >= 3
RETURN other.qualified_name, r.count
ORDER BY r.count DESC

-- Commit gần đây chạm vào file
MATCH (c:Commit)-[:TOUCHED_BY]->(m:Module {qualified_name: $file_path})
RETURN c.message, c.author, c.date
ORDER BY c.date DESC LIMIT 5

-- Class hierarchy
MATCH (cls)-[:EXTENDS|IMPLEMENTS*]->(parent)
WHERE cls.chunk_id = $chunk_id
RETURN parent.qualified_name, labels(parent)
```

---

## Qdrant collections và payload

### Sáu collections

| Collection | Nội dung | Dense model |
|---|---|---|
| `code_ts` | TS/JS function / class — code thật | `nomic-embed-code` |
| `code_ts_desc` | Description nghiệp vụ tiếng Việt cho TS/JS | `nomic-embed-text` |
| `code_cs` | C# method / class — code thật | `nomic-embed-code` |
| `code_cs_desc` | Description nghiệp vụ tiếng Việt cho C# | `nomic-embed-text` |
| `docs` | README, tài liệu kỹ thuật (Markdown, optional PDF/DOCX qua Docling) | `nomic-embed-text` |
| `issues` | Ticket Redmine — flag `source_reliability: low` | `nomic-embed-text` |

**Tại sao tách `code_*` và `code_*_desc`** — search trên code thật phù
hợp với query keyword (tên hàm, exception type). Search trên description
tiếng Việt phù hợp với query nghiệp vụ không trùng vocabulary với code
(ví dụ "kiểm tra hạn mức tín dụng" match `checkCreditLimit`). Hai
collection share `linked_chunk_id` để RRF merge dedup về một result row.

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

Payload index: `file_path`, `repo`, `symbol_type`, `is_latest`,
`source_reliability`, `qualified_name`, `language`, `line_start` (int),
`confidence` (float). Tất cả đều keyword-typed trừ hai cái cuối.

### Hybrid search (dense + BM25 + RRF)

Mỗi collection chạy RRF fusion qua dense pre-fetch (Cosine) và sparse
pre-fetch (BM25). Giữa các collection, search pipeline chạy hybrid trên
từng collection được yêu cầu rồi RRF-merge cross-collection — description
hits được rewrite để `chunk_id` trỏ về linked code chunk's id, dedup về
một result row duy nhất.

---

## Roslyn service

### Tại sao cần service riêng

| | tree-sitter (Python) | Roslyn (.NET) |
|---|---|---|
| Parse syntax | ✅ | ✅ |
| Resolve type của variable | ❌ | ✅ |
| Biết `foo.Bar()` gọi class nào | ❌ | ✅ |
| Cross-file call resolution | ❌ | ✅ |
| Generic type instantiation | ❌ | ✅ |
| Interface → implementation | ❌ | ✅ |
| CALLS confidence | 0.5 (heuristic) | **1.0 (semantic)** |

Roslyn là thư viện .NET, không thể chạy trong Python. Giải pháp: tách
thành microservice `.NET 8`, `kb-indexer` gọi qua HTTP khi gặp file `.cs`.

### Endpoints

```http
POST /analyze/project        # Project đầy đủ — initial index
POST /analyze/file           # Một file — incremental update
POST /cache/invalidate       # Bỏ cache MSBuildWorkspace của một project
GET  /health                 # { status, msbuild_loaded }
```

Project mode load toàn bộ `.csproj` một lần (call đầu tiên chậm, ~2–10
phút tuỳ size project) và cache workspace theo `project_path`. File mode
tiếp theo trên cache nóng chỉ tốn 1–3 giây.

### Cache safety (hai bug bắt buộc phải đúng)

1. **Concurrent loads** — Hai request đồng thời cho cùng project chưa
   cache sẽ cùng gọi `MSBuildWorkspace.Create()` và `OpenProjectAsync` —
   chậm và tốn RAM. Dùng
   `ConcurrentDictionary<string, Lazy<Task<ProjectCacheEntry>>>` để
   request đầu tiên trigger load và request thứ hai chờ cùng task đó.

2. **Stale text** — Sau khi file thay đổi trên disk, `Compilation` cache
   vẫn giữ text cũ. `AnalyzeFileAsync` đọc text hiện tại từ disk và apply
   qua `workspace.TryApplyChanges(updatedSolution)` trước khi extract,
   để semantic relations luôn phản ánh state hiện tại của file.

### Discovery `project_path`

File không tự mang `.csproj` của nó trong path. KB tự lo: `CsprojResolver`
walk-up từ mỗi file `.cs` về `.csproj` chủ sở hữu sâu nhất. n8n không bao
giờ phải gửi `project_path` trong webhook trừ khi muốn override.

### Internal namespace allowlist

Mặc định analyzer drop call vào BCL / public NuGet (location của symbol
là `IsInMetadata`). Với internal NuGet package mà tổ chức tự publish (mã
nguồn nằm ngoài repo), set
`INTERNAL_NS_PREFIXES=MyOrg.Internal,MyOrg.Shared` thì các call này vẫn
trở thành CALLS edge.

---

## Synthetic description tiếng Việt

### Tại sao

Tickets noisy và đôi khi mới có tiếng Việt. Để bridge một query tiếng
Việt như `"kiểm tra hạn mức tín dụng"` về function tên tiếng Anh như
`validateConstraints`, ta sinh 1–2 câu mô tả nghiệp vụ tiếng Việt cho
mỗi symbol và embed vào collection `code_*_desc` song song.

### Cost & throughput

Cho ~50k symbols (codebase 60/40 C#/TS):

| Backend | Throughput | Time | Ghi chú |
|---|---|---|---|
| Haiku 4.5 (API, 5 concurrent) | ~10–15 req/s | ~1 giờ | vài chục USD |
| Qwen2.5-7B local (1 GPU) | ~20 req/s | ~40 phút | free nếu đã có GPU |
| Qwen2.5-7B CPU | ~1 req/s | ~14 giờ | bottleneck — không nên dùng cho initial |

### Async pipeline

```text
index_file ──► Qdrant (code_ts | code_cs)
            ─► Neo4j (Function/Method/Class)
            ─► enqueue DescJob (SQLite, status=pending)

desc_worker ──► claim batch
              ─► generate_description(entity, llm)
              ─► Qdrant (code_*_desc)  với linked_chunk_id → code chunk
              ─► Neo4j set synthetic_description_vi
              ─► flip description_status=ready trên code chunk
              ─► tối đa 3 lần retry; failed jobs được mark, không retry nữa
```

`/index/file` trả về dưới 1 giây; description generation không bao giờ
block indexing. Nếu description fail vĩnh viễn, code chunk vẫn search
được trong `code_*` — chỉ `code_*_desc` thiếu entry. `/stats` báo cáo
queue counts để gap coverage hiện rõ.

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
Code: {500 ký tự đầu}
```

---

## Change management

### Detector

`POST /index/changes` chạy `git diff --name-status -M` giữa hai commit
và sinh ra `ChangeSet` với 4 bucket: modified, added, deleted, renamed.
Filter extension giữ non-source ngoài pipeline.

### Handler — flow idempotent

| Sự kiện | Xử lý |
|---|---|
| MODIFIED | Capture symbol name cũ → re-index → diff cũ vs mới → ripgrep relink referencer |
| ADDED | Re-index → relink referencer (resolve placeholder edge đang trỏ vào symbol chưa indexed) |
| DELETED | Capture name cũ → DETACH DELETE Neo4j + delete khỏi mọi Qdrant collection → mark `status=deleted` trong tracker → relink referencer cũ |
| RENAMED | Treat như DELETE cũ + ADD mới (correctness over cleverness; vẫn đúng cả khi content cũng thay đổi) |

Handler capture symbol set **trước** khi re-index từ Neo4j
(`names_for_file`), để có thể tính symmetric diff của name và chỉ relink
file thực sự reference đến name xuất hiện / biến mất. Name ổn định không
trigger relink.

### Cross-file relink (ripgrep)

```python
def find_referencers(symbol_names: set[str], repo_path: str) -> set[str]:
    pattern = r"\b(?:" + "|".join(re.escape(n) for n in symbol_names) + r")\b"
    args = ["rg", "--files-with-matches", "--regexp", pattern,
            "--type", "ts", "--type", "js", "--type", "cs"]
    ...
```

Pattern alternation word-boundary qua tất cả symbol name trong một lần
gọi ripgrep. Type built-in `ts`/`js`/`cs` của ripgrep đã cover
`.tsx`/`.jsx`/`.cjs`/`.mjs`/`.cts`/`.mts`. Single-pass — relinker
re-index referencer nhưng không trigger relink lần nữa, nên pass kết
thúc.

### Idempotent re-index (thay cho 2-phase commit)

```python
async def reindex_file_idempotent(file_path: str):
    op_id = sync_log.record_intent(file_path, desired_state="indexed")
    try:
        entities, relations = parse_file(file_path)

        # Drop state cũ theo file_path — không cần nhớ ID cũ
        neo4j_store.delete_by_file(file_path)
        qdrant_store.delete_by_file(file_path)

        # Insert state mới
        neo4j_ids = neo4j_store.insert(entities, relations)
        chunk_ids = qdrant_store.upsert_batch(entities)

        file_index.update(file_path, status="indexed", ...)
        sync_log.mark_done(op_id)
    except Exception as e:
        sync_log.mark_failed(op_id, error=str(e))
        file_index.update(file_path, status="failed")
```

Re-run cùng change yields cùng state. `DETACH DELETE` không rollback
được, nên design tránh hẳn 2-phase commit — repair job bên dưới catch
mọi thứ lệch.

### Co-change graph từ git history

Edge module-level, viết theo batch. Threshold `max_files=30` per-commit
loại bỏ commit mass-format / mass-rename — những commit này nếu giữ lại
sẽ làm spam co-occurrence count.

```bash
python -m scripts.build_co_change --path /path/to/repo --min-count 3
```

---

## Search pipeline

```text
POST /search
  └─► hybrid_search        (per collection: dense + BM25 + RRF)
      └─► merge_collection_hits  (RRF cross-collection; desc → code dedup)
          └─► reranker     (cross-encoder ms-marco-MiniLM-L-6-v2, optional)
              └─► graph_expand
                  ├─ callers (1-2 hop, product-of-confidence)
                  ├─ callees
                  ├─ co_changed  (Module-level)
                  ├─ recent_commits (TOUCHED_BY)
                  └─ related_issues (REFERENCES, source_reliability=low)
                      └─► context_packer  (JSON gọn theo plan §9)
```

Description hits được rewrite để `chunk_id` thành id của linked code
chunk, dedup về một result row duy nhất bất kể match qua code hay qua
description tiếng Việt (field `matched_via` chỉ rõ).

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

Khi `LANGFUSE_HOST` + `LANGFUSE_SECRET_KEY` + `LANGFUSE_PUBLIC_KEY`
đều được set, mỗi `/search` ghi trace với input (query, top_k,
collections, filters) và output (result_count). Khi thiếu bất kỳ biến
nào trace trở thành stub — call site dùng cùng context manager trong
cả hai path.

---

## Repair và maintenance

Repair pass có giới hạn — không bao giờ scan toàn bộ file. Chạy cron
mỗi 15 phút:

```python
def repair():
    # 1. Retry các file failed (cap ở failed_limit)
    for f in tracker.query_failed(limit=100):
        reindex_file_idempotent(f.file_path)

    # 2. Drain dirty queue (set bởi webhook / failed ops, có cap)
    for f in tracker.query_dirty(limit=200):
        actual = qdrant_store.count_by_file(f.file_path)
        if actual != len(f.chunk_ids):
            reindex_file_idempotent(f.file_path)

    # 3. Sample 1% record indexed — bắt drift im lặng
    sample = tracker.random_sample_indexed(fraction=0.01)
    for f in sample:
        if qdrant_store.count_by_file(f.file_path) != len(f.chunk_ids):
            tracker.mark_dirty(f.file_path)  # pass tới sẽ xử lý
```

`/stats/consistency` chạy sweep lớn hơn khi muốn xem drift trực tiếp.
Loop 15 phút tin vào dirty flag và 1% sample để dần phát hiện drift
theo thời gian.

---

## API reference

### Indexing

```http
POST /index/repo
Body: {"path": "/repos/my-project", "repo": "my-project"}
→ Full background index của một repo (TS + C# auto-detect)

POST /index/changes
Body: {"repo": "...", "repo_path": "/repos/my-project",
       "since_commit": "abc123", "current_commit": "HEAD"}
→ Run git diff giữa 2 commit, apply M/A/D/R + cross-file relink

POST /index/file
Body: {"file_path": "src/auth/validator.ts", "repo": "my-project"}
      {"file_path": "src/Auth/Validator.cs", "repo": "my-project",
       "repo_root": "/repos/my-project"}    # bắt buộc với .cs auto-resolve
      # `project_path` là override optional cho cả hai
→ Idempotent re-index một file

POST /index/rename
Body: {"old_path": "src/old.ts", "new_path": "src/new.ts", "repo": "..."}

POST /index/delete
Body: {"file_path": "src/old.ts", "repo": "..."}
# POST thay vì DELETE-with-body — nhiều proxy / client strip body của
# DELETE (RFC 7231 không định nghĩa semantics).

POST /index/docs
Body: {"path": "/docs/", "repo": "..."}
→ Background index toàn bộ Markdown / PDF / DOCX trong directory

POST /index/doc/file
Body: {"file_path": "/docs/architecture.md", "repo": "..."}
```

### Search (n8n gọi các endpoint này)

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
→ Lookup chính xác (Neo4j) — dùng khi n8n đã có name

POST /search/callers
Body: {"chunk_id": "...", "max_hops": 2}

POST /search/callees
Body: {"chunk_id": "...", "max_hops": 2}

POST /search/co_changed
Body: {"file_path": "src/auth/validator.ts", "min_count": 3, "limit": 8}
```

### Maintenance

```http
GET  /health                  -- liveness Neo4j + Qdrant + Roslyn
GET  /stats                   -- count per-collection + tổng kết desc-job
GET  /stats/consistency       -- list file mà Qdrant ↔ tracker drift
POST /repair                  -- retry failed, drain dirty, sample sweep
```

---

## Eval methodology

### Golden set từ commits (không dùng tickets)

Tickets noisy. Commit có message như `fix:` / `bug:` và PR description
rõ ràng là ground truth mạnh hơn — diff cho ta biết chính xác file /
function nào fix đã chạm.

```text
Chọn 30–50 commit với message "fix:" / "bug:" rõ ràng.
Với mỗi commit:
  - Ground truth: file / function thay đổi (từ git diff).
  - Query:        commit message làm input.
  - Expected:     /search trả về đúng những file / function đó.
```

### Metrics

| Metric | Mục tiêu |
|---|---|
| Precision@5 | ≥ 0.75 |
| MRR | ≥ 0.70 |
| Hybrid vs dense-only (query keyword) | ≥ +15% |
| Query latency p95 | ≤ 2 giây |
| Indexing throughput | ≥ 100 file/phút (CPU) |
| Incremental re-index 1 file | ≤ 15 giây |
| Consistency `chunk_id` Neo4j ↔ Qdrant | 100% |

### Failure mode catalog

```text
Mỗi case /search miss:
  failure_type: retrieval_miss | wrong_file | graph_wrong | bm25_miss | desc_miss
  notes: lý do cụ thể

Review cuối tuần → top 3 failure mode quyết định fix gì tuần sau.
```

---

## Layout

```text
kb_indexer/                 Python service (port 8000)
  parsers/
    ts_parser.py            tree-sitter cho TS/TSX/JS
    csharp_parser.py        HTTP bridge → roslyn-service
    csproj_resolver.py      .cs file → owning .csproj (deepest wins)
    doc_parser.py           Markdown native; Docling cho PDF/DOCX (optional)
  extractors/
    entity_extractor.py     dispatch theo extension
    relation_extractor.py   resolve intra-file; preserve to_qn từ Roslyn
    commit_extractor.py     git log → Commit nodes + TOUCHED_BY edges
    co_change_builder.py    CO_CHANGED edge module-level (loại mass-format)
  change/
    detector.py             git diff → ChangeSet (M/A/D/R)
    handler.py              apply_changes idempotent + cross-file relink
    relinker.py             ripgrep tìm referencer file để re-index
  query/
    hybrid_search.py        dense + BM25 + RRF cross multi-collection
    cross_collection.py     RRF merge code + description; dedup linked_chunk_id
    graph_expand.py         callers, callees, co_changed, recent_commits, issues
    reranker.py             cross-encoder ms-marco-MiniLM-L-6-v2 (lazy-load)
    context_packer.py       format response /search theo schema bên trên
    search_pipeline.py      orchestrate retrieval → rerank → graph → pack
    filters.py              dict → Qdrant Filter
  stores/
    neo4j_store.py          unique constraint qualified_name, MERGE idempotent
    qdrant_store.py         dense + BM25, RRF fusion, 6 collections
  state/
    models.py               file_index, doc_index, sync_log, desc_jobs
    tracker.py              session SQLite WAL, dirty queue, sampling
  embedder.py               Ollama (incremental) + voyage-code-2 (initial)
  bm25_encoder.py           fastembed Qdrant/bm25
  llm.py                    backend Anthropic Haiku / Ollama cho description
  description_generator.py  prompt tiếng Việt → 1-2 câu mô tả nghiệp vụ
  description_worker.py     drain queue desc_jobs
  indexing.py               pipeline idempotent index_file / index_doc
  repair.py                 retry failed + drain dirty + sample sweep
  tracing.py                Langfuse trace_search context manager (no-op nếu unset)
  api/                      FastAPI routers (health, index, maintenance, search)

roslyn-service/             .NET 8 minimal API (port 5000)
  Program.cs                JSON snake_case, MSBuildLocator.RegisterDefaults
  CSharpAnalyzer.cs         cache MSBuildWorkspace per-project, TryApplyChanges
                            khi analyze incremental file, allowlist internal NS
  Models/                   EntityDto, RelationDto, AnalysisResult

scripts/                    setup + initial index utilities
  setup_neo4j.py            constraints + schema SQLite
  setup_collections.py      Qdrant collections idempotent
  initial_index.py          full repo index (auto-detect TS + C#)
  desc_worker.py            queue drainer chạy lâu (--once cho batch)
  build_co_change.py        ghi CO_CHANGED edge từ lịch sử git
  repair.py                 repair pass cron-friendly chạy một lần

tests/                      67 unit tests (pytest)
```

---

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/
```

67 unit tests cover:

- `ts_parser` — extract entity TS/JS, qualified name, edge CALLS / IMPORTS / EXTENDS
- `csharp_parser_bridge` — Roslyn HTTP bridge (mock qua `httpx.MockTransport`)
- `csproj_resolver` — file → owning `.csproj` (deepest wins) + caching
- `entity_extractor_dispatch` — routing theo extension
- `relation_extractor` — same-file resolution, preserve `to_qn` từ Roslyn
- `state_tracker` — SQLite WAL, dirty flag, sync log
- `doc_parser` — split heading Markdown, chunk theo window
- `description_generator` — build prompt + làm sạch output
- `description_worker` — claim → generate → write → mark done; retry khi fail
- `cross_collection` — RRF fusion, dedup qua `linked_chunk_id`, multi-collection merge
- `change_detector` — `git diff --name-status -M` → ChangeSet (M/A/D/R), filter extension
- `change_handler` — apply idempotent + relink, không double-index, cô lập failure
- `relinker` — ripgrep word-boundary tìm referencer cross `ts/js/cs`
- `commit_extractor` — `git log` → Commit với file đã chạm
- `co_change_builder` — pair module-level ≥ min_count, loại commit mass-format
- `filters` — dict phẳng → Qdrant `Filter` (scalar, list, None handling)
- `context_packer` — drop field None, attach `graph_context` theo `chunk_id`
- `search_pipeline` — shape response packed, dedup description→code, filter pass-through, rerank fallback

Test `relinker` và git-based cần `rg` và `git` trên PATH; auto-skip nếu
thiếu.

---

## Timeline

Tổng cộng 11 tuần. Tách tuần 7–8 là cố ý — eval tuần 7 thường lộ ra vấn
đề chunking strategy hoặc description prompt, fix các thứ này cần
re-index một phần codebase, không nhét được trong cùng một tuần với
chạy eval.

| Tuần | Focus | Done criterion |
|---|---|---|
| 1–2 | Foundation TS + dual stores | graph có `qualified_name` unique, CALLS edge có confidence, `chunk_id` khớp 2 stores |
| 3 | Roslyn service + C# indexing | name dạng `MyProject.Auth::AuthService.Login`; CALLS edge confidence=1.0 (semantic) |
| 4 | Description tiếng Việt + docs | "kiểm tra hạn mức tín dụng" → trả về function tên tiếng Anh |
| 5 | Change management | Rename class → `qualified_name` update đúng cả 2 stores < 30s; không có orphan node |
| 6 | Search & context packing | n8n nhận caller chain + co_changed + related_issues (flag low) trong một call |
| 7 | Eval — build & baseline | Golden set + eval harness chạy được, baseline P@5 / MRR đã ghi |
| 8 | Eval — iterate | P@5 ≥ 0.75 trên golden set commit-based |
| 9 | Hardening | p95 ≤ 2s, throughput ≥ 100 file/phút, repair stress-tested |
| 10–11 | Polish n8n integration | n8n workflow Git webhook → `/index/changes`, runbook re-index / repair |

---

## Scope v1 vs v2

| Feature | v1 | v2 |
|---|---|---|
| TypeScript / JavaScript | ✅ | |
| C# với Roslyn semantic analysis | ✅ | |
| Index Docs + Issues | ✅ | |
| Synthetic description tiếng Việt | ✅ | |
| BM25 hybrid search | ✅ | |
| CO_CHANGED edge | ✅ | |
| Change management idempotent | ✅ | |
| AnythingLLM UI | | ✅ |
| Multi-version doc tracking | | ✅ |
| Dynamic dispatch resolution (C#) | | ✅ |

---

## Rủi ro và phương án

| Rủi ro | Khả năng | Phương án |
|---|---|---|
| GPL-3.0 của Neo4j không phù hợp | Thấp | Memgraph (BSL) hoặc FalkorDB (MIT) — cùng Cypher |
| Ollama quá chậm cho initial index | Cao (CPU) | voyage-code-2 cho initial; Ollama chỉ cho incremental |
| Description chất lượng kém | Trung bình | Few-shot example trong prompt; eval `desc_miss` riêng |
| ripgrep miss dynamic reference | Trung bình | Known limitation, surface trong response metadata |
| SQLite contention khi nhiều webhook | Thấp | WAL mode + dirty-flag queue; SQLAlchemy session-per-op |
| `chunk_id` lệch Neo4j ↔ Qdrant | Trung bình | Repair job + `/stats/consistency` bắt drift |
| Roslyn service tốn RAM | Cao | Limit 2GB, cache LRU per-project, max 2 concurrent |
| MSBuildWorkspace không load được `.csproj` | Trung bình | Cần `dotnet sdk` + restore NuGet trước khi analyze |
| Partial class C# split nhiều file | Trung bình | Roslyn merge qua semantic model; qualified_name namespace-based xử lý đúng |
| Roslyn cold start (15–30s) | Cao | Service luôn running; `/health` check `msbuild_loaded` |
| Cache Roslyn stale sau khi sửa file | Cao | `AnalyzeFileAsync` đọc disk + `TryApplyChanges`; `/cache/invalidate` cho re-index lớn |
| Concurrent request đua nhau load workspace | Trung bình | `ConcurrentDictionary<Lazy<Task<…>>>` collapse về single load |
| Description backlog tồn đọng nhiều ngày | Trung bình | Async queue + `description_status`; `/stats` báo coverage; code search vẫn hoạt động |
| n8n bị ép phải biết `.csproj` của từng `.cs` | Trung bình | KB resolve qua `CsprojResolver`; override qua repo-config nếu cần |
| Mass-format commit phá CO_CHANGED | Trung bình | Loại commit chạm > 30 source file |
