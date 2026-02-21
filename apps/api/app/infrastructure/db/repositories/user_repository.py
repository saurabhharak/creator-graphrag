"""Repository for User, Organization, and AuditLog database operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.user import AuditLog, User

logger = structlog.get_logger(__name__)

# Account lock policy (STANDARDS.md §8.x)
MAX_FAILED_LOGINS: int = 5
LOCK_DURATION_MINUTES: int = 15


class UserRepository:
    """Database operations for User accounts and the append-only audit log.

    Args:
        db: An open AsyncSession bound to the current request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_email(self, email: str) -> User | None:
        """Fetch a non-deleted user by email address.

        Args:
            email: The user's email address (case-sensitive).

        Returns:
            The User ORM object, or None if not found.
        """
        result = await self.db.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Fetch a non-deleted user by primary key.

        Args:
            user_id: UUID of the user.

        Returns:
            The User ORM object, or None if not found.
        """
        result = await self.db.execute(
            select(User).where(User.user_id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        email: str,
        display_name: str,
        password_hash: str,
        role: str = "editor",
        org_id: UUID | None = None,
    ) -> User:
        """Insert a new user record.

        Args:
            email: Must be globally unique.
            display_name: Human-readable name shown in the UI.
            password_hash: bcrypt hash of the plaintext password.
            role: RBAC role (admin | editor | viewer | api_client).
            org_id: Optional organization affiliation.

        Returns:
            The newly created User (flushed but not yet committed).
        """
        user = User(
            user_id=uuid.uuid4(),
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            role=role,
            org_id=org_id,
        )
        self.db.add(user)
        await self.db.flush()
        logger.info("user_created", user_id=str(user.user_id), role=role)
        return user

    async def increment_failed_login(self, user_id: UUID) -> int:
        """Increment the consecutive failed login counter and return the new value.

        Args:
            user_id: UUID of the user who failed to authenticate.

        Returns:
            The updated failed_login_count.
        """
        result = await self.db.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(failed_login_count=User.failed_login_count + 1)
            .returning(User.failed_login_count)
        )
        new_count: int = result.scalar_one()
        return new_count

    async def lock_account(self, user_id: UUID, until: datetime) -> None:
        """Set account lock expiry after too many failed logins.

        Args:
            user_id: UUID of the user to lock.
            until: Timestamp when the lock expires (UTC).
        """
        await self.db.execute(
            update(User).where(User.user_id == user_id).values(locked_until=until)
        )
        logger.warning("account_locked", user_id=str(user_id), until=until.isoformat())

    async def record_successful_login(self, user_id: UUID) -> None:
        """Reset failure counter and record last_login_at on successful authentication.

        Args:
            user_id: UUID of the user who authenticated successfully.
        """
        now = datetime.now(timezone.utc)
        await self.db.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(failed_login_count=0, locked_until=None, last_login_at=now)
        )

    async def write_audit_log(
        self,
        action: str,
        user_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        ip_address: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Append an entry to the append-only audit log.

        Args:
            action: Short action name e.g. ``"user.register"``, ``"user.login.failed"``.
            user_id: Actor performing the action; None for unauthenticated events.
            resource_type: Type of the affected resource e.g. ``"user"``.
            resource_id: UUID of the affected resource.
            ip_address: Client IP address (no PII beyond what's required).
            payload: Additional context dict. Must not contain passwords or tokens.
        """
        entry = AuditLog(
            log_id=uuid.uuid4(),
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            payload_json=payload,
        )
        self.db.add(entry)
        await self.db.flush()
