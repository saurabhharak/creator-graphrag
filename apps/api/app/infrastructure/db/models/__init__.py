"""ORM model registry — import all models so Alembic autogenerate works."""
from __future__ import annotations

from app.infrastructure.db.models.base import Base
from app.infrastructure.db.models.books import (
    Book,
    BookFile,
    BookPermission,
    Chapter,
    Chunk,
    IngestionJob,
)
from app.infrastructure.db.models.knowledge_units import (
    KnowledgeUnit,
    LlmUsageLog,
    QASample,
    UnitEdit,
)
from app.infrastructure.db.models.user import ApiKey, AuditLog, Organization, User
from app.infrastructure.db.models.video_packages import (
    SearchLog,
    Template,
    VideoPackage,
    VideoPackageVersion,
    Webhook,
)

__all__ = [
    "Base",
    "Organization",
    "User",
    "ApiKey",
    "AuditLog",
    "Book",
    "BookFile",
    "BookPermission",
    "IngestionJob",
    "Chapter",
    "Chunk",
    "KnowledgeUnit",
    "UnitEdit",
    "QASample",
    "LlmUsageLog",
    "Template",
    "VideoPackage",
    "VideoPackageVersion",
    "Webhook",
    "SearchLog",
]
