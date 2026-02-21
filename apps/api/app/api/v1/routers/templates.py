"""Video generation template management endpoints (EPIC 8)."""
from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.deps import AdminDep, CurrentUserDep, DbSession
from app.infrastructure.db.repositories.template_repository import TemplateRepository

logger = structlog.get_logger(__name__)
router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    format: str = Field(pattern="^(shorts|explainer|deep_dive)$")
    audience_level: str | None = Field(None, pattern="^(beginner|intermediate)$")
    required_sections: list[str] = []
    scene_min: int = Field(default=5, ge=3, le=30)
    scene_max: int = Field(default=8, ge=3, le=40)
    pacing_constraints: dict = {}
    output_schema: dict = {}


class UpdateTemplateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    audience_level: str | None = Field(None, pattern="^(beginner|intermediate)$")
    required_sections: list[str] | None = None
    scene_min: int | None = Field(None, ge=3, le=30)
    scene_max: int | None = Field(None, ge=3, le=40)
    pacing_constraints: dict | None = None
    output_schema: dict | None = None


def _tmpl_response(t) -> dict:
    return {
        "template_id": str(t.template_id),
        "name": t.name,
        "format": t.format,
        "audience_level": t.audience_level,
        "required_sections": t.required_sections,
        "scene_min": t.scene_min,
        "scene_max": t.scene_max,
        "pacing_constraints": t.pacing_constraints,
        "output_schema": t.output_schema,
        "is_system": t.is_system,
        "created_by": str(t.created_by) if t.created_by else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", summary="List templates")
async def list_templates(
    user: CurrentUserDep,
    db: DbSession,
):
    """Return all available templates (system + custom), ordered by creation date."""
    repo = TemplateRepository(db)
    templates = await repo.list_all(include_system=True)
    return {"templates": [_tmpl_response(t) for t in templates]}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create template (admin only)",
)
async def create_template(
    body: CreateTemplateRequest,
    user: AdminDep,
    db: DbSession,
):
    """Create a custom generation template. Admin only."""
    repo = TemplateRepository(db)
    template = await repo.create(
        created_by=user.user_id,
        name=body.name,
        format=body.format,
        audience_level=body.audience_level,
        required_sections=body.required_sections,
        scene_min=body.scene_min,
        scene_max=body.scene_max,
        pacing_constraints=body.pacing_constraints,
        output_schema=body.output_schema,
    )
    await db.commit()
    return _tmpl_response(template)


@router.get("/{template_id}", summary="Get template")
async def get_template(
    template_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return template detail."""
    repo = TemplateRepository(db)
    template = await repo.get_by_id(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return _tmpl_response(template)


@router.patch("/{template_id}", summary="Update template (admin only)")
async def update_template(
    template_id: UUID,
    body: UpdateTemplateRequest,
    user: AdminDep,
    db: DbSession,
):
    """Update a custom template. Admin only. System templates cannot be modified."""
    repo = TemplateRepository(db)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    template = await repo.update(template_id, patch)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or is a system template (cannot be modified)",
        )
    await db.commit()
    return _tmpl_response(template)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete template (admin only)",
)
async def delete_template(
    template_id: UUID,
    user: AdminDep,
    db: DbSession,
):
    """Delete a custom template. Admin only. System templates cannot be deleted."""
    repo = TemplateRepository(db)
    deleted = await repo.soft_delete(template_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or is a system template (cannot be deleted)",
        )
    await db.commit()
