"""Celery task: deliver webhook events with HMAC-SHA256 signing."""
from __future__ import annotations
import hashlib
import hmac
import json

import httpx
import structlog

from app.worker import app

logger = structlog.get_logger(__name__)

# Retry schedule: 5 total attempts (initial + 4 retries)
# Delays: 1 min → 5 min → 30 min → 2 hr
RETRY_DELAYS = [60, 300, 1800, 7200]

SUPPORTED_EVENTS = [
    "job.completed",
    "job.failed",
    "ku_review.ready",
    "video_package.created",
]


@app.task(
    bind=True,
    name="app.tasks.webhooks.deliver_webhook",
    max_retries=4,
    queue="webhooks",
    acks_late=True,
    soft_time_limit=25,
    time_limit=30,
)
def deliver_webhook(self, webhook_id: str, event: str, payload: dict) -> None:
    """
    Deliver a webhook event to a registered endpoint.
    - Signs payload with HMAC-SHA256 in X-Creator-Signature header
    - Retries up to 4 times (5 total attempts) with schedule: 1m/5m/30m/2h
    """
    # TODO(#0): fetch webhook from DB (url, secret_token_hash)
    # For now, demonstrate the signing logic:
    secret = "webhook_secret_from_db"
    body = json.dumps({"event": event, "data": payload}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    countdown = RETRY_DELAYS[self.request.retries] if self.request.retries < len(RETRY_DELAYS) else RETRY_DELAYS[-1]

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                url="https://example.com/webhook",  # from DB
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Creator-Signature": f"sha256={signature}",
                    "X-Event": event,
                    "X-Webhook-Id": webhook_id,
                },
            )
            response.raise_for_status()
        logger.info("webhook_delivered", webhook_id=webhook_id, event=event)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "webhook_delivery_failed",
            webhook_id=webhook_id,
            event=event,
            status_code=exc.response.status_code,
            retry=self.request.retries,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=countdown)
    except Exception as exc:
        logger.error("webhook_error", webhook_id=webhook_id, error=str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=countdown)
