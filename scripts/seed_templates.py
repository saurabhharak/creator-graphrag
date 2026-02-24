#!/usr/bin/env python3
"""Seed default system templates into the database.

Idempotent — skips any template whose name already exists.

Usage (from project root):
    python scripts/seed_templates.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

# Allow running from project root; load .env
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import asyncpg

DATABASE_URL = (
    os.environ.get(
        "DATABASE_URL",
        "postgresql://cgr_user:changeme@localhost:5432/creator_graphrag",
    )
    .replace("postgresql+asyncpg://", "postgresql://")
)

TEMPLATES = [
    {
        "name": "shorts_60s",
        "format": "shorts",
        "audience_level": "beginner",
        "scene_min": 5,
        "scene_max": 7,
        "required_sections": ["hook", "core_fact", "takeaway"],
        "pacing_constraints": {"max_words_per_scene": 50, "target_duration_sec": 60},
        "output_schema": {},
    },
    {
        "name": "explainer_5min",
        "format": "explainer",
        "audience_level": "intermediate",
        "scene_min": 8,
        "scene_max": 14,
        "required_sections": [
            "intro", "context", "main_concept",
            "how_it_works", "examples", "recap",
        ],
        "pacing_constraints": {"max_words_per_scene": 120, "target_duration_sec": 300},
        "output_schema": {},
    },
    {
        "name": "myth_buster",
        "format": "explainer",
        "audience_level": "beginner",
        "scene_min": 6,
        "scene_max": 10,
        "required_sections": [
            "myth_statement", "why_people_believe_it",
            "the_truth", "evidence", "takeaway",
        ],
        "pacing_constraints": {"max_words_per_scene": 100, "target_duration_sec": 240},
        "output_schema": {},
    },
    {
        "name": "step_by_step",
        "format": "explainer",
        "audience_level": "beginner",
        "scene_min": 6,
        "scene_max": 12,
        "required_sections": [
            "overview", "materials_needed",
            "step_1", "step_2", "step_3", "tips", "result",
        ],
        "pacing_constraints": {"max_words_per_scene": 80, "target_duration_sec": 270},
        "output_schema": {},
    },
]


async def main() -> None:
    conn: asyncpg.Connection = await asyncpg.connect(DATABASE_URL)
    try:
        inserted = 0
        skipped = 0
        for tmpl in TEMPLATES:
            existing = await conn.fetchval(
                "SELECT template_id FROM templates WHERE name = $1 AND deleted_at IS NULL",
                tmpl["name"],
            )
            if existing:
                print(f"  skip  {tmpl['name']} (already exists)")
                skipped += 1
                continue

            await conn.execute(
                """
                INSERT INTO templates (
                    template_id, name, format, audience_level,
                    required_sections, scene_min, scene_max,
                    pacing_constraints, output_schema, is_system
                ) VALUES (
                    $1::uuid, $2, $3, $4,
                    $5::jsonb, $6, $7,
                    $8::jsonb, $9::jsonb, true
                )
                """,
                str(uuid.uuid4()),
                tmpl["name"],
                tmpl["format"],
                tmpl["audience_level"],
                json.dumps(tmpl["required_sections"]),
                tmpl["scene_min"],
                tmpl["scene_max"],
                json.dumps(tmpl["pacing_constraints"]),
                json.dumps(tmpl["output_schema"]),
            )
            print(f"  insert {tmpl['name']}")
            inserted += 1

        print(f"\nDone: {inserted} inserted, {skipped} skipped.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
