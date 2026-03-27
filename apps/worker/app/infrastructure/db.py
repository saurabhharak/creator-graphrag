"""Minimal async DB helpers for the ingestion worker.

Uses asyncpg directly (raw SQL) so the worker has no dependency on the
API's SQLAlchemy ORM models. Only the fields touched by the pipeline
need to be listed here — not the full schema.

Connection pooling
------------------
A lazily-created asyncpg pool is shared across all calls that use the
same DSN.  Call ``close_pool()`` during graceful shutdown to release
connections.
"""
from __future__ import annotations

import asyncio
import json
import traceback as tb
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# ── Connection pool (singleton per DSN) ────────────────────────────────────────

_pools: dict[str, asyncpg.Pool] = {}
_pool_lock: asyncio.Lock | None = None  # created lazily (needs a running loop)


def _pg_url(database_url: str) -> str:
    """Strip the SQLAlchemy asyncpg dialect prefix for raw asyncpg."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


def _get_pool_lock() -> asyncio.Lock:
    """Return (and lazily create) the module-level asyncio lock."""
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_pool(database_url: str) -> asyncpg.Pool:
    """Return a shared connection pool for *database_url* (lazy singleton).

    The pool is created on first call and cached for subsequent calls with
    the same DSN.  ``min_size=2, max_size=10`` keeps a handful of warm
    connections without over-allocating.
    """
    dsn = _pg_url(database_url)
    pool = _pools.get(dsn)
    if pool is not None:
        return pool

    async with _get_pool_lock():
        # Double-check after acquiring the lock
        pool = _pools.get(dsn)
        if pool is not None:
            return pool

        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        _pools[dsn] = pool
        logger.info("pg_pool_created", dsn=dsn[:dsn.find("@") + 1] + "***")
        return pool


async def close_pool(database_url: str | None = None) -> None:
    """Close one or all cached connection pools.

    Args:
        database_url: If given, close only the pool for this DSN.
                      If ``None``, close **all** cached pools.
    """
    if database_url is not None:
        dsn = _pg_url(database_url)
        pool = _pools.pop(dsn, None)
        if pool is not None:
            await pool.close()
            logger.info("pg_pool_closed", dsn=dsn[:dsn.find("@") + 1] + "***")
    else:
        for dsn, pool in list(_pools.items()):
            await pool.close()
            logger.info("pg_pool_closed", dsn=dsn[:dsn.find("@") + 1] + "***")
        _pools.clear()


# ── Job status ─────────────────────────────────────────────────────────────────

async def update_job_status(
    database_url: str,
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error_json: dict[str, Any] | None = None,
    metrics_json: dict[str, Any] | None = None,
    celery_task_id: str | None = None,
) -> None:
    """Partially update an ingestion_jobs row.

    Only non-None keyword arguments are applied so callers can update
    a single field without touching the others.

    Uses a shared asyncpg connection pool for efficiency.

    Args:
        database_url: PostgreSQL DSN from worker_settings.DATABASE_URL.
        job_id: String UUID of the ingestion job to update.
        status: New status (queued | running | failed | completed | canceled).
        stage: Pipeline stage name.
        progress: Fractional progress 0.0–1.0.
        message: Human-readable status message.
        error_json: Error detail dict stored as JSONB.
        celery_task_id: Celery async result ID for cancellation.
    """
    set_clauses: list[str] = ["updated_at = NOW()"]
    params: list[Any] = []
    idx = 1

    if status is not None:
        set_clauses.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if stage is not None:
        set_clauses.append(f"stage = ${idx}")
        params.append(stage)
        idx += 1
    if progress is not None:
        set_clauses.append(f"progress = ${idx}")
        params.append(float(progress))
        idx += 1
    if message is not None:
        set_clauses.append(f"message = ${idx}")
        params.append(message)
        idx += 1
    if error_json is not None:
        set_clauses.append(f"error_json = ${idx}::jsonb")
        params.append(json.dumps(error_json))
        idx += 1
    if metrics_json is not None:
        set_clauses.append(f"metrics_json = ${idx}::jsonb")
        params.append(json.dumps(metrics_json))
        idx += 1
    if celery_task_id is not None:
        set_clauses.append(f"celery_task_id = ${idx}")
        params.append(celery_task_id)
        idx += 1

    if idx == 1:
        # Nothing to set beyond updated_at — skip the round-trip
        return

    params.append(job_id)  # WHERE job_id = $idx::uuid
    sql = (
        f"UPDATE ingestion_jobs SET {', '.join(set_clauses)} "
        f"WHERE job_id = ${idx}::uuid"
    )

    pool = await get_pool(database_url)
    async with pool.acquire() as conn:
        try:
            await conn.execute(sql, *params)
            logger.debug(
                "job_status_updated",
                job_id=job_id,
                status=status,
                stage=stage,
                progress=progress,
            )
        except Exception:
            logger.error(
                "job_status_update_failed",
                job_id=job_id,
                exc_info=True,
            )
            raise

    # Publish to Redis pub/sub for SSE streaming (non-blocking, non-critical)
    try:
        from app.core.config import worker_settings as _ws
        await publish_job_event(
            _ws.REDIS_URL,
            job_id,
            stage=stage,
            status=status,
            progress=progress,
        )
    except Exception:
        pass


# ── Knowledge units ────────────────────────────────────────────────────────────

async def insert_knowledge_units(database_url: str, units: list[dict]) -> None:
    """Bulk INSERT knowledge_unit rows via asyncpg executemany.

    Args:
        database_url: PostgreSQL DSN from worker_settings.DATABASE_URL.
        units: List of dicts; each must have keys matching the columns below.
               Missing optional keys default to None.
    """
    if not units:
        return

    sql = """
        INSERT INTO knowledge_units (
            unit_id, source_book_id, source_chunk_id,
            type, language_detected, language_confidence,
            subject, predicate, object,
            payload_jsonb, confidence, status,
            evidence_jsonb, canonical_key
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid,
            $4, $5, $6,
            $7, $8, $9,
            $10::jsonb, $11, $12,
            $13::jsonb, $14
        )
        ON CONFLICT (unit_id) DO NOTHING
    """

    rows = [
        (
            u["unit_id"],
            u["source_book_id"],
            u.get("source_chunk_id"),
            u["type"],
            u["language_detected"],
            u.get("language_confidence"),
            u.get("subject"),
            u.get("predicate"),
            u.get("object"),
            json.dumps(u.get("payload_jsonb", {})),
            float(u["confidence"]),
            u.get("status", "extracted"),
            json.dumps(u.get("evidence_jsonb", [])),
            u.get("canonical_key"),
        )
        for u in units
    ]

    pool = await get_pool(database_url)
    async with pool.acquire() as conn:
        try:
            await conn.executemany(sql, rows)
            logger.info("knowledge_units_inserted", count=len(rows))
        except Exception:
            logger.error("knowledge_units_insert_failed", exc_info=True)
            raise


# ── Chunks ─────────────────────────────────────────────────────────────────────

async def insert_chunks(database_url: str, chunks_data: list[dict]) -> None:
    """Bulk INSERT chunk rows via asyncpg executemany.

    Each dict must have: chunk_id, book_id, chunk_type, language_detected,
    source_type, text, text_hash.
    Optional: chapter_id, language_confidence, page_start, page_end,
              section_title, vector_ref, embedding_model_id.
    """
    if not chunks_data:
        return

    sql = """
        INSERT INTO chunks (
            chunk_id, book_id, chapter_id,
            chunk_type, language_detected, language_confidence,
            source_type, page_start, page_end, section_title,
            text, text_hash, vector_ref, embedding_model_id
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid,
            $4, $5, $6,
            $7, $8, $9, $10,
            $11, $12, $13, $14
        )
        ON CONFLICT (book_id, text_hash) DO NOTHING
    """

    rows = [
        (
            c["chunk_id"],
            c["book_id"],
            c.get("chapter_id"),
            c["chunk_type"],
            c["language_detected"],
            c.get("language_confidence"),
            c["source_type"],
            c.get("page_start"),
            c.get("page_end"),
            c.get("section_title"),
            c["text"],
            c["text_hash"],
            c.get("vector_ref"),
            c.get("embedding_model_id"),
        )
        for c in chunks_data
    ]

    pool = await get_pool(database_url)
    async with pool.acquire() as conn:
        try:
            await conn.executemany(sql, rows)
            logger.info("chunks_inserted", count=len(rows))
        except Exception:
            logger.error("chunks_insert_failed", exc_info=True)
            raise


# ── LLM usage logging ─────────────────────────────────────────────────────────

async def log_llm_usage(
    database_url: str,
    *,
    operation_type: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    book_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """INSERT one row into llm_usage_logs for cost tracking.

    Cost estimation uses gpt-4o pricing as a reference; exact values
    depend on the model — treat as an approximation only.

    Args:
        database_url: PostgreSQL DSN from worker_settings.DATABASE_URL.
        operation_type: "embedding" | "extraction" | "generation" | "repair".
        model_id: Model identifier string (e.g. "gpt-4o").
        input_tokens: Prompt token count.
        output_tokens: Completion token count.
        book_id: Optional book UUID string.
        job_id: Optional ingestion job UUID string.
    """
    import uuid as _uuid

    # gpt-4o reference pricing (USD per 1k tokens)
    _COST_PER_1K = {"input": 0.005, "output": 0.015}
    estimated_cost = round(
        (input_tokens * _COST_PER_1K["input"] + output_tokens * _COST_PER_1K["output"]) / 1000,
        6,
    )

    sql = """
        INSERT INTO llm_usage_logs (
            log_id, operation_type, model_id,
            input_tokens, output_tokens, estimated_cost_usd,
            book_id, job_id
        ) VALUES (
            $1::uuid, $2, $3,
            $4, $5, $6,
            $7::uuid, $8::uuid
        )
    """

    pool = await get_pool(database_url)
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                sql,
                str(_uuid.uuid4()),
                operation_type,
                model_id,
                input_tokens,
                output_tokens,
                estimated_cost,
                book_id,
                job_id,
            )
            logger.debug(
                "llm_usage_logged",
                op=operation_type,
                model=model_id,
                in_tok=input_tokens,
                out_tok=output_tokens,
                cost_usd=estimated_cost,
            )
        except Exception:
            # Non-critical — don't fail the pipeline if cost logging fails
            logger.warning("llm_usage_log_failed", exc_info=True)


# ── Redis pub/sub ──────────────────────────────────────────────────────────────

async def publish_job_event(
    redis_url: str,
    job_id: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    progress: float | None = None,
) -> None:
    """Publish a job progress event to Redis pub/sub for SSE streaming.

    Non-critical — silently swallows errors so the pipeline is never blocked
    by a Redis connectivity issue.
    """
    try:
        import redis.asyncio as _aioredis

        payload = json.dumps({
            k: v for k, v in {
                "job_id": job_id,
                "stage": stage,
                "status": status,
                "progress": progress,
            }.items() if v is not None
        })
        client = _aioredis.from_url(redis_url, decode_responses=True)
        try:
            await client.publish(f"job:{job_id}:events", payload)
        finally:
            await client.aclose()
    except Exception:
        logger.warning("redis_publish_failed", job_id=job_id)


# ── Book lookup ────────────────────────────────────────────────────────────────

async def fetch_book_title(database_url: str, book_id: str) -> str:
    """Return the title of a book by its UUID, or empty string if not found."""
    pool = await get_pool(database_url)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT title FROM books WHERE book_id = $1::uuid",
            book_id,
        )
        return row["title"] if row else ""


# ── Error formatting ──────────────────────────────────────────────────────────

def format_error(exc: BaseException) -> dict[str, str]:
    """Build a compact error_json dict from an exception."""
    return {
        "type": type(exc).__name__,
        "detail": str(exc)[:500],
        "traceback": tb.format_exc()[-2000:],
    }
