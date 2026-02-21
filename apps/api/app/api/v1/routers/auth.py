"""Authentication endpoints: register, login, refresh, logout, me."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.v1.deps import CurrentUserDep, DbSession, RedisClient
from app.core.config import settings
from app.core.errors import ConflictError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.infrastructure.db.repositories.user_repository import (
    LOCK_DURATION_MINUTES,
    MAX_FAILED_LOGINS,
    UserRepository,
)

logger = structlog.get_logger(__name__)
router = APIRouter()

# Redis key template for revoked JTIs (refresh tokens)
_JTI_REVOKED_KEY = "jti:revoked:{jti}"
# TTL matches refresh token lifetime + 1 day so revocations outlive tokens
_JTI_TTL_SECONDS = (settings.JWT_REFRESH_TOKEN_TTL_DAYS + 1) * 86400


# ── Request / Response schemas ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # seconds until access token expires


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    created_at: datetime


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    body: RegisterRequest,
    db: DbSession,
    redis: RedisClient,
    request: Request,
):
    """Create a new user account. Default role: editor.

    Raises 409 if the email is already registered.
    """
    repo = UserRepository(db)

    existing = await repo.get_by_email(body.email)
    if existing:
        raise ConflictError("Email address is already registered")

    user = await repo.create(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
    )

    await repo.write_audit_log(
        action="user.register",
        user_id=user.user_id,
        resource_type="user",
        resource_id=user.user_id,
        ip_address=request.client.host if request.client else None,
    )

    access_token = create_access_token(subject=str(user.user_id), role=user.role)
    refresh_token = create_refresh_token(subject=str(user.user_id), jti=str(uuid.uuid4()))

    logger.info("user_registered", user_id=str(user.user_id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_TTL_MINUTES * 60,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email + password",
)
async def login(
    body: LoginRequest,
    db: DbSession,
    redis: RedisClient,
    request: Request,
):
    """Authenticate and receive JWT tokens.

    Returns generic 401 to avoid user enumeration.
    Locks the account for 15 min after 5 consecutive failures.
    """
    repo = UserRepository(db)
    ip = request.client.host if request.client else None

    user = await repo.get_by_email(body.email)
    if user is None:
        # Constant-time path: don't leak whether the email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check account lock
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account temporarily locked. Try again later.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(body.password, user.password_hash):
        new_count = await repo.increment_failed_login(user.user_id)
        await repo.write_audit_log(
            action="user.login.failed",
            user_id=user.user_id,
            ip_address=ip,
            payload={"failed_count": new_count},
        )
        if new_count >= MAX_FAILED_LOGINS:
            lock_until = datetime.now(timezone.utc) + timedelta(minutes=LOCK_DURATION_MINUTES)
            await repo.lock_account(user.user_id, lock_until)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    await repo.record_successful_login(user.user_id)
    await repo.write_audit_log(action="user.login", user_id=user.user_id, ip_address=ip)

    jti = str(uuid.uuid4())
    access_token = create_access_token(subject=str(user.user_id), role=user.role)
    refresh_token = create_refresh_token(subject=str(user.user_id), jti=jti)

    logger.info("user_logged_in", user_id=str(user.user_id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_TTL_MINUTES * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh(body: RefreshRequest, db: DbSession, redis: RedisClient):
    """Exchange a valid refresh token for a new token pair.

    The old refresh JTI is immediately revoked in Redis to prevent replay.
    Returns 401 if the token is expired, malformed, or already revoked.
    """
    try:
        claims = decode_token(body.refresh_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if claims.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a refresh token",
        )

    jti: str = claims.get("jti", "")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing jti claim",
        )

    # Revocation check — deny already-used or explicitly revoked tokens
    if await redis.get(_JTI_REVOKED_KEY.format(jti=jti)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used or revoked",
        )

    # Revoke old JTI *before* issuing new tokens (prevents replay on error)
    await redis.set(_JTI_REVOKED_KEY.format(jti=jti), "1", ex=_JTI_TTL_SECONDS)

    try:
        user_id = UUID(claims.get("sub", ""))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        )

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    new_jti = str(uuid.uuid4())
    access_token = create_access_token(subject=str(user.user_id), role=user.role)
    refresh_token = create_refresh_token(subject=str(user.user_id), jti=new_jti)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_TTL_MINUTES * 60,
    )


@router.delete(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout (revoke refresh token)",
)
async def logout(body: RefreshRequest, redis: RedisClient):
    """Revoke the refresh token. The access token expires naturally (≤ 15 min)."""
    try:
        claims = decode_token(body.refresh_token)
    except ValueError:
        # Silently succeed — can't revoke an already-invalid token
        return

    jti: str = claims.get("jti", "")
    if jti:
        await redis.set(_JTI_REVOKED_KEY.format(jti=jti), "1", ex=_JTI_TTL_SECONDS)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get current user info",
)
async def me(user: CurrentUserDep, db: DbSession):
    """Return the full profile of the authenticated user from the database."""
    repo = UserRepository(db)
    db_user = await repo.get_by_id(user.user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User record not found",
        )
    return MeResponse(
        user_id=str(db_user.user_id),
        email=db_user.email,
        display_name=db_user.display_name,
        role=db_user.role,
        created_at=db_user.created_at,
    )
