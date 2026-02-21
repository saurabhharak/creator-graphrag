"""JWT authentication utilities (RS256)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_private_key: str | None = None
_public_key: str | None = None


def _load_keys() -> None:
    global _private_key, _public_key
    if _private_key is None:
        _private_key = Path(settings.JWT_PRIVATE_KEY_PATH).read_text()
    if _public_key is None:
        _public_key = Path(settings.JWT_PUBLIC_KEY_PATH).read_text()


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt.

    Args:
        password: The plaintext password to hash.

    Returns:
        A bcrypt-hashed password string.
    """
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.

    Args:
        plain: The plaintext password to verify.
        hashed: The stored bcrypt hash.

    Returns:
        True if the password matches, False otherwise.
    """
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, role: str) -> str:
    """Create a short-lived RS256 access token.

    Args:
        subject: The user ID (UUID string) embedded as the ``sub`` claim.
        role: The user's role (e.g. ``"editor"``, ``"admin"``).

    Returns:
        A signed JWT access token string.
    """
    _load_keys()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_TTL_MINUTES
    )
    payload = {
        "sub": subject,
        "role": role,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "exp": expire,
    }
    return jwt.encode(payload, _private_key, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, jti: str) -> str:
    """Create a long-lived RS256 refresh token with a revocable JTI.

    Args:
        subject: The user ID (UUID string) embedded as the ``sub`` claim.
        jti: A unique token identifier stored in Redis for revocation.

    Returns:
        A signed JWT refresh token string.
    """
    _load_keys()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_TTL_DAYS
    )
    payload = {
        "sub": subject,
        "jti": jti,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, _private_key, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token using the RS256 public key.

    Args:
        token: The raw JWT string to decode.

    Returns:
        The decoded payload as a dictionary.

    Raises:
        ValueError: If the token is invalid, expired, or cannot be decoded.
    """
    _load_keys()
    try:
        return jwt.decode(token, _public_key, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e
