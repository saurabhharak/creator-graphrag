"""Celery application initialization."""
from __future__ import annotations
from celery import Celery
from celery.schedules import crontab

from app.core.config import worker_settings

app = Celery(
    "creator_graphrag_worker",
    broker=worker_settings.REDIS_CELERY_URL,
    backend=worker_settings.REDIS_URL,
    include=[
        "app.tasks.ingest",
        "app.tasks.ocr",
        "app.tasks.chunk",
        "app.tasks.embed",
        "app.tasks.extract_units",
        "app.tasks.build_graph",
        "app.tasks.webhooks",
        "app.tasks.scheduled",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_soft_time_limit=worker_settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=worker_settings.CELERY_TASK_SOFT_TIME_LIMIT + 60,
    worker_prefetch_multiplier=1,
    task_acks_late=True,  # re-queue on worker crash
    # Queue routing: different resource profiles
    task_routes={
        "app.tasks.ocr.*": {"queue": "ocr"},
        "app.tasks.embed.*": {"queue": "embed"},
        "app.tasks.build_graph.*": {"queue": "graph"},
        "app.tasks.webhooks.*": {"queue": "webhooks"},
        "*": {"queue": "default"},
    },
    # Celery Beat schedules
    beat_schedule={
        "cleanup-expired-exports": {
            "task": "app.tasks.scheduled.cleanup_expired_exports",
            "schedule": crontab(hour=2, minute=0),  # daily at 2am UTC
        },
        "aggregate-metrics": {
            "task": "app.tasks.scheduled.aggregate_metrics",
            "schedule": crontab(minute=0),  # hourly
        },
        "neo4j-orphan-cleanup": {
            "task": "app.tasks.scheduled.neo4j_orphan_cleanup",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),  # weekly Sunday
        },
    },
)
