"""Repository for ApiKey database operations."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.user import ApiKey

logger = structlog.get_logger(__name__)

# Key prefix makes secrets identifiable in logs/pastes without revealing value
_KEY_PREFIX = "cgr_"


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key secret and its SHA-256 hash.

    Returns:
        A tuple of ``(raw_key, key_hash)`` where ``raw_key`` must be shown to
        the user exactly once and ``key_hash`` is what is stored in the DB.
    """
    raw_key = _KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def hash_api_key(raw_key: str) -> str:
    """Compute the SHA-256 hash of a raw API key for DB lookup.

    Args:
        raw_key: The plaintext API key as presented in the Authorization header.

    Returns:
        Hex-encoded SHA-256 digest (64 chars).
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ApiKeyRepository:
    """Database operations for API key records.

    Args:
        db: An open AsyncSession bound to the current request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        user_id: UUID,
        label: str,
        key_hash: str,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> ApiKey:
        """Insert a new API key record.

        Args:
            user_id: Owner of the key.
            label: Human-readable description.
            key_hash: SHA-256 hex digest of the raw key (stored, never plaintext).
            scopes: List of permission scope strings e.g. ``["books:read"]``.
            expires_at: Optional expiry timestamp (UTC). None means no expiry.

        Returns:
            The newly created ApiKey (flushed but not yet committed).
        """
        api_key = ApiKey(
            key_id=uuid.uuid4(),
            user_id=user_id,
            key_hash=key_hash,
            label=label,
            scopes=scopes,
            expires_at=expires_at,
        )
        self.db.add(api_key)
        await self.db.flush()
        logger.info("api_key_created", key_id=str(api_key.key_id), user_id=str(user_id))
        return api_key

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        """Fetch an active API key by its SHA-256 hash.

        Args:
            key_hash: SHA-256 hex digest of the raw bearer token.

        Returns:
            The ApiKey ORM object, or None if not found.
        """
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID) -> list[ApiKey]:
        """Return all active API keys belonging to a user, newest first.

        Args:
            user_id: UUID of the owning user.

        Returns:
            List of ApiKey objects (never includes key_hash — callers must not expose it).
        """
        result = await self.db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user_id, ApiKey.is_active.is_(True))
            .order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(self, key_id: UUID, user_id: UUID) -> bool:
        """Soft-revoke a key by setting is_active=false.

        Verifies ownership before revoking to prevent cross-user deletion.

        Args:
            key_id: UUID of the key to revoke.
            user_id: UUID of the requesting user (ownership check).

        Returns:
            True if the key was found and revoked, False if not found or not owned.
        """
        result = await self.db.execute(
            update(ApiKey)
            .where(ApiKey.key_id == key_id, ApiKey.user_id == user_id, ApiKey.is_active.is_(True))
            .values(is_active=False)
            .returning(ApiKey.key_id)
        )
        revoked = result.scalar_one_or_none() is not None
        if revoked:
            logger.info("api_key_revoked", key_id=str(key_id), user_id=str(user_id))
        return revoked

    async def update_last_used(self, key_id: UUID) -> None:
        """Record the current UTC timestamp as last_used_at.

        Args:
            key_id: UUID of the key that was just authenticated with.
        """
        now = datetime.now(timezone.utc)
        await self.db.execute(
            update(ApiKey).where(ApiKey.key_id == key_id).values(last_used_at=now)
        )
