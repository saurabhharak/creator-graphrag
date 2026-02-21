"""0002 — Books + book_files + book_permissions tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── books ──────────────────────────────────────────────────────────────
    op.create_table(
        "books",
        sa.Column("book_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=True),
        sa.Column("year", sa.Integer, nullable=True),
        sa.Column("edition", sa.Text, nullable=True),
        sa.Column("language_primary", sa.Text, nullable=False),
        sa.Column("publisher", sa.Text, nullable=True),
        sa.Column("isbn", sa.Text, nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default="[]"),
        sa.Column("visibility", sa.Text, nullable=False, server_default="private"),
        sa.Column("usage_rights", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_books_created_by", "books", ["created_by"])
    op.create_index("idx_books_language", "books", ["language_primary"])
    op.create_index("idx_books_org", "books", ["org_id"])
    op.create_index("idx_books_deleted", "books", ["deleted_at"])

    # ── book_files ─────────────────────────────────────────────────────────
    op.create_table(
        "book_files",
        sa.Column("file_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),  # raw_pdf, extracted_text, ocr_json, etc.
        sa.Column("source_format", sa.Text, nullable=True),
        sa.Column("uri", sa.Text, nullable=False),
        sa.Column("checksum_sha256", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("upload_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_book_files_book", "book_files", ["book_id", "kind"])
    op.create_unique_constraint("uq_book_files_checksum", "book_files", ["checksum_sha256"])

    # ── book_permissions ───────────────────────────────────────────────────
    op.create_table(
        "book_permissions",
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_level", sa.Text, nullable=False, server_default="read"),
        sa.Column("granted_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("book_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("book_permissions")
    op.drop_table("book_files")
    op.drop_table("books")
