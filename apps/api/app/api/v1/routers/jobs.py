"""Ingestion job management endpoints."""
from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.v1.deps import CurrentUserDep, DbSession, EditorOrAdminDep
from app.core.errors import NotFoundError
from app.infrastructure.db.repositories.ingestion_job_repository import IngestionJobRepository

logger = structlog.get_logger(__name__)
router = APIRouter()


class JobStatusResponse(BaseModel):
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: float
    message: str | None
    error: dict | None
    metrics: dict | None
    created_at: str
    updated_at: str


@router.get("/{job_id}", response_model=JobStatusResponse, summary="Get job status")
async def get_job(job_id: UUID, user: CurrentUserDep, db: DbSession):
    """Get ingestion job status with stage, progress (0–1), and metrics.

    Progress uses a stage-weighted formula:
    upload=2%, ocr=35%, structure_extract=10%, chunk=10%,
    embed=20%, unit_extract=15%, graph_build=8%
    """
    job_repo = IngestionJobRepository(db)
    job = await job_repo.get_by_id(job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} not found")

    # Ownership check: user must have created the job (or be admin)
    if job.created_by != user.user_id and user.role != "admin":
        raise NotFoundError(f"Job {job_id} not found")

    return JobStatusResponse(
        job_id=str(job.job_id),
        book_id=str(job.book_id),
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        message=job.message,
        error=job.error_json,
        metrics=job.metrics_json,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


@router.get("/{job_id}/events", summary="Stream job progress (SSE)")
async def job_events(job_id: UUID, user: CurrentUserDep):
    """Server-Sent Events stream for real-time job progress.

    Workers publish to Redis pub/sub channel ``job:<job_id>:events``.
    Reconnects automatically on client disconnect.
    """
    async def event_generator():
        # TODO(#1): subscribe to Redis pub/sub channel job:{job_id}:events
        # TODO(#1): stream events until job status is done/failed/canceled
        yield f"data: {{\"stage\": \"upload\", \"progress\": 0}}\n\n"
        await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{job_id}/cancel", summary="Cancel a running job")
async def cancel_job(job_id: UUID, user: EditorOrAdminDep, db: DbSession):
    """Cancel a running ingestion job."""
    job_repo = IngestionJobRepository(db)
    job = await job_repo.get_by_id(job_id)
    if job is None or (job.created_by != user.user_id and user.role != "admin"):
        raise NotFoundError(f"Job {job_id} not found")

    # Revoke Celery task if it has one
    if job.celery_task_id:
        try:
            from app.infrastructure.celery_client import _sender
            _sender().control.revoke(job.celery_task_id, terminate=True)
        except Exception as exc:
            logger.warning("celery_revoke_failed", job_id=str(job_id), error=str(exc))

    await job_repo.update_status(job_id, status="canceled", message="Canceled by user")
    return {"job_id": str(job_id), "status": "canceled"}


@router.post("/{job_id}/retry", summary="Retry a failed job")
async def retry_job(job_id: UUID, user: EditorOrAdminDep, db: DbSession):
    """Retry a failed ingestion job from the failed stage.

    Each pipeline stage is idempotent so partial work is safely re-runnable.
    """
    job_repo = IngestionJobRepository(db)
    job = await job_repo.get_by_id(job_id)
    if job is None or (job.created_by != user.user_id and user.role != "admin"):
        raise NotFoundError(f"Job {job_id} not found")

    if job.status != "failed":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Job is in '{job.status}' state — only failed jobs can be retried.",
        )

    # Create a fresh job record (idempotent re-run from beginning)
    new_job = await job_repo.create(
        book_id=job.book_id,
        created_by=job.created_by,
        config_json=job.config_json,
    )

    # Enqueue Celery task for the new job
    try:
        from app.infrastructure.celery_client import enqueue_ingestion
        celery_task_id = enqueue_ingestion(
            job_id=str(new_job.job_id),
            book_id=str(job.book_id),
            config=job.config_json or {},
        )
        await job_repo.update_status(new_job.job_id, celery_task_id=celery_task_id)
    except Exception as exc:
        logger.error("celery_retry_enqueue_failed", job_id=str(new_job.job_id), error=str(exc))

    logger.info(
        "ingestion_job_retried",
        original_job_id=str(job_id),
        new_job_id=str(new_job.job_id),
    )
    return {"job_id": str(job_id), "new_job_id": str(new_job.job_id), "status": "queued"}
