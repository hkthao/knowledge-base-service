# Knowledge Base Service

Index codebase + docs + issues into Neo4j (graph) and Qdrant (hybrid dense+BM25), expose `/search`
to n8n agent flows. See [`kb-full-plan-v4.md`](../mvp/kb-full-plan-v4.md) for the full design.

This repo currently implements **Tuần 1–2: Foundation TS + Dual Stores**.

## Quick start

```bash
cp .env.example .env
docker compose up -d neo4j qdrant ollama

# Pull embedding models (first run only)
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull nomic-embed-code   # ~2GB

# Bootstrap stores
python -m scripts.setup_neo4j
python -m scripts.setup_collections

# Index a TypeScript repo
python -m scripts.initial_index --repo my-project --path /path/to/repo
```

## Layout

```
kb_indexer/
  parsers/        tree-sitter for TS/JS (Roslyn bridge lands week 3)
  extractors/     entity + relation extraction
  stores/         neo4j_store, qdrant_store
  state/          SQLite WAL index tracker
  embedder.py     Ollama dense + voyage-code-2 (initial)
  bm25_encoder.py fastembed BM25
  api/            FastAPI app
scripts/          setup + initial index utilities
tests/            unit tests
```
