"""Video package generation and management endpoints (EPIC 7)."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.v1.deps import CurrentUserDep, DbSession, EditorOrAdminDep
from app.domain.usecases.generate_video_package import GenerateVideoPackageUsecase
from app.infrastructure.db.repositories.video_package_repository import (
    VideoPackageRepository,
)

logger = structlog.get_logger(__name__)
router = APIRouter()


# ─── Request / Response schemas ──────────────────────────────────────────────

class SceneConstraints(BaseModel):
    min_scenes: int = Field(default=5, ge=3, le=30)
    max_scenes: int = Field(default=8, ge=3, le=40)


class SourceFilters(BaseModel):
    book_ids: list[str] = []
    prefer_languages: list[str] = []


class GenerateVideoPackageRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    format: str = Field(pattern="^(shorts|explainer|deep_dive)$")
    audience_level: str = Field(pattern="^(beginner|intermediate)$")
    language_mode: str = Field(
        pattern="^(mr|hi|en|hinglish|mr_plus_en_terms|hi_plus_en_terms)$"
    )
    tone: str = Field(pattern="^(teacher|storyteller|myth_buster|step_by_step)$")
    strict_citations: bool = True
    scene_constraints: SceneConstraints = SceneConstraints()
    source_filters: SourceFilters = SourceFilters()
    template_id: str | None = Field(None, max_length=80)
    citation_repair_mode: str | None = Field(
        None,
        pattern="^(remove_paragraph|label_interpretation|fail_generation)$",
    )


def _pkg_summary(pkg) -> dict:
    return {
        "video_id": str(pkg.video_id),
        "topic": pkg.topic,
        "format": pkg.format,
        "audience_level": pkg.audience_level,
        "language_mode": pkg.language_mode,
        "tone": pkg.tone,
        "version": pkg.version,
        "citation_coverage": pkg.citations_report_jsonb.get("citation_coverage"),
        "scene_count": len(pkg.storyboard_jsonb.get("scenes", [])),
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
    }


def _pkg_full(pkg) -> dict:
    return {
        "video_id": str(pkg.video_id),
        "topic": pkg.topic,
        "format": pkg.format,
        "audience_level": pkg.audience_level,
        "language_mode": pkg.language_mode,
        "tone": pkg.tone,
        "strict_citations": pkg.strict_citations,
        "citation_repair_mode": pkg.citation_repair_mode,
        "version": pkg.version,
        "outline_md": pkg.outline_md,
        "script_md": pkg.script_md,
        "storyboard": pkg.storyboard_jsonb,
        "visual_spec": pkg.visual_spec_jsonb,
        "citations_report": pkg.citations_report_jsonb,
        "evidence_map": pkg.evidence_map_jsonb,
        "warnings": pkg.warnings_jsonb,
        "source_filters": pkg.source_filters_jsonb,
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    ":generate",
    status_code=status.HTTP_201_CREATED,
    summary="Generate video package",
)
async def generate_video_package(
    body: GenerateVideoPackageRequest,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Generate a complete video package (outline + script + storyboard + visual spec).

    Retrieves evidence from the vector store, calls LLM, enforces citations,
    and saves the result to the database.  Returns the full package on 201.
    """
    logger.info(
        "generate_video_package",
        user_id=str(user.user_id),
        topic=body.topic[:60],
        format=body.format,
        language_mode=body.language_mode,
    )
    usecase = GenerateVideoPackageUsecase(db)
    return await usecase.execute(
        user_id=user.user_id,
        topic=body.topic,
        format=body.format,
        audience_level=body.audience_level,
        language_mode=body.language_mode,
        tone=body.tone,
        strict_citations=body.strict_citations,
        citation_repair_mode=body.citation_repair_mode,
        min_scenes=body.scene_constraints.min_scenes,
        max_scenes=body.scene_constraints.max_scenes,
        book_ids=body.source_filters.book_ids,
        prefer_languages=body.source_filters.prefer_languages,
        template_id=body.template_id,
    )


@router.get("", summary="List video packages")
async def list_video_packages(
    user: CurrentUserDep,
    db: DbSession,
    topic: str | None = None,
    format: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    """List video packages for the current user with keyset pagination."""
    repo = VideoPackageRepository(db)
    cursor_dt: datetime | None = None
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="cursor must be an ISO 8601 datetime string",
            )

    pkgs = await repo.list_for_user(
        user_id=user.user_id,
        limit=limit + 1,
        cursor_created_at=cursor_dt,
        topic_filter=topic,
        format_filter=format,
    )
    next_cursor = None
    if len(pkgs) > limit:
        pkgs = pkgs[:limit]
        next_cursor = pkgs[-1].created_at.isoformat()

    total = await repo.count_for_user(user.user_id)
    return {
        "items": [_pkg_summary(p) for p in pkgs],
        "next_cursor": next_cursor,
        "total_count": total,
    }


@router.get("/{video_id}", summary="Get video package")
async def get_video_package(
    video_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return the latest version of a video package with full evidence map."""
    repo = VideoPackageRepository(db)
    pkg = await repo.get_by_id_for_user(video_id, user.user_id)
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video package not found")
    return _pkg_full(pkg)


@router.get("/{video_id}/versions", summary="List versions of a video package")
async def list_video_package_versions(
    video_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return all version metadata for a video package."""
    repo = VideoPackageRepository(db)
    pkg = await repo.get_by_id_for_user(video_id, user.user_id)
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video package not found")
    versions = await repo.list_versions(video_id)
    return {
        "video_id": str(video_id),
        "versions": [
            {
                "version_id": str(v.version_id),
                "version_number": v.version_number,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


@router.get("/{video_id}/versions/{version_number}", summary="Get specific version")
async def get_video_package_version(
    video_id: UUID,
    version_number: int,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return the snapshot for a specific version of a video package."""
    repo = VideoPackageRepository(db)
    pkg = await repo.get_by_id_for_user(video_id, user.user_id)
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video package not found")
    ver = await repo.get_version(video_id, version_number)
    if ver is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return {
        "version_id": str(ver.version_id),
        "video_id": str(video_id),
        "version_number": ver.version_number,
        "snapshot": ver.snapshot_jsonb,
        "created_at": ver.created_at.isoformat() if ver.created_at else None,
    }


@router.get("/{video_id}/export", summary="Export video package")
async def export_video_package(
    video_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
    format: str = Query(pattern="^(json|pdf|zip)$", default="json"),
):
    """Export video package.

    json: returns full package inline (same as GET /{video_id}).
    pdf/zip: returns 501 (async export not yet implemented).
    """
    repo = VideoPackageRepository(db)
    pkg = await repo.get_by_id_for_user(video_id, user.user_id)
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video package not found")

    if format == "json":
        return _pkg_full(pkg)

    if format == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            full = _pkg_full(pkg)
            zf.writestr("package.json", json.dumps(full, ensure_ascii=False, indent=2))
            zf.writestr("outline.md", pkg.outline_md or "")
            zf.writestr("script.md", pkg.script_md or "")
            zf.writestr(
                "storyboard.json",
                json.dumps(pkg.storyboard_jsonb, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "visual_spec.json",
                json.dumps(pkg.visual_spec_jsonb, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "citations.json",
                json.dumps(pkg.citations_report_jsonb, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "evidence_map.json",
                json.dumps(pkg.evidence_map_jsonb, ensure_ascii=False, indent=2),
            )
        buf.seek(0)
        slug = pkg.topic[:40].replace(" ", "_").replace("/", "-")
        filename = f"video_package_{slug}_v{pkg.version}.zip"
        return Response(
            content=buf.read(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Export format '{format}' is not yet implemented.",
    )


@router.delete(
    "/{video_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete video package",
)
async def delete_video_package(
    video_id: UUID,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Soft-delete a video package (sets deleted_at)."""
    repo = VideoPackageRepository(db)
    deleted = await repo.soft_delete(video_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video package not found")
    await db.commit()
