"""Knowledge unit review and approval endpoints."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.deps import CurrentUserDep, DbSession, EditorOrAdminDep
from app.infrastructure.db.models.knowledge_units import KnowledgeUnit, UnitEdit
from app.infrastructure.db.repositories.knowledge_unit_repository import (
    KnowledgeUnitRepository,
)

router = APIRouter()


# ── Response schemas ──────────────────────────────────────────────────────────


class KnowledgeUnitResponse(BaseModel):
    unit_id: str
    source_book_id: str
    type: str
    language_detected: str
    subject: str | None
    predicate: str | None
    object: str | None
    confidence: float
    status: str
    canonical_key: str | None
    evidence: list
    payload: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, ku: KnowledgeUnit) -> "KnowledgeUnitResponse":
        return cls(
            unit_id=str(ku.unit_id),
            source_book_id=str(ku.source_book_id),
            type=ku.type,
            language_detected=ku.language_detected,
            subject=ku.subject,
            predicate=ku.predicate,
            object=ku.object,
            confidence=ku.confidence,
            status=ku.status,
            canonical_key=ku.canonical_key,
            evidence=ku.evidence_jsonb or [],
            payload=ku.payload_jsonb or {},
            created_at=ku.created_at,
            updated_at=ku.updated_at,
        )


class UnitEditResponse(BaseModel):
    edit_id: str
    editor_user_id: str | None
    patch: dict
    note: str | None
    created_at: datetime

    @classmethod
    def from_orm(cls, edit: UnitEdit) -> "UnitEditResponse":
        return cls(
            edit_id=str(edit.edit_id),
            editor_user_id=str(edit.editor_user_id) if edit.editor_user_id else None,
            patch=edit.patch_jsonb or {},
            note=edit.note,
            created_at=edit.created_at,
        )


# ── Request schemas ───────────────────────────────────────────────────────────


class UpdateKnowledgeUnitRequest(BaseModel):
    status: str | None = Field(None, pattern="^(needs_review|approved|rejected)$")
    subject: str | None = Field(None, max_length=300)
    predicate: str | None = Field(None, max_length=50)
    object: str | None = Field(None, max_length=300)
    payload: dict | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    editor_note: str | None = Field(None, max_length=1000)


class BulkUpdateRequest(BaseModel):
    unit_ids: list[UUID] = Field(max_length=200)
    action: str = Field(pattern="^(approve|reject)$")
    editor_note: str | None = Field(None, max_length=1000)


class BulkUpdateResponse(BaseModel):
    succeeded: int
    failed: int
    errors: list[dict] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", summary="List knowledge units")
async def list_knowledge_units(
    user: CurrentUserDep,
    db: DbSession,
    ku_status: str | None = Query(None, alias="status"),
    book_id: UUID | None = None,
    ku_type: str | None = Query(None, alias="type"),
    language: str | None = None,
    cursor: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    """List knowledge units with optional filters.

    status=needs_review returns units awaiting editor review.
    Units with confidence < 0.65 are auto-tagged needs_review at extraction time.
    Keyset-paginated on created_at DESC — pass the created_at of the last item as cursor.
    """
    repo = KnowledgeUnitRepository(db)
    units = await repo.list_for_book(
        book_id=book_id,
        status=ku_status,
        ku_type=ku_type,
        language=language,
        limit=limit + 1,
        cursor=cursor,
    )
    has_more = len(units) > limit
    items = units[:limit]
    next_cursor = items[-1].created_at.isoformat() if has_more and items else None

    return {
        "items": [KnowledgeUnitResponse.from_orm(u) for u in items],
        "next_cursor": next_cursor,
        "total_count": len(items),
    }


@router.get("/{unit_id}", summary="Get knowledge unit")
async def get_knowledge_unit(
    unit_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return full knowledge unit including complete edit history."""
    repo = KnowledgeUnitRepository(db)
    unit = await repo.get_by_id(unit_id)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found")

    edits = await repo.list_edits(unit_id)
    return {
        **KnowledgeUnitResponse.from_orm(unit).model_dump(),
        "edit_history": [UnitEditResponse.from_orm(e) for e in edits],
    }


@router.patch("/{unit_id}", summary="Update knowledge unit")
async def update_knowledge_unit(
    unit_id: UUID,
    body: UpdateKnowledgeUnitRequest,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Approve, reject, or edit a knowledge unit.

    All changes are recorded in the unit_edits audit table.
    Role required: editor or admin.
    """
    repo = KnowledgeUnitRepository(db)

    patch: dict = {}
    if body.status is not None:
        patch["status"] = body.status
    if body.subject is not None:
        patch["subject"] = body.subject
    if body.predicate is not None:
        patch["predicate"] = body.predicate
    if body.object is not None:
        patch["object"] = body.object
    if body.confidence is not None:
        patch["confidence"] = body.confidence
    if body.payload is not None:
        patch["payload_jsonb"] = body.payload

    updated = await repo.update(
        unit_id,
        patch=patch,
        editor_user_id=user.user_id,
        note=body.editor_note,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found")

    await db.commit()
    return KnowledgeUnitResponse.from_orm(updated)


@router.post(
    "/bulk-update",
    response_model=BulkUpdateResponse,
    summary="Bulk approve/reject",
)
async def bulk_update_knowledge_units(
    body: BulkUpdateRequest,
    user: EditorOrAdminDep,
    db: DbSession,
):
    """Bulk approve or reject up to 200 knowledge units at once."""
    new_status = "approved" if body.action == "approve" else "rejected"
    repo = KnowledgeUnitRepository(db)

    succeeded, failed_ids = await repo.bulk_update_status(
        unit_ids=body.unit_ids,
        new_status=new_status,
        editor_user_id=user.user_id,
        note=body.editor_note,
    )
    await db.commit()

    return BulkUpdateResponse(
        succeeded=succeeded,
        failed=len(failed_ids),
        errors=[{"unit_id": uid, "reason": "not_found"} for uid in failed_ids],
    )


@router.post("/bulk-merge", summary="Bulk merge concepts")
async def bulk_merge_concepts(user: EditorOrAdminDep):
    """Merge duplicate concept knowledge units (Phase 3 — not yet implemented)."""
    return {"merged": 0, "detail": "Concept merging is not yet implemented"}
