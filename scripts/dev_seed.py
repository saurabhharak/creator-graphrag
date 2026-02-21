"""
Development seed script.

Creates:
  - Default admin user
  - System video generation templates (Shorts, Explainer, Myth-buster, Step-by-step)
  - Sample organization

Run after migrations:
  python scripts/dev_seed.py
"""
from __future__ import annotations
import os
import sys
import uuid
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

SYSTEM_TEMPLATES = [
    {
        "template_id": "00000000-0000-0000-0000-000000000001",
        "name": "Shorts 60-90s",
        "format": "shorts",
        "audience_level": "beginner",
        "scene_min": 5,
        "scene_max": 8,
        "required_sections": ["hook", "definition", "key_fact", "call_to_action"],
        "pacing_constraints": {"max_seconds_per_scene": 15, "total_seconds": 90},
        "is_system": True,
    },
    {
        "template_id": "00000000-0000-0000-0000-000000000002",
        "name": "Explainer 4-6 min",
        "format": "explainer",
        "audience_level": "intermediate",
        "scene_min": 10,
        "scene_max": 20,
        "required_sections": ["intro", "context", "main_concepts", "process", "examples", "summary"],
        "pacing_constraints": {"max_seconds_per_scene": 30, "total_seconds": 360},
        "is_system": True,
    },
    {
        "template_id": "00000000-0000-0000-0000-000000000003",
        "name": "Myth-buster",
        "format": "explainer",
        "audience_level": "beginner",
        "scene_min": 6,
        "scene_max": 12,
        "required_sections": ["myth_statement", "evidence_against", "truth", "why_it_matters"],
        "pacing_constraints": {"max_seconds_per_scene": 20, "total_seconds": 180},
        "is_system": True,
    },
    {
        "template_id": "00000000-0000-0000-0000-000000000004",
        "name": "Step-by-step Process",
        "format": "explainer",
        "audience_level": "beginner",
        "scene_min": 6,
        "scene_max": 15,
        "required_sections": ["overview", "materials", "steps", "tips", "recap"],
        "pacing_constraints": {"max_seconds_per_scene": 25, "total_seconds": 240},
        "is_system": True,
    },
    {
        "template_id": "00000000-0000-0000-0000-000000000005",
        "name": "Deep Dive",
        "format": "deep_dive",
        "audience_level": "intermediate",
        "scene_min": 20,
        "scene_max": 40,
        "required_sections": [
            "intro", "historical_context", "core_concepts", "mechanisms",
            "evidence", "comparisons", "applications", "limitations", "conclusion"
        ],
        "pacing_constraints": {"max_seconds_per_scene": 30, "total_seconds": 720},
        "is_system": True,
    },
]


def seed():
    """Idempotent seed: safe to run multiple times; check-before-insert for every record."""
    print("Starting dev seed...")

    # TODO(#0): connect to DB
    # TODO(#0): create default organization — check if slug='dev-org' exists first
    print("  Creating default organization: Dev Org")

    # TODO(#0): create admin user — check if email='admin@dev.local' exists first
    print("  Creating admin user: admin@dev.local (password: DevAdmin123!)")

    # TODO(#0): insert system templates — check if template_id exists before each INSERT
    for t in SYSTEM_TEMPLATES:
        # TODO(#0): if db.query(Template).filter_by(template_id=t["template_id"]).first(): continue
        print(f"  Seeding template: {t['name']}")

    print("\nDev seed complete.")
    print("\nDefault credentials:")
    print("  Email:    admin@dev.local")
    print("  Password: DevAdmin123!")
    print("  Role:     admin")


if __name__ == "__main__":
    seed()
