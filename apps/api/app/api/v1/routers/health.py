"""Health, readiness, and liveness endpoints (no auth required)."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.v1.deps import DbSession, RedisClient

logger = structlog.get_logger(__name__)
router = APIRouter()


class ServiceHealth(BaseModel):
    postgres: str = "unknown"
    redis: str = "unknown"
    qdrant: str = "unknown"
    neo4j: str = "unknown"


class ReadinessResponse(BaseModel):
    status: str
    services: ServiceHealth


@router.get("/health", tags=["health"])
async def health():
    """Basic liveness check. No auth required."""
    return {"status": "ok"}


@router.get("/health/live", tags=["health"])
async def liveness():
    """Kubernetes liveness probe. Returns 200 if process is alive."""
    return {"status": "ok"}


@router.get("/health/ready", response_model=ReadinessResponse, tags=["health"])
async def readiness(db: DbSession, redis: RedisClient):
    """Kubernetes readiness probe.

    Checks connectivity to all downstream dependencies.
    Returns 503 if any critical dependency (Postgres, Redis) is unhealthy.
    Qdrant and Neo4j report 'unknown' until their clients are wired in Phase 1.
    """
    services = ServiceHealth()
    all_healthy = True

    # Check PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        services.postgres = "ok"
    except Exception as exc:
        logger.warning("health_check_postgres_failed", error=str(exc))
        services.postgres = "error"
        all_healthy = False

    # Check Redis
    try:
        await redis.ping()
        services.redis = "ok"
    except Exception as exc:
        logger.warning("health_check_redis_failed", error=str(exc))
        services.redis = "error"
        all_healthy = False

    # TODO(#0): inject Qdrant client and verify collection exists
    services.qdrant = "unknown"

    # TODO(#0): inject Neo4j driver and run RETURN 1
    services.neo4j = "unknown"

    response_status = "ok" if all_healthy else "degraded"
    http_status = status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content={"status": response_status, "services": services.model_dump()},
    )
