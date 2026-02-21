"""Evidence drilldown endpoints."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.v1.deps import CurrentUserDep, DbSession
from app.infrastructure.db.models.books import Book, Chapter, Chunk
from app.infrastructure.db.repositories.video_package_repository import (
    VideoPackageRepository,
)

router = APIRouter()


@router.get("/{chunk_id}", summary="Get evidence for a chunk")
async def get_evidence(
    chunk_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return full citation for a chunk: snippet, page range, chapter, book title.

    Used by the evidence panel in the UI when a user clicks a script paragraph.
    Returns 404 if the chunk does not exist or has been soft-deleted.
    """
    result = await db.execute(
        select(Chunk, Chapter, Book)
        .join(Book, Chunk.book_id == Book.book_id)
        .outerjoin(Chapter, Chunk.chapter_id == Chapter.chapter_id)
        .where(
            Chunk.chunk_id == chunk_id,
            Chunk.deleted_at.is_(None),
            Book.deleted_at.is_(None),
        )
    )
    row = result.first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chunk not found",
        )

    chunk: Chunk = row[0]
    chapter: Chapter | None = row[1]
    book: Book = row[2]

    return {
        "chunk_id": str(chunk.chunk_id),
        "snippet": chunk.text[:600] if chunk.text else None,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_title": chunk.section_title,
        "chapter_title": chapter.title if chapter else None,
        "book_id": str(book.book_id),
        "book_title": book.title,
        "language_detected": chunk.language_detected,
        "chunk_type": chunk.chunk_type,
    }


@router.get("/map/{video_id}", summary="Get evidence map for a video package")
async def get_evidence_map(
    video_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return the full evidence map for a video package.

    Each scene in the script maps to its evidence refs (chunk_id, book_id, snippet, pages).
    Used by the UI for click-paragraph → evidence panel feature.
    """
    repo = VideoPackageRepository(db)
    pkg = await repo.get_by_id_for_user(video_id, user.user_id)
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video package not found")
    return {
        "video_id": str(video_id),
        "paragraphs": pkg.evidence_map_jsonb.get("paragraphs", []),
    }
