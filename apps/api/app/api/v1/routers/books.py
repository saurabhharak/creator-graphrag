"""Book management endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.deps import CurrentUserDep, DbSession, EditorOrAdminDep
from app.core.config import settings
from app.core.errors import JobConcurrencyError, NotFoundError, UploadNotVerifiedError
from app.infrastructure.db.repositories.book_repository import (
    BookFileRepository,
    BookRepository,
)
from app.infrastructure.db.repositories.ingestion_job_repository import IngestionJobRepository
from app.infrastructure.celery_client import enqueue_ingestion
from app.infrastructure.storage.s3_client import (
    generate_presigned_put_url,
    get_object_size,
    object_exists,
)

logger = structlog.get_logger(__name__)
router = APIRouter()

# S3 key template for raw book uploads
_RAW_KEY = "books/{book_id}/raw.pdf"


# ─── Schemas ────────────────────────────────────────────────────────────────

class CreateBookRequest(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    author: str | None = Field(None, max_length=300)
    year: int | None = Field(None, ge=1400, le=2100)
    edition: str | None = Field(None, max_length=100)
    language_primary: str = Field(pattern="^(mr|hi|en|mixed|unknown)$")
    publisher: str | None = Field(None, max_length=300)
    isbn: str | None = Field(None, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=50)


class UploadInfo(BaseModel):
    upload_method: str
    url: str
    headers: dict[str, str] = {}
    expires_at: str


class CreateBookResponse(BaseModel):
    book_id: UUID
    upload: UploadInfo


class BookSummary(BaseModel):
    book_id: UUID
    title: str
    author: str | None
    language_primary: str
    tags: list[str]
    ingestion_status: str | None
    chunk_count: int
    unit_approval_rate: float | None
    created_at: str
    updated_at: str


class BookListResponse(BaseModel):
    items: list[BookSummary]
    next_cursor: str | None
    total_count: int | None


class BookDetailResponse(BaseModel):
    book_id: UUID
    title: str
    author: str | None
    year: int | None
    edition: str | None
    language_primary: str
    publisher: str | None
    isbn: str | None
    tags: list[str]
    visibility: str
    usage_rights: str
    ingestion_status: str | None
    ingestion_stage: str | None
    ingestion_progress: float | None
    created_at: str
    updated_at: str


class UploadCompleteRequest(BaseModel):
    checksum_sha256: str | None = None


class StartIngestionRequest(BaseModel):
    force_ocr: bool = False
    ocr_languages: list[str] = ["hin", "mar", "eng"]
    chunking: dict = {"max_chars": 2000, "overlap_chars": 250}
    extract_knowledge_units: bool = True
    build_graph: bool = True


class StartIngestionResponse(BaseModel):
    job_id: UUID
    status: str


class PermissionRequest(BaseModel):
    user_id: UUID
    permission_level: str = Field(pattern="^(read|edit)$")


# ─── Endpoints ──────────────────────────────────────────────────────────────

@router.get("", response_model=BookListResponse, summary="List books")
async def list_books(
    user: CurrentUserDep,
    db: DbSession,
    language: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    """List books created by the authenticated user, newest first.

    Uses keyset (cursor) pagination. Pass the ``next_cursor`` from the
    previous response as the ``cursor`` query parameter to fetch the next page.
    """
    cursor_dt: datetime | None = None
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format. Expected ISO-8601 datetime.",
            )

    book_repo = BookRepository(db)
    books = await book_repo.list_for_user(
        user_id=user.user_id,
        limit=limit,
        cursor_created_at=cursor_dt,
        language=language,
    )

    job_repo = IngestionJobRepository(db)
    items: list[BookSummary] = []
    for book in books:
        latest_job = await job_repo.get_latest_for_book(book.book_id)
        items.append(
            BookSummary(
                book_id=book.book_id,
                title=book.title,
                author=book.author,
                language_primary=book.language_primary,
                tags=book.tags or [],
                ingestion_status=latest_job.status if latest_job else None,
                chunk_count=(latest_job.metrics_json or {}).get("chunks_created", 0) if latest_job else 0,
                unit_approval_rate=None,  # populated post-extraction
                created_at=book.created_at.isoformat(),
                updated_at=book.updated_at.isoformat(),
            )
        )

    next_cursor: str | None = None
    if len(books) == limit:
        next_cursor = books[-1].created_at.isoformat()

    return BookListResponse(items=items, next_cursor=next_cursor, total_count=None)


@router.post(
    "",
    response_model=CreateBookResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create book metadata and get upload URL",
)
async def create_book(body: CreateBookRequest, user: EditorOrAdminDep, db: DbSession):
    """Create a book record and return a presigned S3 PUT URL for file upload.

    Workflow:
    1. Client POSTs book metadata here → receives ``book_id`` + presigned URL.
    2. Client PUTs the PDF directly to the presigned URL (no API proxy).
    3. Client calls ``POST /books/{book_id}/upload-complete`` to confirm.
    4. Client calls ``POST /books/{book_id}/ingest`` to start processing.
    """
    book_repo = BookRepository(db)
    book = await book_repo.create(
        created_by=user.user_id,
        title=body.title,
        language_primary=body.language_primary,
        author=body.author,
        year=body.year,
        edition=body.edition,
        publisher=body.publisher,
        isbn=body.isbn,
        tags=body.tags,
        org_id=user.org_id,
    )

    # Register the pending file record
    s3_key = _RAW_KEY.format(book_id=book.book_id)
    file_repo = BookFileRepository(db)
    await file_repo.create(
        book_id=book.book_id,
        kind="raw_pdf",
        uri=f"s3://{settings.S3_BUCKET_BOOKS}/{s3_key}",
    )

    # Generate presigned URL (CPU-only signing — no network call)
    ttl_seconds = settings.PRESIGNED_UPLOAD_URL_TTL_MINUTES * 60
    presigned_url = generate_presigned_put_url(
        key=s3_key,
        bucket=settings.S3_BUCKET_BOOKS,
        ttl_seconds=ttl_seconds,
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()

    logger.info("book_upload_url_issued", book_id=str(book.book_id), user_id=str(user.user_id))
    return CreateBookResponse(
        book_id=book.book_id,
        upload=UploadInfo(
            upload_method="presigned_put",
            url=presigned_url,
            expires_at=expires_at,
        ),
    )


@router.post(
    "/{book_id}/upload-complete",
    status_code=status.HTTP_200_OK,
    summary="Confirm file upload completed",
)
async def upload_complete(
    book_id: UUID,
    body: UploadCompleteRequest,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Confirm that the direct S3 upload has completed.

    Issues a HEAD request to S3 to verify the object exists, then marks the
    BookFile as ``verified`` so that ingestion can begin.
    """
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or book.created_by != user.user_id:
        raise NotFoundError(f"Book {book_id} not found")

    s3_key = _RAW_KEY.format(book_id=book_id)
    if not await object_exists(key=s3_key, bucket=settings.S3_BUCKET_BOOKS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File not found in storage. Please upload the file before confirming.",
        )

    size = await get_object_size(key=s3_key, bucket=settings.S3_BUCKET_BOOKS)

    file_repo = BookFileRepository(db)
    raw_file = await file_repo.get_raw_file(book_id)
    if raw_file:
        await file_repo.mark_verified(
            file_id=raw_file.file_id,
            checksum=body.checksum_sha256,
            size_bytes=size,
        )

    logger.info("book_upload_verified", book_id=str(book_id), size_bytes=size)
    return {"status": "verified", "book_id": str(book_id)}


@router.get("/{book_id}", response_model=BookDetailResponse, summary="Get book detail")
async def get_book(book_id: UUID, user: CurrentUserDep, db: DbSession):
    """Return full book detail including latest ingestion job status."""
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or book.created_by != user.user_id:
        raise NotFoundError(f"Book {book_id} not found")

    job_repo = IngestionJobRepository(db)
    latest_job = await job_repo.get_latest_for_book(book_id)

    return BookDetailResponse(
        book_id=book.book_id,
        title=book.title,
        author=book.author,
        year=book.year,
        edition=book.edition,
        language_primary=book.language_primary,
        publisher=book.publisher,
        isbn=book.isbn,
        tags=book.tags or [],
        visibility=book.visibility,
        usage_rights=book.usage_rights,
        ingestion_status=latest_job.status if latest_job else None,
        ingestion_stage=latest_job.stage if latest_job else None,
        ingestion_progress=latest_job.progress if latest_job else None,
        created_at=book.created_at.isoformat(),
        updated_at=book.updated_at.isoformat(),
    )


class UpdateBookRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=400)
    author: str | None = Field(None, max_length=300)
    year: int | None = Field(None, ge=1400, le=2100)
    edition: str | None = Field(None, max_length=100)
    language_primary: str | None = Field(None, pattern="^(mr|hi|en|mixed|unknown)$")
    publisher: str | None = Field(None, max_length=300)
    isbn: str | None = Field(None, max_length=40)
    tags: list[str] | None = None


@router.patch("/{book_id}", summary="Update book metadata")
async def update_book(
    book_id: UUID,
    body: UpdateBookRequest,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Update editable book metadata. Requires owner or admin role."""
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or (book.created_by != user.user_id and user.role != "admin"):
        raise NotFoundError(f"Book {book_id} not found")

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    for field, value in patch.items():
        setattr(book, field, value)
    book.updated_at = datetime.now(timezone.utc)

    await db.flush()
    logger.info("book_updated", book_id=str(book_id), fields=list(patch.keys()))
    return {"book_id": str(book_id), "updated_fields": list(patch.keys())}


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete book")
async def delete_book(book_id: UUID, user: EditorOrAdminDep, db: DbSession):
    """Soft-delete the book. Schedules async cleanup of S3 files and vectors."""
    book_repo = BookRepository(db)
    deleted = await book_repo.soft_delete(book_id=book_id, user_id=user.user_id)
    if not deleted:
        raise NotFoundError(f"Book {book_id} not found or already deleted")


@router.get("/{book_id}/chapters", summary="List book chapters")
async def list_chapters(book_id: UUID, user: CurrentUserDep, db: DbSession):
    """Return chapter tree with page ranges and structure confidence.

    Chapters are populated during the ``structure_extract`` ingestion stage.
    Returns an empty list if ingestion has not completed.
    """
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or book.created_by != user.user_id:
        raise NotFoundError(f"Book {book_id} not found")

    from sqlalchemy import select as sa_select
    from app.infrastructure.db.models.books import Chapter

    result = await db.execute(
        sa_select(Chapter)
        .where(Chapter.book_id == book_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.sort_order)
    )
    chapters = result.scalars().all()
    return {
        "book_id": str(book_id),
        "chapters": [
            {
                "chapter_id": str(ch.chapter_id),
                "title": ch.title,
                "page_start": ch.page_start,
                "page_end": ch.page_end,
                "sort_order": ch.sort_order,
            }
            for ch in chapters
        ],
    }


@router.get("/{book_id}/chunks", summary="List book chunks")
async def list_chunks(
    book_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
    chunk_type: str | None = None,
    language: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Return paginated chunks with optional type and language filters.

    Chunks are populated during the ``chunk`` ingestion stage.
    Returns an empty list if ingestion has not completed.
    """
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or book.created_by != user.user_id:
        raise NotFoundError(f"Book {book_id} not found")

    from sqlalchemy import select as sa_select
    from app.infrastructure.db.models.books import Chunk

    conditions = [Chunk.book_id == book_id, Chunk.deleted_at.is_(None)]
    if chunk_type:
        conditions.append(Chunk.chunk_type == chunk_type)
    if language:
        conditions.append(Chunk.language_detected == language)
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
            conditions.append(Chunk.created_at < cursor_dt)
        except ValueError:
            pass

    result = await db.execute(
        sa_select(Chunk)
        .where(*conditions)
        .order_by(Chunk.created_at.desc())
        .limit(limit + 1)
    )
    chunks = list(result.scalars().all())
    has_more = len(chunks) > limit
    items = chunks[:limit]
    next_c = items[-1].created_at.isoformat() if has_more and items else None

    return {
        "book_id": str(book_id),
        "chunks": [
            {
                "chunk_id": str(c.chunk_id),
                "chunk_type": c.chunk_type,
                "language_detected": c.language_detected,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "section_title": c.section_title,
                "text_preview": (c.text[:300] if c.text else None),
            }
            for c in items
        ],
        "next_cursor": next_c,
    }


@router.get("/{book_id}/jobs", summary="List ingestion jobs for book")
async def list_book_jobs(book_id: UUID, user: CurrentUserDep, db: DbSession):
    """Return ingestion job history for this book, newest first."""
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or book.created_by != user.user_id:
        raise NotFoundError(f"Book {book_id} not found")

    job_repo = IngestionJobRepository(db)
    jobs = await job_repo.list_for_book(book_id)
    return {
        "book_id": str(book_id),
        "jobs": [
            {
                "job_id": str(j.job_id),
                "status": j.status,
                "stage": j.stage,
                "progress": j.progress,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
            }
            for j in jobs
        ],
    }


@router.post("/{book_id}/ingest", response_model=StartIngestionResponse, summary="Start ingestion")
async def start_ingestion(
    book_id: UUID,
    body: StartIngestionRequest,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Start the ingestion pipeline for an uploaded book.

    Pre-conditions:
    - The book exists and belongs to the authenticated user.
    - The raw PDF has been confirmed (upload_status = ``verified``).
    - The user has fewer than ``MAX_CONCURRENT_JOBS_PER_USER`` active jobs.

    Returns:
        ``job_id`` and ``status=queued``. The Celery worker picks up the job
        asynchronously and updates the status as it progresses through stages.
    """
    book_repo = BookRepository(db)
    book = await book_repo.get_by_id(book_id)
    if book is None or book.created_by != user.user_id:
        raise NotFoundError(f"Book {book_id} not found")

    # Verify upload is confirmed before allowing ingestion
    file_repo = BookFileRepository(db)
    raw_file = await file_repo.get_raw_file(book_id)
    if raw_file is None or raw_file.upload_status != "verified":
        raise UploadNotVerifiedError(
            "Upload has not been confirmed yet. Call upload-complete first."
        )

    # Enforce concurrent job limit
    job_repo = IngestionJobRepository(db)
    running = await job_repo.count_running_for_user(user.user_id)
    if running >= settings.MAX_CONCURRENT_JOBS_PER_USER:
        raise JobConcurrencyError(
            f"You already have {running} active ingestion job(s). "
            f"Maximum allowed: {settings.MAX_CONCURRENT_JOBS_PER_USER}."
        )

    # Create the job record
    config_snapshot = {
        "force_ocr": body.force_ocr,
        "ocr_languages": body.ocr_languages,
        "chunking": body.chunking,
        "extract_knowledge_units": body.extract_knowledge_units,
        "build_graph": body.build_graph,
    }
    job = await job_repo.create(
        book_id=book_id,
        created_by=user.user_id,
        config_json=config_snapshot,
    )

    # Enqueue Celery task (3-second countdown lets DB commit before worker reads job)
    try:
        celery_task_id = enqueue_ingestion(
            job_id=str(job.job_id),
            book_id=str(book_id),
            config=config_snapshot,
        )
        await job_repo.update_status(job.job_id, celery_task_id=celery_task_id)
    except Exception as exc:
        # Redis unavailable: job stays queued in DB; operator can retry manually
        logger.error("celery_enqueue_failed", job_id=str(job.job_id), error=str(exc))

    logger.info(
        "ingestion_job_queued",
        job_id=str(job.job_id),
        book_id=str(book_id),
        user_id=str(user.user_id),
    )
    return StartIngestionResponse(job_id=job.job_id, status=job.status)


@router.get("/{book_id}/permissions", summary="List book permissions")
async def list_permissions(book_id: UUID, user: EditorOrAdminDep):
    """List all users with access to this book."""
    # TODO(#1): fetch from book_permissions table
    return {"book_id": str(book_id), "permissions": []}


@router.post("/{book_id}/permissions", status_code=status.HTTP_201_CREATED, summary="Grant access")
async def grant_permission(book_id: UUID, body: PermissionRequest, user: EditorOrAdminDep):
    """Grant a user read or edit access to this book."""
    # TODO(#1): insert into book_permissions, log to audit_log
    return {"granted": True}


@router.delete(
    "/{book_id}/permissions/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke access",
)
async def revoke_permission(book_id: UUID, target_user_id: UUID, user: EditorOrAdminDep):
    """Revoke a user's access to this book."""
    # TODO(#1): delete from book_permissions, log to audit_log
    pass
