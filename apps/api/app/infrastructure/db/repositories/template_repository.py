"""Repository for Template CRUD operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.video_packages import Template

logger = structlog.get_logger(__name__)


class TemplateRepository:
    """Database operations for Template records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, template_id: UUID) -> Template | None:
        result = await self.db.execute(
            select(Template).where(
                Template.template_id == template_id,
                Template.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self, include_system: bool = True) -> list[Template]:
        conditions = [Template.deleted_at.is_(None)]
        if not include_system:
            conditions.append(Template.is_system.is_(False))
        result = await self.db.execute(
            select(Template).where(and_(*conditions)).order_by(Template.created_at.asc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        created_by: UUID,
        name: str,
        format: str,
        audience_level: str | None = None,
        required_sections: list | None = None,
        scene_min: int = 5,
        scene_max: int = 8,
        pacing_constraints: dict | None = None,
        output_schema: dict | None = None,
        is_system: bool = False,
    ) -> Template:
        template = Template(
            template_id=uuid.uuid4(),
            created_by=created_by,
            name=name,
            format=format,
            audience_level=audience_level,
            required_sections=required_sections or [],
            scene_min=scene_min,
            scene_max=scene_max,
            pacing_constraints=pacing_constraints or {},
            output_schema=output_schema or {},
            is_system=is_system,
        )
        self.db.add(template)
        await self.db.flush()
        logger.info("template_created", template_id=str(template.template_id), name=name)
        return template

    async def update(
        self,
        template_id: UUID,
        patch: dict,
    ) -> Template | None:
        allowed = {
            "name", "audience_level", "required_sections",
            "scene_min", "scene_max", "pacing_constraints", "output_schema",
        }
        values = {k: v for k, v in patch.items() if k in allowed}
        if not values:
            return await self.get_by_id(template_id)
        result = await self.db.execute(
            update(Template)
            .where(
                Template.template_id == template_id,
                Template.is_system.is_(False),
                Template.deleted_at.is_(None),
            )
            .values(**values)
            .returning(Template.template_id)
        )
        updated_id = result.scalar_one_or_none()
        if updated_id is None:
            return None
        await self.db.flush()
        return await self.get_by_id(template_id)

    async def soft_delete(self, template_id: UUID) -> bool:
        result = await self.db.execute(
            update(Template)
            .where(
                Template.template_id == template_id,
                Template.is_system.is_(False),
                Template.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(timezone.utc))
            .returning(Template.template_id)
        )
        deleted = result.scalar_one_or_none() is not None
        if deleted:
            logger.info("template_deleted", template_id=str(template_id))
        return deleted
