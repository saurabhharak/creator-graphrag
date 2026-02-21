"""0007 — Video packages + versions + templates + webhooks + search_logs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── templates ──────────────────────────────────────────────────────────
    op.create_table(
        "templates",
        sa.Column("template_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("format", sa.Text, nullable=False),
        sa.Column("audience_level", sa.Text, nullable=True),
        sa.Column("required_sections", JSONB, nullable=False, server_default="[]"),
        sa.Column("scene_min", sa.Integer, nullable=False, server_default="5"),
        sa.Column("scene_max", sa.Integer, nullable=False, server_default="8"),
        sa.Column("pacing_constraints", JSONB, nullable=False, server_default="{}"),
        sa.Column("output_schema", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ── video_packages ─────────────────────────────────────────────────────
    op.create_table(
        "video_packages",
        sa.Column("video_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("format", sa.Text, nullable=False),
        sa.Column("audience_level", sa.Text, nullable=False),
        sa.Column("language_mode", sa.Text, nullable=False),
        sa.Column("tone", sa.Text, nullable=False),
        sa.Column("strict_citations", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("citation_repair_mode", sa.Text, nullable=False, server_default="label_interpretation"),
        sa.Column("template_id", UUID(as_uuid=True), sa.ForeignKey("templates.template_id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("outline_md", sa.Text, nullable=False),
        sa.Column("script_md", sa.Text, nullable=False),
        sa.Column("storyboard_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("visual_spec_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("citations_report_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("evidence_map_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("warnings_jsonb", JSONB, nullable=False, server_default="[]"),
        sa.Column("source_filters_jsonb", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_vp_created_by", "video_packages", ["created_by"])
    op.create_index("idx_vp_topic", "video_packages", ["topic"])
    op.create_index("idx_vp_created", "video_packages", ["created_at"])
    op.create_index("idx_vp_format_lang", "video_packages", ["format", "language_mode"])

    # ── video_package_versions (history) ───────────────────────────────────
    op.create_table(
        "video_package_versions",
        sa.Column("version_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("video_packages.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("snapshot_jsonb", JSONB, nullable=False),  # full package snapshot
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("video_id", "version_number", name="uq_vp_version"),
    )
    op.create_index("idx_vpv_video", "video_package_versions", ["video_id"])

    # ── webhooks ───────────────────────────────────────────────────────────
    op.create_table(
        "webhooks",
        sa.Column("webhook_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("events", JSONB, nullable=False),  # ["job.completed", ...]
        sa.Column("secret_token_hash", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_webhooks_user", "webhooks", ["user_id"])

    # ── search_logs (analytics) ────────────────────────────────────────────
    op.create_table(
        "search_logs",
        sa.Column("log_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("query_language", sa.Text, nullable=True),
        sa.Column("top_k", sa.Integer, nullable=True),
        sa.Column("result_count", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("filters_jsonb", JSONB, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_search_logs_user", "search_logs", ["user_id"])
    op.create_index("idx_search_logs_created", "search_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("search_logs")
    op.drop_table("webhooks")
    op.drop_table("video_package_versions")
    op.drop_table("video_packages")
    op.drop_table("templates")
