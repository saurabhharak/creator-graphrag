"""Analytics & quality endpoints (EPIC 10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Query
from sqlalchemy import cast, func, select, String

from app.api.v1.deps import AdminDep, CurrentUserDep, DbSession
from app.core.config import settings
from app.core.errors import NotFoundError
from app.infrastructure.db.models.books import Book, Chunk
from app.infrastructure.db.models.knowledge_units import KnowledgeUnit, LlmUsageLog

logger = structlog.get_logger(__name__)
router = APIRouter()


# ─── US-ANALYTICS-01: Citation coverage ──────────────────────────────────────

@router.get("/books/{book_id}/coverage", summary="Citation coverage for a book")
async def book_coverage(
    book_id: UUID,
    user: CurrentUserDep,
    db: DbSession,
):
    """Return chunk count, unit counts by status, and approval rate for a book.

    The `citation_coverage` field is defined as
    ``approved_units / total_units`` — it measures how much of the extracted
    knowledge has been human-reviewed and confirmed.
    """
    book = (
        await db.execute(
            select(Book).where(
                Book.book_id == book_id,
                Book.created_by == user.user_id,
                Book.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if book is None:
        raise NotFoundError(f"Book {book_id} not found")

    # Total non-deleted chunks
    chunk_count: int = (
        await db.execute(
            select(func.count()).where(
                Chunk.book_id == book_id,
                Chunk.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    # KU counts grouped by status
    ku_rows = (
        await db.execute(
            select(KnowledgeUnit.status, func.count())
            .where(KnowledgeUnit.source_book_id == book_id)
            .group_by(KnowledgeUnit.status)
        )
    ).all()
    units_by_status: dict[str, int] = {row[0]: row[1] for row in ku_rows}
    total_units = sum(units_by_status.values())
    approved_units = units_by_status.get("approved", 0)
    citation_coverage = round(approved_units / total_units, 4) if total_units > 0 else None

    return {
        "book_id": str(book_id),
        "book_title": book.title,
        "chunk_count": chunk_count,
        "units_total": total_units,
        "units_by_status": units_by_status,
        "citation_coverage": citation_coverage,
    }


# ─── US-ANALYTICS-02: LLM usage / cost ───────────────────────────────────────

@router.get("/llm-usage", summary="LLM token usage and cost breakdown (admin only)")
async def llm_usage(
    user: AdminDep,
    db: DbSession,
    from_dt: datetime | None = Query(None, alias="from"),
    to_dt: datetime | None = Query(None, alias="to"),
    group_by: str = Query(default="operation", pattern="^(operation|book|user)$"),
):
    """Return LLM token usage and estimated USD cost, grouped by operation, book, or user.

    Defaults to the last 30 days.  Admin-only endpoint.
    """
    now = datetime.now(timezone.utc)
    if from_dt is None:
        from_dt = now - timedelta(days=30)
    if to_dt is None:
        to_dt = now

    time_filter = [
        LlmUsageLog.created_at >= from_dt,
        LlmUsageLog.created_at <= to_dt,
    ]

    if group_by == "operation":
        group_col = LlmUsageLog.operation_type
    elif group_by == "book":
        group_col = cast(LlmUsageLog.book_id, String)
    else:  # user
        group_col = cast(LlmUsageLog.user_id, String)

    rows = (
        await db.execute(
            select(
                group_col.label("group_key"),
                func.sum(LlmUsageLog.input_tokens).label("input_tokens"),
                func.sum(LlmUsageLog.output_tokens).label("output_tokens"),
                func.sum(LlmUsageLog.estimated_cost_usd).label("total_cost_usd"),
                func.count().label("call_count"),
            )
            .where(*time_filter)
            .group_by(group_col)
            .order_by(func.sum(LlmUsageLog.estimated_cost_usd).desc())
        )
    ).all()

    # Overall totals for the period
    totals_row = (
        await db.execute(
            select(
                func.sum(LlmUsageLog.input_tokens),
                func.sum(LlmUsageLog.output_tokens),
                func.sum(LlmUsageLog.estimated_cost_usd),
                func.count(),
            ).where(*time_filter)
        )
    ).one()

    return {
        "from": from_dt.isoformat(),
        "to": to_dt.isoformat(),
        "group_by": group_by,
        "breakdown": [
            {
                "group_key": str(row.group_key) if row.group_key else "unknown",
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "total_cost_usd": float(row.total_cost_usd or 0),
                "call_count": row.call_count,
            }
            for row in rows
        ],
        "totals": {
            "input_tokens": totals_row[0] or 0,
            "output_tokens": totals_row[1] or 0,
            "total_cost_usd": float(totals_row[2] or 0),
            "call_count": totals_row[3] or 0,
        },
    }


# ─── LLM provider balance ─────────────────────────────────────────────────────

@router.get("/llm-balance", summary="Fetch LLM provider account balance")
async def llm_balance(user: CurrentUserDep):
    """Return the current balance from the Zenmux account.

    Uses ``ZENMUX_USER_TOKEN`` (dashboard session token) to call
    ``https://zenmux.ai/api/user/info``.  Falls back to a dashboard link
    if the token is not configured.
    """
    base_url = (settings.OPENAI_BASE_URL or "").rstrip("/")
    user_token = settings.ZENMUX_USER_TOKEN

    # Derive the Zenmux web origin from the API base URL
    # e.g. https://zenmux.ai/api/v1 → https://zenmux.ai
    import re
    origin_match = re.match(r"(https?://[^/]+)", base_url)
    origin = origin_match.group(1) if origin_match else "https://zenmux.ai"
    dashboard_url = origin

    if not user_token:
        return {
            "balance_usd": None,
            "currency": None,
            "status": "token_not_configured",
            "dashboard_url": dashboard_url,
            "message": "Add ZENMUX_USER_TOKEN to .env to enable balance lookup",
        }

    info_url = f"{origin}/api/user/info"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                info_url,
                headers={"Authorization": f"Bearer {user_token}", "Accept": "application/json"},
            )
        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
            data = resp.json()
            if data.get("success") and data.get("data"):
                d = data["data"]
                # One-API style: quota is in 1/500000 USD units
                quota = d.get("quota", 0)
                used = d.get("usedQuota", 0)
                remaining_raw = quota - used
                balance_usd = round(remaining_raw / 500_000, 4)
                return {
                    "balance_usd": balance_usd,
                    "currency": "USD",
                    "status": "ok",
                    "dashboard_url": dashboard_url,
                }
            else:
                return {
                    "balance_usd": None,
                    "currency": None,
                    "status": "token_invalid",
                    "dashboard_url": dashboard_url,
                    "message": "Token rejected by Zenmux — check ZENMUX_USER_TOKEN",
                }
        else:
            return {
                "balance_usd": None,
                "currency": None,
                "status": f"error_{resp.status_code}",
                "dashboard_url": dashboard_url,
            }
    except Exception as exc:
        logger.warning("llm_balance_fetch_failed", error=str(exc))
        return {
            "balance_usd": None,
            "currency": None,
            "status": "unreachable",
            "dashboard_url": dashboard_url,
        }
