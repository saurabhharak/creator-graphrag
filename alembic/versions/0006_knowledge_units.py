"""0006 — Knowledge units + unit_edits + qa_samples + llm_usage_logs.

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── knowledge_units ────────────────────────────────────────────────────
    op.create_table(
        "knowledge_units",
        sa.Column("unit_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_chunk_id", UUID(as_uuid=True), sa.ForeignKey("chunks.chunk_id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("language_detected", sa.Text, nullable=False),
        sa.Column("language_confidence", sa.Float, nullable=True),
        sa.Column("subject", sa.Text, nullable=True),
        sa.Column("predicate", sa.Text, nullable=True),
        sa.Column("object", sa.Text, nullable=True),
        sa.Column("payload_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="extracted"),
        sa.Column("conflict_group_id", UUID(as_uuid=True), nullable=True),  # for conflicting claims
        sa.Column("evidence_jsonb", JSONB, nullable=False, server_default="[]"),
        sa.Column("canonical_key", sa.Text, nullable=True),  # normalized key for dedup
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_ku_status", "knowledge_units", ["status"])
    op.create_index("idx_ku_type", "knowledge_units", ["type"])
    op.create_index("idx_ku_language", "knowledge_units", ["language_detected"])
    op.create_index("idx_ku_book", "knowledge_units", ["source_book_id"])
    op.create_index("idx_ku_canonical", "knowledge_units", ["canonical_key"])
    op.create_index("idx_ku_conflict", "knowledge_units", ["conflict_group_id"])

    # ── unit_edits (audit trail) ───────────────────────────────────────────
    op.create_table(
        "unit_edits",
        sa.Column("edit_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("unit_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_units.unit_id", ondelete="CASCADE"), nullable=False),
        sa.Column("editor_user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("patch_jsonb", JSONB, nullable=False),  # before/after diff
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_unit_edits_unit", "unit_edits", ["unit_id"])

    # ── qa_samples (precision sampling) ───────────────────────────────────
    op.create_table(
        "qa_samples",
        sa.Column("sample_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("unit_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_units.unit_id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("verdict", sa.Text, nullable=False),  # correct|incorrect|partially_correct
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_qa_unit", "qa_samples", ["unit_id"])

    # ── llm_usage_logs ──────────────────────────────────────────────────────
    op.create_table(
        "llm_usage_logs",
        sa.Column("log_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("operation_type", sa.Text, nullable=False),  # embedding|extraction|generation|repair
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("book_id", UUID(as_uuid=True), nullable=True),
        sa.Column("video_id", UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_llm_user", "llm_usage_logs", ["user_id"])
    op.create_index("idx_llm_op", "llm_usage_logs", ["operation_type"])
    op.create_index("idx_llm_created", "llm_usage_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_usage_logs")
    op.drop_table("qa_samples")
    op.drop_table("unit_edits")
    op.drop_table("knowledge_units")
