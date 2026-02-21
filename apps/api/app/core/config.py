"""Application configuration loaded from environment variables."""
from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_NAME: str = "creator-graphrag"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_WORKERS: int = 1
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Database
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "creator_graphrag"
    POSTGRES_USER: str = "cgr_user"
    POSTGRES_PASSWORD: str
    DATABASE_URL: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CELERY_URL: str = "redis://localhost:6379/1"

    # Qdrant
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "chunks_multilingual"
    QDRANT_API_KEY: str | None = None

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str

    # S3 / MinIO
    S3_ENDPOINT_URL: str | None = None
    S3_BUCKET_BOOKS: str = "creator-books"
    S3_BUCKET_EXPORTS: str = "creator-exports"
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AWS_REGION: str = "us-east-1"

    # Auth
    JWT_PRIVATE_KEY_PATH: str = "./certs/private.pem"
    JWT_PUBLIC_KEY_PATH: str = "./certs/public.pem"
    JWT_ALGORITHM: str = "RS256"
    JWT_ACCESS_TOKEN_TTL_MINUTES: int = 15
    JWT_REFRESH_TOKEN_TTL_DAYS: int = 7

    # LLM
    LLM_PROVIDER: Literal["openai", "azure"] = "openai"
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None  # e.g. https://zenmux.ai/api/v1
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_API_KEY: str | None = None
    AZURE_OPENAI_API_VERSION: str = "2024-02-01"
    LLM_EXTRACTION_MODEL: str = "gpt-4o"
    LLM_GENERATION_MODEL: str = "gpt-4o"
    LLM_REPAIR_MODEL: str = "gpt-4o-mini"

    # Embeddings
    EMBEDDING_MODEL: Literal["bge-m3", "text-embedding-3-large", "qwen3-embedding:8b"] = "qwen3-embedding:8b"
    EMBEDDING_DIMENSION: int = 4096
    OLLAMA_ENDPOINT: str = "http://localhost:11434"
    BGE_M3_ENDPOINT: str | None = None

    # OCR
    OCR_ENGINE: Literal["tesseract", "azure_vision", "sarvam"] = "tesseract"
    TESSERACT_DATA_PATH: str = "/usr/share/tessdata"
    AZURE_VISION_ENDPOINT: str | None = None
    AZURE_VISION_KEY: str | None = None
    OCR_FALLBACK_CONFIDENCE_THRESHOLD: float = 0.60

    # Document Intelligence (Sarvam AI — Indic-language scanned PDF extraction)
    SARVAM_API_KEY: str | None = None

    # Language Detection
    LANG_DETECT_CONFIDENCE_THRESHOLD: float = 0.80
    FASTTEXT_MODEL_PATH: str = "./models/lid.176.ftz"

    # Ingestion
    MAX_BOOK_SIZE_MB: int = 500
    MAX_CONCURRENT_JOBS_PER_USER: int = 2
    CHUNK_MAX_CHARS: int = 2000
    CHUNK_OVERLAP_CHARS: int = 250
    SCANNED_PAGE_TEXT_THRESHOLD_CHARS: int = 100
    SCANNED_PAGE_RATIO_THRESHOLD: float = 0.30

    # Rate Limits
    RATE_LIMIT_GENERATE_PER_HOUR: int = 10
    RATE_LIMIT_SEARCH_PER_MINUTE: int = 60
    RATE_LIMIT_INGEST_PER_HOUR: int = 5

    # Observability
    OTLP_ENDPOINT: str = "http://localhost:4317"
    ENABLE_OTEL: bool = False

    # Celery
    CELERY_WORKER_CONCURRENCY: int = 4
    CELERY_TASK_SOFT_TIME_LIMIT: int = 7200

    # URL Expiry
    PRESIGNED_UPLOAD_URL_TTL_MINUTES: int = 15
    PRESIGNED_DOWNLOAD_URL_TTL_HOURS: int = 1
    EXPORT_DOWNLOAD_URL_TTL_HOURS: int = 24

    # Reranking
    RERANK_WEIGHT_VECTOR: float = 0.5
    RERANK_WEIGHT_CITATION: float = 0.2
    RERANK_WEIGHT_CHUNK_TYPE: float = 0.2
    RERANK_WEIGHT_CHAPTER_PROXIMITY: float = 0.1

    # Citation
    CITATION_REPAIR_MODE: Literal[
        "remove_paragraph", "label_interpretation", "fail_generation"
    ] = "label_interpretation"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def build_database_url(cls, v: str, info) -> str:
        if v:
            return v
        data = info.data
        user = data.get("POSTGRES_USER", "cgr_user")
        password = data.get("POSTGRES_PASSWORD", "")
        host = data.get("POSTGRES_HOST", "localhost")
        port = data.get("POSTGRES_PORT", 5432)
        db = data.get("POSTGRES_DB", "creator_graphrag")
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


settings = Settings()
