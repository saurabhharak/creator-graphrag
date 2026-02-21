"""API key management endpoints (US-AUTH-04)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.deps import CurrentUserDep, DbSession
from app.core.errors import NotFoundError
from app.infrastructure.db.repositories.api_key_repository import (
    ApiKeyRepository,
    generate_api_key,
)

logger = structlog.get_logger(__name__)
router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class CreateApiKeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class CreateApiKeyResponse(BaseModel):
    key_id: str
    label: str
    secret: str  # shown exactly once — caller must store it
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime


class ApiKeyItem(BaseModel):
    key_id: str
    label: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeyItem]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
)
async def create_api_key(
    body: CreateApiKeyRequest,
    user: CurrentUserDep,
    db: DbSession,
):
    """Create a new API key for the authenticated user.

    The ``secret`` field in the response is the only time the plaintext key
    is returned — it is not stored and cannot be recovered. Treat it like a
    password.

    The key can be used as a Bearer token: ``Authorization: Bearer cgr_...``
    """
    raw_key, key_hash = generate_api_key()

    repo = ApiKeyRepository(db)
    api_key = await repo.create(
        user_id=user.user_id,
        label=body.label,
        key_hash=key_hash,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )

    logger.info(
        "api_key_created",
        key_id=str(api_key.key_id),
        user_id=str(user.user_id),
        label=body.label,
    )
    return CreateApiKeyResponse(
        key_id=str(api_key.key_id),
        label=api_key.label,
        secret=raw_key,
        scopes=api_key.scopes,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get(
    "",
    response_model=ApiKeyListResponse,
    summary="List API keys",
)
async def list_api_keys(user: CurrentUserDep, db: DbSession):
    """List all active API keys for the authenticated user.

    Secret values are never returned — only metadata.
    """
    repo = ApiKeyRepository(db)
    keys = await repo.list_for_user(user.user_id)

    return ApiKeyListResponse(
        api_keys=[
            ApiKeyItem(
                key_id=str(k.key_id),
                label=k.label,
                scopes=k.scopes,
                last_used_at=k.last_used_at,
                expires_at=k.expires_at,
                is_active=k.is_active,
                created_at=k.created_at,
            )
            for k in keys
        ]
    )


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
)
async def revoke_api_key(key_id: UUID, user: CurrentUserDep, db: DbSession):
    """Revoke an API key immediately.

    Subsequent requests using this key will be rejected with 401.
    Ownership is verified — users cannot revoke other users' keys.
    """
    repo = ApiKeyRepository(db)
    revoked = await repo.revoke(key_id=key_id, user_id=user.user_id)
    if not revoked:
        raise NotFoundError(f"API key {key_id} not found or already revoked")
