"""ORM models for video packages, templates, webhooks, and search logs (migration 0007)."""
from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import Base


class Template(Base):
    """Prompt / layout template for video package generation."""

    __tablename__ = "templates"

    template_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    audience_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_sections: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    scene_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    scene_max: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8")
    pacing_constraints: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    output_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class VideoPackage(Base):
    """A generated video package: outline + script + storyboard + visual spec."""

    __tablename__ = "video_packages"

    video_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    audience_level: Mapped[str] = mapped_column(Text, nullable=False)
    language_mode: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False)
    strict_citations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    citation_repair_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="label_interpretation"
    )
    template_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("templates.template_id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    outline_md: Mapped[str] = mapped_column(Text, nullable=False)
    script_md: Mapped[str] = mapped_column(Text, nullable=False)
    storyboard_jsonb: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    visual_spec_jsonb: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    citations_report_jsonb: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    evidence_map_jsonb: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    warnings_jsonb: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    source_filters_jsonb: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    pkg_versions: Mapped[list[VideoPackageVersion]] = relationship(
        "VideoPackageVersion",
        back_populates="package",
        cascade="all, delete-orphan",
    )


class VideoPackageVersion(Base):
    """Historical snapshot of a video package after each regeneration."""

    __tablename__ = "video_package_versions"

    version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("video_packages.video_id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    package: Mapped[VideoPackage] = relationship(
        "VideoPackage", back_populates="pkg_versions"
    )


class Webhook(Base):
    """User-registered webhook for async event notifications."""

    __tablename__ = "webhooks"

    webhook_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list] = mapped_column(JSONB, nullable=False)
    secret_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class SearchLog(Base):
    """Append-only record of every search query for analytics."""

    __tablename__ = "search_logs"

    log_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_language: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filters_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
