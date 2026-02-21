"""Worker service configuration (shares most settings with API)."""
from __future__ import annotations
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CELERY_URL: str = "redis://localhost:6379/1"
    DATABASE_URL: str = "postgresql+asyncpg://cgr_user:changeme@localhost:5432/creator_graphrag"

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme_dev"

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "chunks_multilingual"

    S3_ENDPOINT_URL: str | None = None
    S3_BUCKET_BOOKS: str = "creator-books"
    AWS_ACCESS_KEY_ID: str = "minioadmin"
    AWS_SECRET_ACCESS_KEY: str = "changeme_dev"
    AWS_REGION: str = "us-east-1"

    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None   # e.g. https://zenmux.ai/api/v1
    LLM_EXTRACTION_MODEL: str = "openai/gpt-4.1"
    EMBEDDING_MODEL: str = "qwen3-embedding:8b"
    EMBEDDING_DIMENSION: int = 4096   # qwen3-embedding:8b native output dim
    BGE_M3_ENDPOINT: str | None = None
    OLLAMA_ENDPOINT: str = "http://localhost:11434"

    SARVAM_API_KEY: str | None = None

    OCR_ENGINE: str = "tesseract"
    TESSERACT_DATA_PATH: str = "/usr/share/tessdata"
    OCR_FALLBACK_CONFIDENCE_THRESHOLD: float = 0.60

    LANG_DETECT_CONFIDENCE_THRESHOLD: float = 0.80
    FASTTEXT_MODEL_PATH: str = "./models/lid.176.ftz"

    CHUNK_MAX_CHARS: int = 2000
    CHUNK_OVERLAP_CHARS: int = 250
    SCANNED_PAGE_TEXT_THRESHOLD_CHARS: int = 100
    SCANNED_PAGE_RATIO_THRESHOLD: float = 0.30

    CELERY_WORKER_CONCURRENCY: int = 4
    CELERY_TASK_SOFT_TIME_LIMIT: int = 7200

    OTLP_ENDPOINT: str = "http://localhost:4317"
    ENABLE_OTEL: bool = False


worker_settings = WorkerSettings()
