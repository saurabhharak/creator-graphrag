"""0001 — Core enums for language, chunk, job, unit, video types.

Revision ID: 0001
Revises: 0000
Create Date: 2026-02-19
"""
from alembic import op

revision = "0001"
down_revision = "0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute("CREATE TYPE language_code AS ENUM ('mr','hi','en','mixed','unknown')")
    op.execute("CREATE TYPE source_type AS ENUM ('digital_text','ocr')")
    op.execute("CREATE TYPE chunk_type AS ENUM ('concept','process','evidence','general')")
    op.execute("CREATE TYPE source_format AS ENUM ('pdf_text','pdf_scanned','epub','ocr_output')")
    op.execute("CREATE TYPE job_status AS ENUM ('queued','running','failed','completed','canceled')")
    op.execute(
        "CREATE TYPE job_stage AS ENUM "
        "('upload','ocr','structure_extract','chunk','embed','unit_extract','graph_build','done')"
    )
    op.execute("CREATE TYPE upload_status AS ENUM ('pending','uploaded','verified','failed')")
    op.execute("CREATE TYPE unit_type AS ENUM ('definition','claim','process','comparison')")
    op.execute(
        "CREATE TYPE unit_status AS ENUM "
        "('extracted','needs_review','approved','rejected','conflicting')"
    )
    op.execute("CREATE TYPE video_format AS ENUM ('shorts','explainer','deep_dive')")
    op.execute("CREATE TYPE audience_level AS ENUM ('beginner','intermediate')")
    op.execute(
        "CREATE TYPE tone AS ENUM ('teacher','storyteller','myth_buster','step_by_step')"
    )
    op.execute(
        "CREATE TYPE citation_repair_mode AS ENUM "
        "('remove_paragraph','label_interpretation','fail_generation')"
    )
    op.execute(
        "CREATE TYPE permission_level AS ENUM ('read','edit')"
    )


def downgrade() -> None:
    for t in [
        "permission_level", "citation_repair_mode", "tone", "audience_level",
        "video_format", "unit_status", "unit_type", "upload_status",
        "job_stage", "job_status", "source_format", "chunk_type",
        "source_type", "language_code",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {t}")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
