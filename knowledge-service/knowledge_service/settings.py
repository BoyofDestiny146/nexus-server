"""Knowledge-service settings. Env prefix KS_."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KS_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8090
    log_level: str = "info"

    # Same named volume as careconnect-api (cc-voice → /data). Read-only in compose.
    source_dir: str = "/data/knowledge-sources"

    ollama_url: str = "http://host-gateway:11434"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_timeout_s: float = 60.0
    embed_batch_size: int = 16
    vector_size: int = 768

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "nexus_knowledge"
    qdrant_timeout_s: float = 30.0


settings = Settings()
