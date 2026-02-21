"""AWS S3 / MinIO client helpers for book file storage."""
from __future__ import annotations

import asyncio
from functools import lru_cache

import boto3
import structlog
from botocore.exceptions import ClientError

from app.core.config import settings

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _s3_boto_client():
    """Return a cached synchronous boto3 S3 client.

    Configuration is read from settings at first call and cached for the
    lifetime of the process. For MinIO compatibility the endpoint_url is
    set when S3_ENDPOINT_URL is configured.
    """
    kwargs: dict = {"region_name": settings.AWS_REGION}
    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        **kwargs,
    )


def generate_presigned_put_url(key: str, bucket: str, ttl_seconds: int) -> str:
    """Generate an S3 presigned PUT URL (CPU-only — safe to call synchronously).

    The presigned URL allows the client to upload a file directly to S3
    without exposing AWS credentials. The URL expires after ``ttl_seconds``.

    Args:
        key: S3 object key (e.g. ``books/{book_id}/raw.pdf``).
        bucket: S3 bucket name.
        ttl_seconds: URL validity window in seconds.

    Returns:
        Presigned PUT URL string.
    """
    client = _s3_boto_client()
    url: str = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
    return url


async def object_exists(key: str, bucket: str) -> bool:
    """Check whether an S3 object exists by issuing a HEAD request.

    Wraps the synchronous boto3 ``head_object`` call in a thread pool so
    it does not block the event loop.

    Args:
        key: S3 object key.
        bucket: S3 bucket name.

    Returns:
        True if the object exists, False on 404/NoSuchKey.

    Raises:
        ClientError: For unexpected S3 errors (not 404).
    """
    def _head() -> bool:
        try:
            _s3_boto_client().head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey"):
                return False
            logger.error("s3_head_object_error", key=key, bucket=bucket, error=str(exc))
            raise

    return await asyncio.to_thread(_head)


async def get_object_size(key: str, bucket: str) -> int | None:
    """Return the ContentLength of an S3 object in bytes, or None if not found.

    Args:
        key: S3 object key.
        bucket: S3 bucket name.

    Returns:
        File size in bytes, or None if the object does not exist.
    """
    def _head() -> int | None:
        try:
            response = _s3_boto_client().head_object(Bucket=bucket, Key=key)
            return response.get("ContentLength")
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return None
            raise

    return await asyncio.to_thread(_head)
