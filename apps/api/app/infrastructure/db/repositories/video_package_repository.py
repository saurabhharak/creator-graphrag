"""Repository for VideoPackage and VideoPackageVersion database operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.video_packages import VideoPackage, VideoPackageVersion

logger = structlog.get_logger(__name__)


class VideoPackageRepository:
    """Database operations for VideoPackage records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        created_by: UUID,
        topic: str,
        format: str,
        audience_level: str,
        language_mode: str,
        tone: str,
        outline_md: str,
        script_md: str,
        storyboard: dict,
        visual_spec: dict,
        citations_report: dict,
        evidence_map: dict,
        warnings: list,
        source_filters: dict,
        strict_citations: bool = True,
        citation_repair_mode: str = "label_interpretation",
        template_id: UUID | None = None,
        version: int = 1,
    ) -> VideoPackage:
        pkg = VideoPackage(
            video_id=uuid.uuid4(),
            created_by=created_by,
            topic=topic,
            format=format,
            audience_level=audience_level,
            language_mode=language_mode,
            tone=tone,
            strict_citations=strict_citations,
            citation_repair_mode=citation_repair_mode,
            template_id=template_id,
            version=version,
            outline_md=outline_md,
            script_md=script_md,
            storyboard_jsonb=storyboard,
            visual_spec_jsonb=visual_spec,
            citations_report_jsonb=citations_report,
            evidence_map_jsonb=evidence_map,
            warnings_jsonb=warnings,
            source_filters_jsonb=source_filters,
        )
        self.db.add(pkg)
        await self.db.flush()
        logger.info(
            "video_package_created",
            video_id=str(pkg.video_id),
            topic=topic[:60],
            format=format,
        )
        return pkg

    async def create_version_snapshot(
        self,
        video_id: UUID,
        version_number: int,
        snapshot: dict,
    ) -> VideoPackageVersion:
        ver = VideoPackageVersion(
            version_id=uuid.uuid4(),
            video_id=video_id,
            version_number=version_number,
            snapshot_jsonb=snapshot,
        )
        self.db.add(ver)
        await self.db.flush()
        return ver

    async def get_by_id(self, video_id: UUID) -> VideoPackage | None:
        result = await self.db.execute(
            select(VideoPackage).where(
                VideoPackage.video_id == video_id,
                VideoPackage.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_user(self, video_id: UUID, user_id: UUID) -> VideoPackage | None:
        result = await self.db.execute(
            select(VideoPackage).where(
                VideoPackage.video_id == video_id,
                VideoPackage.created_by == user_id,
                VideoPackage.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        limit: int = 20,
        cursor_created_at: datetime | None = None,
        topic_filter: str | None = None,
        format_filter: str | None = None,
    ) -> list[VideoPackage]:
        conditions = [
            VideoPackage.created_by == user_id,
            VideoPackage.deleted_at.is_(None),
        ]
        if cursor_created_at is not None:
            conditions.append(VideoPackage.created_at < cursor_created_at)
        if topic_filter:
            conditions.append(VideoPackage.topic.ilike(f"%{topic_filter}%"))
        if format_filter:
            conditions.append(VideoPackage.format == format_filter)

        result = await self.db.execute(
            select(VideoPackage)
            .where(and_(*conditions))
            .order_by(VideoPackage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_user(self, user_id: UUID) -> int:
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count()).where(
                VideoPackage.created_by == user_id,
                VideoPackage.deleted_at.is_(None),
            )
        )
        return result.scalar_one()

    async def list_versions(self, video_id: UUID) -> list[VideoPackageVersion]:
        result = await self.db.execute(
            select(VideoPackageVersion)
            .where(VideoPackageVersion.video_id == video_id)
            .order_by(VideoPackageVersion.version_number.asc())
        )
        return list(result.scalars().all())

    async def get_version(
        self, video_id: UUID, version_number: int
    ) -> VideoPackageVersion | None:
        result = await self.db.execute(
            select(VideoPackageVersion).where(
                VideoPackageVersion.video_id == video_id,
                VideoPackageVersion.version_number == version_number,
            )
        )
        return result.scalar_one_or_none()

    async def soft_delete(self, video_id: UUID, user_id: UUID) -> bool:
        result = await self.db.execute(
            update(VideoPackage)
            .where(
                VideoPackage.video_id == video_id,
                VideoPackage.created_by == user_id,
                VideoPackage.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(timezone.utc))
            .returning(VideoPackage.video_id)
        )
        deleted = result.scalar_one_or_none() is not None
        if deleted:
            logger.info("video_package_deleted", video_id=str(video_id))
        return deleted
