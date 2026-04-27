# Test fixtures

Hai repo nhỏ dùng cho smoke test KB indexer end-to-end. Cả hai cùng
domain (auth + credit) để test cross-collection retrieval.

## `sample-ts/` — TypeScript

- `src/credit/Customer.ts` — `Customer` interface, `VipCustomer` class
- `src/credit/limits.ts` — `calculateCreditLimit`, `checkCreditLimit`,
  `explainCreditDecision`
- `src/auth/Validator.ts` — `Validator` base class, `StrictValidator`
  extends nó
- `src/auth/AuthService.ts` — class chính; gọi `Validator.validate` và
  `checkCreditLimit` (cross-file CALLS)
- `src/index.ts` — wire-up

Sau khi index, expect graph có:

- 2 class hierarchy edge (`StrictValidator` EXTENDS `Validator`,
  `VipCustomer` IMPLEMENTS `Customer`)
- CALLS edge từ `AuthService.approveLoan` → `checkCreditLimit` (cross-file)
- IMPORTS edge từ `auth/AuthService.ts` → `credit/limits.ts`

## `sample-cs/` — C#

- `SampleApp.csproj`
- `src/Auth/IAuthService.cs` — interface
- `src/Auth/Validator.cs` — `Validator` base + `StrictValidator`
- `src/Auth/AuthService.cs` — implements `IAuthService`, gọi
  `CreditChecker.CheckLimit`
- `src/Credit/CreditChecker.cs` — `Customer` record, `CreditChecker`

Roslyn semantic phải trả CALLS edge với confidence 1.0 cho mọi cross-file
call (`AuthService.ApproveLoan` → `CreditChecker.CheckLimit`).

## Quick test

```bash
# 1. Bật Neo4j + Qdrant trong docker (Ollama đã chạy local)
docker compose up -d neo4j qdrant

# 2. (Optional) Bật roslyn-service nếu test C#
docker compose up -d roslyn-service

# 3. Pull model embedding vào Ollama local (chỉ lần đầu)
ollama pull nomic-embed-code
ollama pull nomic-embed-text

# 4. Bootstrap stores
.venv/bin/python -m scripts.setup_neo4j
.venv/bin/python -m scripts.setup_collections

# 5. Index
.venv/bin/python -m scripts.initial_index --repo sample-ts --path repos/sample-ts --embedder ollama
.venv/bin/python -m scripts.initial_index --repo sample-cs --path repos/sample-cs --embedder ollama

# 6. Index docs
.venv/bin/python -c "
from kb_indexer.indexing import index_docs_dir
print(index_docs_dir(repo='samples', docs_path='documents'))
"

# 7. Drain description queue (cần ANTHROPIC_API_KEY hoặc Ollama LLM)
.venv/bin/python -m scripts.desc_worker --once

# 8. Truy vấn
.venv/bin/uvicorn kb_indexer.api.main:app --reload &
curl -s -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"kiểm tra hạn mức tín dụng","top_k":5,"expand_graph":false,"rerank":false}' \
  | python3 -m json.tool
```

Nếu Ollama không có sẵn `nomic-embed-code` thì initial index sẽ fail
khi gọi embed. Set `--embedder ollama` chỉ làm rõ ý định; mặc định
auto-pick voyage nếu `VOYAGE_API_KEY` set, nếu không sẽ dùng Ollama.
