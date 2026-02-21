"""Webhook registration and management endpoints."""
from __future__ import annotations
import secrets
import uuid
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.api.v1.deps import CurrentUserDep

router = APIRouter()

SUPPORTED_EVENTS = [
    "job.completed",
    "job.failed",
    "ku_review.ready",
    "video_package.created",
]


class CreateWebhookRequest(BaseModel):
    url: HttpUrl
    events: list[str] = Field(min_length=1)
    label: str = Field(min_length=1, max_length=200)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Register webhook",
)
async def create_webhook(body: CreateWebhookRequest, user: CurrentUserDep):
    """
    Register a webhook URL for one or more events.
    Deliveries signed with HMAC-SHA256 in X-Signature header.
    Supported events: job.completed, job.failed, ku_review.ready, video_package.created
    """
    invalid = [e for e in body.events if e not in SUPPORTED_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported event types: {invalid}. Supported: {SUPPORTED_EVENTS}",
        )
    # TODO(#0): generate secret_token, store in DB
    return {
        "webhook_id": str(uuid.uuid4()),
        "url": str(body.url),
        "events": body.events,
        "secret_token": secrets.token_hex(32),  # shown once at creation
        "is_active": True,
    }


@router.get("", summary="List webhooks")
async def list_webhooks(user: CurrentUserDep):
    """List all registered webhooks for the current user (no secret tokens shown)."""
    # TODO(#0): fetch from DB
    return {"webhooks": []}


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete webhook",
)
async def delete_webhook(webhook_id: UUID, user: CurrentUserDep):
    """Delete a webhook registration."""
    # TODO(#0): verify ownership, delete from DB
    pass
