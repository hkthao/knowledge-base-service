from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme-please"

    qdrant_url: str = "http://localhost:6333"

    ollama_url: str = "http://localhost:11434"
    # nomic-embed-code chưa có trên Ollama hub (chỉ trên HuggingFace).
    # Dùng nomic-embed-text cho cả code lẫn text — chất lượng đủ cho domain
    # và tránh phải build custom GGUF. Override qua OLLAMA_CODE_MODEL nếu
    # đã pull một model code-specific (vd: bge-m3, mxbai-embed-large).
    ollama_code_model: str = "nomic-embed-text"
    ollama_text_model: str = "nomic-embed-text"

    voyage_api_key: str = ""
    voyage_code_model: str = "voyage-code-2"

    roslyn_url: str = "http://localhost:5050"
    # Comma-separated list of internal namespace prefixes whose calls we keep
    # even when their symbol locations are in metadata (e.g. shared internal
    # NuGet packages). Forwarded to roslyn-service via INTERNAL_NS_PREFIXES.
    internal_ns_prefixes: str = ""

    description_llm_backend: str = "anthropic"  # anthropic | ollama
    description_llm_model: str = "claude-haiku-4-5"
    anthropic_api_key: str = ""
    description_content_chars: int = 500
    description_worker_batch: int = 16

    state_db_path: str = "data/index_state.db"

    dense_dim: int = 768

    langfuse_host: str = ""
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""

    log_level: str = Field(default="INFO")


settings = Settings()
