"""0005 — Chunks and citations tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Snippet max length: 600 chars (enforced in app + schema + DB constraint)
SNIPPET_MAX_LEN = 600


def upgrade() -> None:
    # ── chunks ─────────────────────────────────────────────────────────────
    op.create_table(
        "chunks",
        sa.Column("chunk_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.chapter_id", ondelete="SET NULL"), nullable=True),
        sa.Column("chunk_type", sa.Text, nullable=False),
        sa.Column("language_detected", sa.Text, nullable=False),
        sa.Column("language_confidence", sa.Float, nullable=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("section_title", sa.Text, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("text_hash", sa.Text, nullable=False),
        sa.Column("vector_ref", sa.Text, nullable=True),  # Qdrant point ID
        sa.Column("embedding_model_id", sa.Text, nullable=True),  # e.g. "bge-m3" or "text-embedding-3-large"
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_chunks_book", "chunks", ["book_id"])
    op.create_index("idx_chunks_book_chapter", "chunks", ["book_id", "chapter_id"])
    op.create_index("idx_chunks_type", "chunks", ["chunk_type"])
    op.create_index("idx_chunks_lang", "chunks", ["language_detected"])
    op.create_unique_constraint("uq_chunks_hash_book", "chunks", ["book_id", "text_hash"])

    # ── citations ──────────────────────────────────────────────────────────
    op.create_table(
        "citations",
        sa.Column("citation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.chapter_id", ondelete="SET NULL"), nullable=True),
        sa.Column("chunk_id", UUID(as_uuid=True), sa.ForeignKey("chunks.chunk_id", ondelete="SET NULL"), nullable=True),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        # CHECK constraint enforces snippet max length
        sa.Column("snippet", sa.Text, nullable=False),
        sa.Column("language", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"length(snippet) <= {SNIPPET_MAX_LEN}", name="ck_citation_snippet_len"),
    )
    op.create_index("idx_citations_book_page", "citations", ["book_id", "page_start"])
    op.create_index("idx_citations_chunk", "citations", ["chunk_id"])


def downgrade() -> None:
    op.drop_table("citations")
    op.drop_table("chunks")
