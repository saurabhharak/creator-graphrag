"""Repository for IngestionJob database operations."""
from __future__ import annotations

import uuid
from uuid import UUID

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.books import IngestionJob

logger = structlog.get_logger(__name__)

# Statuses that count toward the concurrent job limit
_ACTIVE_STATUSES = ("queued", "running")


class IngestionJobRepository:
    """Database operations for IngestionJob records.

    Args:
        db: An open AsyncSession bound to the current request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        book_id: UUID,
        created_by: UUID,
        config_json: dict | None = None,
    ) -> IngestionJob:
        """Insert a new ingestion job record in 'queued' state.

        Args:
            book_id: UUID of the book being ingested.
            created_by: UUID of the user who triggered ingestion.
            config_json: Snapshot of the ingestion configuration options.

        Returns:
            The newly created IngestionJob (flushed but not yet committed).
        """
        job = IngestionJob(
            job_id=uuid.uuid4(),
            book_id=book_id,
            created_by=created_by,
            status="queued",
            stage="upload",
            progress=0.0,
            config_json=config_json,
        )
        self.db.add(job)
        await self.db.flush()
        logger.info(
            "ingestion_job_created",
            job_id=str(job.job_id),
            book_id=str(book_id),
            user_id=str(created_by),
        )
        return job

    async def get_by_id(self, job_id: UUID) -> IngestionJob | None:
        """Fetch a job by primary key.

        Args:
            job_id: UUID of the ingestion job.

        Returns:
            The IngestionJob ORM object, or None if not found.
        """
        result = await self.db.execute(
            select(IngestionJob).where(IngestionJob.job_id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_for_book(self, book_id: UUID) -> list[IngestionJob]:
        """Return all jobs for a book, newest first.

        Args:
            book_id: UUID of the book.

        Returns:
            List of IngestionJob objects ordered by created_at descending.
        """
        result = await self.db.execute(
            select(IngestionJob)
            .where(IngestionJob.book_id == book_id)
            .order_by(IngestionJob.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_for_book(self, book_id: UUID) -> IngestionJob | None:
        """Return the most recently created job for a book.

        Args:
            book_id: UUID of the book.

        Returns:
            The latest IngestionJob, or None if no jobs exist.
        """
        result = await self.db.execute(
            select(IngestionJob)
            .where(IngestionJob.book_id == book_id)
            .order_by(IngestionJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_running_for_user(self, user_id: UUID) -> int:
        """Count active (queued or running) jobs across all books for a user.

        Used to enforce MAX_CONCURRENT_JOBS_PER_USER.

        Args:
            user_id: UUID of the user.

        Returns:
            Integer count of active jobs.
        """
        result = await self.db.execute(
            select(func.count()).where(
                IngestionJob.created_by == user_id,
                IngestionJob.status.in_(_ACTIVE_STATUSES),
            )
        )
        return result.scalar_one()

    async def update_status(
        self,
        job_id: UUID,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        metrics_json: dict | None = None,
        error_json: dict | None = None,
        celery_task_id: str | None = None,
    ) -> None:
        """Partially update a job's status fields.

        Only non-None keyword arguments are applied — other fields remain unchanged.

        Args:
            job_id: UUID of the job to update.
            status: New status value (queued|running|failed|completed|canceled).
            stage: Pipeline stage name.
            progress: Fractional progress 0.0–1.0.
            message: Human-readable status message.
            metrics_json: Pipeline metrics dict (pages_total, chunks_created, etc.).
            error_json: Error detail dict (code, detail).
            celery_task_id: Celery async result ID for cancellation.
        """
        values: dict = {}
        if status is not None:
            values["status"] = status
        if stage is not None:
            values["stage"] = stage
        if progress is not None:
            values["progress"] = progress
        if message is not None:
            values["message"] = message
        if metrics_json is not None:
            values["metrics_json"] = metrics_json
        if error_json is not None:
            values["error_json"] = error_json
        if celery_task_id is not None:
            values["celery_task_id"] = celery_task_id

        if not values:
            return

        await self.db.execute(
            update(IngestionJob).where(IngestionJob.job_id == job_id).values(**values)
        )
        logger.info("ingestion_job_updated", job_id=str(job_id), **{k: v for k, v in values.items() if k != "error_json"})
