"""0004 — Chapters and book_pages tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── chapters ───────────────────────────────────────────────────────────
    op.create_table(
        "chapters",
        sa.Column("chapter_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("order_index", sa.Integer, nullable=False),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),  # structure detection confidence
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_chapters_book_order", "chapters", ["book_id", "order_index"])

    # ── book_pages (optional; stores per-page OCR output) ──────────────────
    op.create_table(
        "book_pages",
        sa.Column("page_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("language_detected", sa.Text, nullable=True),
        sa.Column("language_confidence", sa.Float, nullable=True),
        sa.Column("source_type", sa.Text, nullable=False, server_default="digital_text"),
        sa.Column("ocr_confidence", sa.Float, nullable=True),
        sa.Column("text_uri", sa.Text, nullable=True),  # S3 URI for full page text
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_pages_book_num", "book_pages", ["book_id", "page_number"])
    op.create_unique_constraint("uq_pages_book_page", "book_pages", ["book_id", "page_number"])


def downgrade() -> None:
    op.drop_table("book_pages")
    op.drop_table("chapters")
