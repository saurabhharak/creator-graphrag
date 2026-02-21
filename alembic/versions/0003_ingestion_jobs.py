"""0003 — Ingestion jobs table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Stage weights for progress calculation (must sum to 1.0)
# Stored as a comment; used by the worker pipeline
# upload=0.02, ocr=0.35, structure_extract=0.10, chunk=0.10,
# embed=0.20, unit_extract=0.15, graph_build=0.08


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("stage", sa.Text, nullable=False, server_default="upload"),
        sa.Column("progress", sa.Float, nullable=False, server_default="0"),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("error_json", JSONB, nullable=True),
        sa.Column("metrics_json", JSONB, nullable=True),
        sa.Column("config_json", JSONB, nullable=True),  # ingestion config snapshot
        sa.Column("celery_task_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_jobs_book", "ingestion_jobs", ["book_id"])
    op.create_index("idx_jobs_status_stage", "ingestion_jobs", ["status", "stage"])
    op.create_index("idx_jobs_updated", "ingestion_jobs", ["updated_at"])
    op.create_index("idx_jobs_created_by", "ingestion_jobs", ["created_by"])


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
