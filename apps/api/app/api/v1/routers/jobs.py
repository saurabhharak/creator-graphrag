"""Ingestion job management endpoints."""
from __future__ import annotations

import asyncio
import json
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
async def job_events(job_id: UUID, user: CurrentUserDep, db: DbSession):
    """Server-Sent Events stream for real-time job progress.

    Yields the current job state immediately, then subscribes to the Redis
    pub/sub channel ``job:<job_id>:events`` and streams updates until the
    job reaches a terminal state (completed / failed / canceled).

    Falls back to polling the DB every 3 s if no Redis message arrives,
    so the client always gets an update even if Redis is temporarily down.
    """
    job_repo = IngestionJobRepository(db)
    job = await job_repo.get_by_id(job_id)
    if job is None or (job.created_by != user.user_id and user.role != "admin"):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Capture current state before the request-scoped DB session closes
    initial = json.dumps({
        "job_id": str(job_id),
        "stage": job.stage,
        "status": job.status,
        "progress": job.progress,
    })
    is_terminal = job.status in {"completed", "failed", "canceled"}

    async def event_generator():
        yield f"data: {initial}\n\n"

        if is_terminal:
            return

        import redis.asyncio as aioredis
        from app.core.config import settings
        from app.infrastructure.db.session import engine
        from sqlalchemy.ext.asyncio import AsyncSession

        terminal_statuses = {"completed", "failed", "canceled"}
        channel = f"job:{str(job_id)}:events"
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = client.pubsub()

        try:
            await pubsub.subscribe(channel)
            last_progress = -1.0

            # Hard timeout: 90 minutes for very large books
            deadline = asyncio.get_event_loop().time() + 5400

            while asyncio.get_event_loop().time() < deadline:
                # Wait up to 3 s for a Redis message before DB-poll fallback
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=3),
                        timeout=4.0,
                    )
                except asyncio.TimeoutError:
                    msg = None

                if msg and msg.get("type") == "message":
                    yield f"data: {msg['data']}\n\n"
                    try:
                        data = json.loads(msg["data"])
                        if data.get("status") in terminal_statuses:
                            return
                        last_progress = data.get("progress", last_progress)
                    except (json.JSONDecodeError, TypeError):
                        pass
                else:
                    # Fallback: poll DB so client never stalls
                    async with AsyncSession(engine) as fresh_db:
                        fresh_repo = IngestionJobRepository(fresh_db)
                        j = await fresh_repo.get_by_id(job_id)
                    if j is None:
                        return
                    if j.progress != last_progress:
                        evt = json.dumps({
                            "job_id": str(job_id),
                            "stage": j.stage,
                            "status": j.status,
                            "progress": j.progress,
                        })
                        yield f"data: {evt}\n\n"
                        last_progress = j.progress
                    if j.status in terminal_statuses:
                        return
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await client.aclose()
            except Exception:
                pass

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
