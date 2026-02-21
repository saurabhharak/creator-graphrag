"""Lightweight Celery task sender for the API service.

The API never imports worker task code directly — it dispatches tasks by name
string via ``send_task()``. This keeps the API and worker deployment fully
decoupled (separate Docker images, different Python environments).

Usage:
    from app.infrastructure.celery_client import enqueue_ingestion
    task_id = enqueue_ingestion(job_id, book_id, config_dict)
"""
from __future__ import annotations

from functools import lru_cache

from celery import Celery


@lru_cache(maxsize=1)
def _sender() -> Celery:
    """Singleton Celery app used only for sending tasks (no workers here)."""
    from app.core.config import settings
    return Celery(broker=settings.REDIS_CELERY_URL)


def enqueue_ingestion(job_id: str, book_id: str, config: dict) -> str:
    """Enqueue an ingestion job on the 'default' Celery queue.

    Uses a 3-second countdown so the API's DB transaction is guaranteed to
    have committed before the worker queries the job record.

    Args:
        job_id: UUID string of the IngestionJob record.
        book_id: UUID string of the Book record.
        config: Serialisable config dict snapshot (matches IngestionConfig).

    Returns:
        Celery async result ID (stored in ingestion_jobs.celery_task_id).
    """
    result = _sender().send_task(
        "app.tasks.ingest.run_ingestion",
        args=[job_id, book_id, config],
        queue="default",
        countdown=3,  # wait 3 s for API's DB commit before worker reads job
    )
    return result.id
