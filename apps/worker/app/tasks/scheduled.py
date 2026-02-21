"""Celery Beat scheduled tasks."""
from __future__ import annotations

import structlog

from app.worker import app

logger = structlog.get_logger(__name__)


@app.task(
    name="app.tasks.scheduled.cleanup_expired_exports",
    acks_late=True,
    soft_time_limit=180,
    time_limit=300,
)
def cleanup_expired_exports():
    """Daily: delete export job artifacts from S3 older than 24 hours."""
    logger.info("cleanup_expired_exports_start")
    # TODO(#0): query export_jobs with created_at < now - 24h, delete S3 objects
    logger.info("cleanup_expired_exports_done")


@app.task(
    name="app.tasks.scheduled.aggregate_metrics",
    acks_late=True,
    soft_time_limit=60,
    time_limit=120,
)
def aggregate_metrics():
    """Hourly: aggregate citation coverage and unit approval rates per book."""
    logger.info("aggregate_metrics_start")
    # TODO(#0): compute per-book metrics, store in book_metrics table (or update books table)
    logger.info("aggregate_metrics_done")


@app.task(
    name="app.tasks.scheduled.neo4j_orphan_cleanup",
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def neo4j_orphan_cleanup():
    """Weekly: remove orphaned Neo4j nodes (no evidence, no approved units referencing them)."""
    logger.info("neo4j_orphan_cleanup_start")
    # TODO(#0): Cypher query to find and delete orphaned nodes
    logger.info("neo4j_orphan_cleanup_done")
