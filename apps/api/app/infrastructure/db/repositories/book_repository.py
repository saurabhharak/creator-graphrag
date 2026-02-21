"""Repository for Book, BookFile, and BookPermission database operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.books import Book, BookFile, BookPermission

logger = structlog.get_logger(__name__)


class BookRepository:
    """Database operations for Book records.

    Args:
        db: An open AsyncSession bound to the current request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        created_by: UUID,
        title: str,
        language_primary: str,
        author: str | None = None,
        year: int | None = None,
        edition: str | None = None,
        publisher: str | None = None,
        isbn: str | None = None,
        tags: list[str] | None = None,
        org_id: UUID | None = None,
    ) -> Book:
        """Insert a new book record.

        Args:
            created_by: UUID of the user uploading the book.
            title: Book title (required).
            language_primary: BCP-47 primary language code (mr|hi|en|mixed|unknown).
            author: Author name.
            year: Publication year.
            edition: Edition string.
            publisher: Publisher name.
            isbn: ISBN-10 or ISBN-13.
            tags: Searchable tag strings.
            org_id: Optional organization scope.

        Returns:
            The newly created Book (flushed but not yet committed).
        """
        book = Book(
            book_id=uuid.uuid4(),
            created_by=created_by,
            title=title,
            language_primary=language_primary,
            author=author,
            year=year,
            edition=edition,
            publisher=publisher,
            isbn=isbn,
            tags=tags or [],
            org_id=org_id,
        )
        self.db.add(book)
        await self.db.flush()
        logger.info("book_created", book_id=str(book.book_id), title=title)
        return book

    async def get_by_id(self, book_id: UUID) -> Book | None:
        """Fetch a non-deleted book by primary key.

        Args:
            book_id: UUID of the book.

        Returns:
            The Book ORM object, or None if not found or soft-deleted.
        """
        result = await self.db.execute(
            select(Book).where(Book.book_id == book_id, Book.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        limit: int = 20,
        cursor_created_at: datetime | None = None,
        language: str | None = None,
    ) -> list[Book]:
        """Return books created by the user, newest first, with cursor pagination.

        Args:
            user_id: UUID of the owning user.
            limit: Maximum number of records to return.
            cursor_created_at: If provided, return only books created before this timestamp
                (exclusive), implementing keyset pagination.
            language: Optional filter by language_primary value.

        Returns:
            List of Book objects ordered by created_at descending.
        """
        conditions = [Book.created_by == user_id, Book.deleted_at.is_(None)]
        if cursor_created_at is not None:
            conditions.append(Book.created_at < cursor_created_at)
        if language:
            conditions.append(Book.language_primary == language)

        result = await self.db.execute(
            select(Book)
            .where(and_(*conditions))
            .order_by(Book.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def soft_delete(self, book_id: UUID, user_id: UUID) -> bool:
        """Soft-delete a book by setting deleted_at. Verifies ownership.

        Args:
            book_id: UUID of the book to delete.
            user_id: UUID of the requesting user (ownership check).

        Returns:
            True if the book was found and deleted, False otherwise.
        """
        result = await self.db.execute(
            update(Book)
            .where(
                Book.book_id == book_id,
                Book.created_by == user_id,
                Book.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(timezone.utc))
            .returning(Book.book_id)
        )
        deleted = result.scalar_one_or_none() is not None
        if deleted:
            logger.info("book_soft_deleted", book_id=str(book_id), user_id=str(user_id))
        return deleted


class BookFileRepository:
    """Database operations for BookFile records.

    Args:
        db: An open AsyncSession bound to the current request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        book_id: UUID,
        kind: str,
        uri: str,
        source_format: str | None = None,
    ) -> BookFile:
        """Insert a new book file record.

        Args:
            book_id: Owning book UUID.
            kind: File type (raw_pdf | extracted_text | ocr_json | sarvam_md | sarvam_page_json).
            uri: S3 URI for the file.
            source_format: Optional format tag (pdf_text | pdf_scanned | epub | ocr_output).

        Returns:
            The newly created BookFile (flushed but not yet committed).
        """
        book_file = BookFile(
            file_id=uuid.uuid4(),
            book_id=book_id,
            kind=kind,
            uri=uri,
            source_format=source_format,
            upload_status="pending",
        )
        self.db.add(book_file)
        await self.db.flush()
        return book_file

    async def get_raw_file(self, book_id: UUID) -> BookFile | None:
        """Return the raw PDF file record for a book (kind='raw_pdf').

        Args:
            book_id: Owning book UUID.

        Returns:
            The BookFile, or None if not found.
        """
        result = await self.db.execute(
            select(BookFile).where(
                BookFile.book_id == book_id,
                BookFile.kind == "raw_pdf",
            )
        )
        return result.scalar_one_or_none()

    async def mark_verified(
        self,
        file_id: UUID,
        checksum: str | None = None,
        size_bytes: int | None = None,
    ) -> BookFile | None:
        """Mark a book file as verified after successful S3 upload confirmation.

        Args:
            file_id: UUID of the BookFile record.
            checksum: Optional SHA-256 hex digest provided by the client.
            size_bytes: File size in bytes reported by S3 HEAD response.

        Returns:
            The updated BookFile, or None if not found.
        """
        values: dict = {"upload_status": "verified"}
        if checksum is not None:
            values["checksum_sha256"] = checksum
        if size_bytes is not None:
            values["size_bytes"] = size_bytes

        result = await self.db.execute(
            update(BookFile)
            .where(BookFile.file_id == file_id)
            .values(**values)
            .returning(BookFile.file_id)
        )
        updated = result.scalar_one_or_none()
        if updated:
            logger.info("book_file_verified", file_id=str(file_id))
        return updated
