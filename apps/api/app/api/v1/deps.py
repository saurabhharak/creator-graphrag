"""FastAPI dependency injection: auth, DB session, Redis, rate limiting."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from app.core.security import decode_token
from app.infrastructure.cache.redis_client import get_redis
from app.infrastructure.db.repositories.api_key_repository import (
    ApiKeyRepository,
    hash_api_key,
)
from app.infrastructure.db.repositories.user_repository import UserRepository
from app.infrastructure.db.session import get_db

logger = structlog.get_logger(__name__)
security = HTTPBearer()


class CurrentUser:
    def __init__(self, user_id: UUID, email: str, role: str, org_id: UUID | None = None):
        self.user_id = user_id
        self.email = email
        self.role = role
        self.org_id = org_id


# ── Infrastructure dependencies (defined early so get_current_user can reference them) ──
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[aioredis.Redis, Depends(get_redis)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    """Validate a Bearer token — either a JWT access token or an API key.

    Authentication flow:
    1. Try to decode the token as a JWT (RS256). Fast path — no DB hit.
    2. If decoding fails, treat the token as an API key: hash it (SHA-256)
       and look it up in the database. Update ``last_used_at`` on success.

    Args:
        credentials: HTTP Bearer credentials from the Authorization header.
        db: Async DB session for API key lookups (injected by FastAPI).

    Returns:
        A lightweight CurrentUser populated from JWT claims or DB user record.

    Raises:
        HTTPException 401: Token is invalid, expired, revoked, or user is inactive.
    """
    token = credentials.credentials

    # ── JWT path (no DB hit for valid access tokens) ─────────────────────────
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is not an access token",
            )
        user_id = UUID(payload["sub"])
        role = payload.get("role", "viewer")
        structlog.contextvars.bind_contextvars(
            user_id=str(user_id), role=role, auth_method="jwt"
        )
        return CurrentUser(user_id=user_id, email="", role=role)
    except ValueError:
        pass  # Not a valid JWT — fall through to API key path

    # ── API key path ─────────────────────────────────────────────────────────
    key_hash = hash_api_key(token)
    api_repo = ApiKeyRepository(db)
    api_key = await api_repo.get_by_hash(key_hash)

    if api_key is None or not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await api_repo.update_last_used(api_key.key_id)

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(api_key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    structlog.contextvars.bind_contextvars(
        user_id=str(user.user_id), role=user.role, auth_method="api_key"
    )
    return CurrentUser(
        user_id=user.user_id,
        email=user.email,
        role=user.role,
        org_id=user.org_id,
    )


def require_role(*roles: str):
    """Return a FastAPI dependency that enforces the caller holds one of the given roles.

    Args:
        *roles: One or more role strings (e.g. ``"admin"``, ``"editor"``).

    Returns:
        An async dependency that resolves to the validated CurrentUser.

    Raises:
        ForbiddenError: If the authenticated user's role is not in ``roles``.
    """
    async def check_role(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise ForbiddenError(
                f"Requires one of roles: {list(roles)}. Your role: {user.role}"
            )
        return user
    return check_role


# ── Auth convenience dependencies ────────────────────────────────────────────
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminDep = Annotated[CurrentUser, Depends(require_role("admin"))]
EditorOrAdminDep = Annotated[CurrentUser, Depends(require_role("editor", "admin"))]
