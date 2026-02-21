"""Repository for KnowledgeUnit and UnitEdit database operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.knowledge_units import KnowledgeUnit, UnitEdit

logger = structlog.get_logger(__name__)

_EDITABLE_FIELDS = {"status", "subject", "predicate", "object", "confidence", "payload_jsonb"}


class KnowledgeUnitRepository:
    """Database operations for KnowledgeUnit records.

    Args:
        db: An open AsyncSession bound to the current request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_bulk(self, units: list[dict]) -> list[KnowledgeUnit]:
        """Insert multiple knowledge units from extraction output.

        Args:
            units: List of dicts matching KnowledgeUnit column names.

        Returns:
            Newly inserted KnowledgeUnit objects (flushed, not committed).
        """
        objs = [KnowledgeUnit(**u) for u in units]
        for obj in objs:
            self.db.add(obj)
        await self.db.flush()
        logger.info("knowledge_units_created", count=len(objs))
        return objs

    async def get_by_id(self, unit_id: UUID) -> KnowledgeUnit | None:
        """Fetch a single unit by PK. Returns None if soft-deleted or not found."""
        result = await self.db.execute(
            select(KnowledgeUnit).where(
                KnowledgeUnit.unit_id == unit_id,
                KnowledgeUnit.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_book(
        self,
        book_id: UUID | None = None,
        status: str | None = None,
        ku_type: str | None = None,
        language: str | None = None,
        limit: int = 50,
        cursor: datetime | None = None,
    ) -> list[KnowledgeUnit]:
        """List knowledge units with optional filters. Keyset cursor on created_at DESC."""
        conditions = [KnowledgeUnit.deleted_at.is_(None)]

        if book_id is not None:
            conditions.append(KnowledgeUnit.source_book_id == book_id)
        if status is not None:
            conditions.append(KnowledgeUnit.status == status)
        if ku_type is not None:
            conditions.append(KnowledgeUnit.type == ku_type)
        if language is not None:
            conditions.append(KnowledgeUnit.language_detected == language)
        if cursor is not None:
            conditions.append(KnowledgeUnit.created_at < cursor)

        result = await self.db.execute(
            select(KnowledgeUnit)
            .where(and_(*conditions))
            .order_by(KnowledgeUnit.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update(
        self,
        unit_id: UUID,
        patch: dict,
        editor_user_id: UUID,
        note: str | None = None,
    ) -> KnowledgeUnit | None:
        """Update allowed fields and create an audit UnitEdit record.

        Args:
            unit_id: Unit to update.
            patch: Dict of fields to set (restricted to _EDITABLE_FIELDS).
            editor_user_id: User making the change.
            note: Optional editor note stored in the audit record.

        Returns:
            Updated KnowledgeUnit, or None if not found / soft-deleted.
        """
        unit = await self.get_by_id(unit_id)
        if unit is None:
            return None

        before = {
            k: getattr(unit, k)
            for k in _EDITABLE_FIELDS
            if hasattr(unit, k)
        }

        safe_patch = {k: v for k, v in patch.items() if k in _EDITABLE_FIELDS and v is not None}
        if not safe_patch:
            return unit

        for field, value in safe_patch.items():
            setattr(unit, field, value)
        unit.updated_at = datetime.now(timezone.utc)

        after = {k: getattr(unit, k) for k in safe_patch}
        edit = UnitEdit(
            edit_id=uuid.uuid4(),
            unit_id=unit_id,
            editor_user_id=editor_user_id,
            patch_jsonb={"before": before, "after": after},
            note=note,
        )
        self.db.add(edit)
        await self.db.flush()
        logger.info("knowledge_unit_updated", unit_id=str(unit_id), fields=list(safe_patch))
        return unit

    async def bulk_update_status(
        self,
        unit_ids: list[UUID],
        new_status: str,
        editor_user_id: UUID,
        note: str | None = None,
    ) -> tuple[int, list[str]]:
        """Batch approve or reject up to 200 units.

        Returns:
            (succeeded_count, list_of_failed_unit_id_strings)
        """
        succeeded = 0
        failed: list[str] = []

        for uid in unit_ids:
            result = await self.update(
                uid,
                patch={"status": new_status},
                editor_user_id=editor_user_id,
                note=note,
            )
            if result is not None:
                succeeded += 1
            else:
                failed.append(str(uid))

        return succeeded, failed

    async def list_edits(self, unit_id: UUID) -> list[UnitEdit]:
        """Return all audit edits for a unit, newest first."""
        result = await self.db.execute(
            select(UnitEdit)
            .where(UnitEdit.unit_id == unit_id)
            .order_by(UnitEdit.created_at.desc())
        )
        return list(result.scalars().all())
