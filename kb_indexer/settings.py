from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme-please"

    qdrant_url: str = "http://localhost:6333"

    ollama_url: str = "http://localhost:11434"
    ollama_code_model: str = "nomic-embed-code"
    ollama_text_model: str = "nomic-embed-text"

    voyage_api_key: str = ""
    voyage_code_model: str = "voyage-code-2"

    state_db_path: str = "data/index_state.db"

    dense_dim: int = 768

    langfuse_host: str = ""
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""

    log_level: str = Field(default="INFO")


settings = Settings()
