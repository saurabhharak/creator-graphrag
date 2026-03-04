"""Celery task: main ingestion job dispatcher."""
from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from app.worker import app
from app.core.config import worker_settings
from app.infrastructure.db import fetch_book_title, format_error, update_job_status
from app.pipelines.ingestion_pipeline import IngestionConfig, IngestionPipeline
from app.tasks.canonicalize_graph import canonicalize_concepts  # Fix 7

logger = structlog.get_logger(__name__)


@app.task(
    bind=True,
    name="app.tasks.ingest.run_ingestion",
    max_retries=3,
    default_retry_delay=60,
    queue="default",
    acks_late=True,
    soft_time_limit=540,
    time_limit=600,
)
def run_ingestion(self, job_id: str, book_id: str, config_dict: dict) -> dict:
    """Main ingestion task. Dispatches to IngestionPipeline.

    Lifecycle:
        1. Mark job running + store Celery task ID in DB.
        2. Run the pipeline (pre_extracted_sarvam or pdf path).
        3. On transient error: exponential-backoff retry (max 3 attempts).
        4. On permanent error: mark job failed with error detail.

    Args:
        job_id: String UUID of the IngestionJob row.
        book_id: String UUID of the Book row.
        config_dict: Dict representation of IngestionConfig (from Celery JSON serializer).

    Returns:
        Dict with job_id and final status for Celery result backend.
    """
    db_url = worker_settings.DATABASE_URL

    # ── Mark as running ────────────────────────────────────────────────────────
    asyncio.run(
        update_job_status(
            db_url,
            job_id,
            status="running",
            stage="upload",
            celery_task_id=self.request.id,
        )
    )
    logger.info("ingestion_started", job_id=job_id, book_id=book_id, task_id=self.request.id)

    # ── Run pipeline ───────────────────────────────────────────────────────────
    config = IngestionConfig(**config_dict)

    # Fix 3: fetch book title so the LLM extractor and embedder know the source
    book_title = asyncio.run(fetch_book_title(db_url, book_id))
    logger.info("ingestion_book_title_resolved", job_id=job_id, book_title=book_title or "(not found)")

    pipeline = IngestionPipeline(
        job_id=UUID(job_id),
        book_id=UUID(book_id),
        config=config,
        book_title=book_title,
    )

    try:
        asyncio.run(pipeline.run())
        logger.info("ingestion_succeeded", job_id=job_id)

        # Fix 7: enqueue cross-lingual entity resolution after graph build.
        # countdown=10s lets Neo4j writes fully commit before the task reads back.
        if config.build_graph:
            canonicalize_concepts.apply_async(
                args=[book_id],
                countdown=10,
                queue="graph",
            )
            logger.info("canonicalize_enqueued", book_id=book_id)

        return {"job_id": job_id, "status": "completed"}

    except Exception as exc:
        logger.error(
            "ingestion_failed",
            job_id=job_id,
            book_id=book_id,
            error=str(exc),
            exc_info=True,
        )

        # Transient errors get retried with exponential back-off
        is_transient = isinstance(exc, (ConnectionError, TimeoutError, OSError))
        if is_transient and self.request.retries < self.max_retries:
            countdown = 60 * (2 ** self.request.retries)
            logger.warning(
                "ingestion_retry",
                job_id=job_id,
                attempt=self.request.retries + 1,
                countdown=countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)

        # Permanent failure: write error detail to DB
        asyncio.run(
            update_job_status(
                db_url,
                job_id,
                status="failed",
                message=f"{type(exc).__name__}: {str(exc)[:200]}",
                error_json=format_error(exc),
            )
        )
        raise
