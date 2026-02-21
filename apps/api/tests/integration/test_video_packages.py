"""Integration tests for /v1/video-packages and /v1/templates endpoints.

Real services used: PostgreSQL, Redis.

Video packages are seeded directly via asyncpg (bypassing the LLM pipeline) so
the test suite runs without an OpenAI/Zenmux connection.

A separate live-generation test (test_live_generate) calls gpt-4.1 via Zenmux
and is skipped when OPENAI_API_KEY is not set or Ollama is unreachable.

Test isolation: each fixture creates fresh records and deletes them in teardown,
so tests can run in any order without DB conflicts.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest

from app.core.config import settings
from tests.conftest import do_register


def _raw_db_url() -> str:
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


# ── DB helpers ────────────────────────────────────────────────────────────────

_INSERT_VIDEO_PACKAGE_SQL = """
    INSERT INTO video_packages (
        video_id, created_by, topic, format, audience_level, language_mode, tone,
        strict_citations, citation_repair_mode, version,
        outline_md, script_md,
        storyboard_jsonb, visual_spec_jsonb, citations_report_jsonb,
        evidence_map_jsonb, warnings_jsonb, source_filters_jsonb
    ) VALUES (
        $1::uuid, $2::uuid, $3, $4, $5, $6, $7,
        $8, $9, $10,
        $11, $12,
        $13::jsonb, $14::jsonb, $15::jsonb,
        $16::jsonb, $17::jsonb, $18::jsonb
    )
"""

_INSERT_VERSION_SQL = """
    INSERT INTO video_package_versions (version_id, video_id, version_number, snapshot_jsonb)
    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)
"""

_INSERT_TEMPLATE_SQL = """
    INSERT INTO templates (template_id, name, format, audience_level, scene_min, scene_max, is_system)
    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
"""


def _make_video_package(user_id: str) -> dict:
    vid = str(uuid.uuid4())
    storyboard = {"scenes": [
        {
            "scene_number": 1,
            "title": "Hook",
            "duration_sec": 20,
            "voiceover": "Humus is the dark organic matter in soil.",
            "on_screen_text": "What is humus?",
            "visual_description": "Close-up of rich dark soil",
            "animation_cues": ["zoom in"],
            "evidence_chunk_indices": [],
            "needs_citation": False,
        }
    ]}
    citations = {
        "citation_coverage": 1.0,
        "total_scenes": 1,
        "supported_scenes": 1,
        "books": [],
        "cited_chunk_ids": [],
    }
    evidence_map = {
        "paragraphs": [
            {
                "paragraph_id": "scene-1",
                "scene_number": 1,
                "script_text": "Humus is the dark organic matter in soil.",
                "evidence_refs": [],
            }
        ]
    }
    return {
        "video_id": vid,
        "user_id": user_id,
        "topic": "humus soil water retention",
        "format": "shorts",
        "audience_level": "beginner",
        "language_mode": "en",
        "tone": "teacher",
        "storyboard": storyboard,
        "citations": citations,
        "evidence_map": evidence_map,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
async def vp_context(client, auth_headers) -> AsyncGenerator[dict, None]:
    """Register a user and seed one video package + version into the DB."""
    email = f"vp_{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/v1/auth/register", json={
        "email": email, "password": "TestPass12345!", "display_name": "VPTestUser"
    })
    assert r.status_code == 201
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Resolve user_id from /me
    me = await client.get("/v1/auth/me", headers=headers)
    user_id = me.json()["user_id"]

    pkg = _make_video_package(user_id)
    vid = pkg["video_id"]
    ver_id = str(uuid.uuid4())

    conn = await asyncpg.connect(_raw_db_url())
    try:
        await conn.execute(
            _INSERT_VIDEO_PACKAGE_SQL,
            vid, user_id, pkg["topic"], pkg["format"],
            pkg["audience_level"], pkg["language_mode"], pkg["tone"],
            True, "label_interpretation", 1,
            "# Outline\n\n## Beat 1: Hook\n",
            "### Scene 1: Hook\nHumus is the dark organic matter in soil.\n",
            json.dumps(pkg["storyboard"]),
            json.dumps({"diagrams": [], "icon_suggestions": []}),
            json.dumps(pkg["citations"]),
            json.dumps(pkg["evidence_map"]),
            json.dumps([]),
            json.dumps({"book_ids": [], "prefer_languages": []}),
        )
        await conn.execute(
            _INSERT_VERSION_SQL,
            ver_id, vid, 1,
            json.dumps({"version": 1, "topic": pkg["topic"]}),
        )
    finally:
        await conn.close()

    yield {"video_id": vid, "user_id": user_id, "headers": headers, "pkg": pkg}

    conn = await asyncpg.connect(_raw_db_url())
    try:
        await conn.execute("DELETE FROM video_package_versions WHERE video_id = $1::uuid", vid)
        await conn.execute("DELETE FROM video_packages WHERE video_id = $1::uuid", vid)
        await conn.execute("DELETE FROM users WHERE user_id = $1::uuid", user_id)
    finally:
        await conn.close()


@pytest.fixture()
async def tmpl_context(client, auth_headers) -> AsyncGenerator[dict, None]:
    """Seed one template into the DB for read tests."""
    tmpl_id = str(uuid.uuid4())
    conn = await asyncpg.connect(_raw_db_url())
    try:
        await conn.execute(
            _INSERT_TEMPLATE_SQL,
            tmpl_id, "Test Shorts Template", "shorts", "beginner", 5, 8, False,
        )
    finally:
        await conn.close()

    yield {"template_id": tmpl_id}

    conn = await asyncpg.connect(_raw_db_url())
    try:
        await conn.execute("DELETE FROM templates WHERE template_id = $1::uuid", tmpl_id)
    finally:
        await conn.close()


# ── /v1/video-packages tests ──────────────────────────────────────────────────

class TestListVideoPackages:
    async def test_empty_list_for_new_user(self, client, auth_headers):
        r = await client.get("/v1/video-packages", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "total_count" in body
        assert isinstance(body["items"], list)

    async def test_list_returns_seeded_package(self, client, vp_context):
        r = await client.get("/v1/video-packages", headers=vp_context["headers"])
        assert r.status_code == 200
        items = r.json()["items"]
        ids = [i["video_id"] for i in items]
        assert vp_context["video_id"] in ids

    async def test_list_requires_auth(self, client):
        r = await client.get("/v1/video-packages")
        assert r.status_code == 401

    async def test_list_topic_filter(self, client, vp_context):
        r = await client.get(
            "/v1/video-packages",
            params={"topic": "humus"},
            headers=vp_context["headers"],
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(i["video_id"] == vp_context["video_id"] for i in items)

    async def test_list_format_filter_no_match(self, client, vp_context):
        r = await client.get(
            "/v1/video-packages",
            params={"format": "deep_dive"},
            headers=vp_context["headers"],
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert not any(i["video_id"] == vp_context["video_id"] for i in items)

    async def test_list_format_filter_match(self, client, vp_context):
        r = await client.get(
            "/v1/video-packages",
            params={"format": "shorts"},
            headers=vp_context["headers"],
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(i["video_id"] == vp_context["video_id"] for i in items)


class TestGetVideoPackage:
    async def test_get_full_package(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(f"/v1/video-packages/{vid}", headers=vp_context["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["video_id"] == vid
        assert "outline_md" in body
        assert "script_md" in body
        assert "storyboard" in body
        assert "visual_spec" in body
        assert "citations_report" in body
        assert "evidence_map" in body
        assert "warnings" in body

    async def test_get_not_found(self, client, vp_context):
        r = await client.get(
            f"/v1/video-packages/{uuid.uuid4()}",
            headers=vp_context["headers"],
        )
        assert r.status_code == 404

    async def test_get_requires_auth(self, client, vp_context):
        r = await client.get(f"/v1/video-packages/{vp_context['video_id']}")
        assert r.status_code == 401

    async def test_get_other_user_cannot_see(self, client, auth_headers, vp_context):
        # auth_headers belongs to a different user than vp_context
        r = await client.get(
            f"/v1/video-packages/{vp_context['video_id']}",
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestVideoPackageVersions:
    async def test_list_versions(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(f"/v1/video-packages/{vid}/versions", headers=vp_context["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["video_id"] == vid
        assert isinstance(body["versions"], list)
        assert len(body["versions"]) >= 1
        ver = body["versions"][0]
        assert "version_id" in ver
        assert "version_number" in ver

    async def test_get_specific_version(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(f"/v1/video-packages/{vid}/versions/1", headers=vp_context["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["version_number"] == 1
        assert "snapshot" in body

    async def test_get_nonexistent_version(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(f"/v1/video-packages/{vid}/versions/999", headers=vp_context["headers"])
        assert r.status_code == 404


class TestExportVideoPackage:
    async def test_export_json_inline(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(
            f"/v1/video-packages/{vid}/export",
            params={"format": "json"},
            headers=vp_context["headers"],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["video_id"] == vid
        assert "script_md" in body

    async def test_export_zip_not_implemented(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(
            f"/v1/video-packages/{vid}/export",
            params={"format": "zip"},
            headers=vp_context["headers"],
        )
        assert r.status_code == 501


class TestDeleteVideoPackage:
    async def test_delete_soft_deletes(self, client, auth_headers):
        """Create a package via DB, delete it, verify 404 afterwards."""
        me = await client.get("/v1/auth/me", headers=auth_headers)
        user_id = me.json()["user_id"]
        vid = str(uuid.uuid4())

        conn = await asyncpg.connect(_raw_db_url())
        try:
            await conn.execute(
                _INSERT_VIDEO_PACKAGE_SQL,
                vid, user_id, "delete test", "shorts", "beginner", "en", "teacher",
                True, "label_interpretation", 1,
                "# Outline", "# Script",
                json.dumps({"scenes": []}), json.dumps({}),
                json.dumps({}), json.dumps({}), json.dumps([]), json.dumps({}),
            )
        finally:
            await conn.close()

        r = await client.delete(f"/v1/video-packages/{vid}", headers=auth_headers)
        assert r.status_code == 204

        r2 = await client.get(f"/v1/video-packages/{vid}", headers=auth_headers)
        assert r2.status_code == 404

    async def test_delete_not_found(self, client, auth_headers):
        r = await client.delete(
            f"/v1/video-packages/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestEvidenceMap:
    async def test_get_evidence_map(self, client, vp_context):
        vid = vp_context["video_id"]
        r = await client.get(f"/v1/evidence/map/{vid}", headers=vp_context["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["video_id"] == vid
        assert isinstance(body["paragraphs"], list)


# ── /v1/templates tests ───────────────────────────────────────────────────────

class TestListTemplates:
    async def test_list_returns_200(self, client, auth_headers):
        r = await client.get("/v1/templates", headers=auth_headers)
        assert r.status_code == 200
        assert "templates" in r.json()

    async def test_list_contains_seeded_template(self, client, auth_headers, tmpl_context):
        r = await client.get("/v1/templates", headers=auth_headers)
        assert r.status_code == 200
        ids = [t["template_id"] for t in r.json()["templates"]]
        assert tmpl_context["template_id"] in ids

    async def test_list_requires_auth(self, client):
        r = await client.get("/v1/templates")
        assert r.status_code == 401


class TestGetTemplate:
    async def test_get_template(self, client, auth_headers, tmpl_context):
        tid = tmpl_context["template_id"]
        r = await client.get(f"/v1/templates/{tid}", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["template_id"] == tid
        assert body["format"] == "shorts"

    async def test_get_not_found(self, client, auth_headers):
        r = await client.get(f"/v1/templates/{uuid.uuid4()}", headers=auth_headers)
        assert r.status_code == 404


class TestCreateTemplate:
    async def test_create_requires_admin(self, client, auth_headers):
        """Regular users cannot create templates."""
        r = await client.post("/v1/templates", json={
            "name": "Test Template",
            "format": "shorts",
            "audience_level": "beginner",
        }, headers=auth_headers)
        assert r.status_code == 403

    async def test_generate_request_validation(self, client, auth_headers):
        """Invalid format is rejected with 422."""
        r = await client.post("/v1/video-packages:generate", json={
            "topic": "compost",
            "format": "invalid_format",
            "audience_level": "beginner",
            "language_mode": "en",
            "tone": "teacher",
        }, headers=auth_headers)
        assert r.status_code == 422

    async def test_generate_requires_auth(self, client):
        r = await client.post("/v1/video-packages:generate", json={
            "topic": "compost",
            "format": "shorts",
            "audience_level": "beginner",
            "language_mode": "en",
            "tone": "teacher",
        })
        assert r.status_code == 401


# ── Live generation test (skipped when services unavailable) ──────────────────

def _zenmux_key() -> str | None:
    try:
        from app.core.config import settings
        return settings.OPENAI_API_KEY
    except Exception:
        return None


@pytest.mark.skipif(not _zenmux_key(), reason="OPENAI_API_KEY not configured")
class TestLiveGenerate:
    async def test_generate_english_shorts(self, client, auth_headers):
        """End-to-end: embed topic → Qdrant → LLM → saved to DB → response."""
        r = await client.post("/v1/video-packages:generate", json={
            "topic": "humus soil water retention organic farming",
            "format": "shorts",
            "audience_level": "beginner",
            "language_mode": "en",
            "tone": "teacher",
            "scene_constraints": {"min_scenes": 5, "max_scenes": 7},
        }, headers=auth_headers, timeout=120.0)

        if r.status_code == 503:
            pytest.skip("Embedding service (Ollama) unavailable")

        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text[:300]}"
        body = r.json()

        # Required fields
        assert "video_id" in body
        assert "outline_md" in body
        assert "script_md" in body
        assert "storyboard" in body
        assert "citations_report" in body
        assert "evidence_map" in body
        assert "warnings" in body

        # Storyboard has scenes
        scenes = body["storyboard"].get("scenes", [])
        assert len(scenes) >= 1, "Expected at least 1 scene"

        # Citation coverage present
        cov = body["citations_report"].get("citation_coverage")
        assert cov is not None

        # Can retrieve saved package
        vid = body["video_id"]
        r2 = await client.get(f"/v1/video-packages/{vid}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["video_id"] == vid

        # Cleanup
        r3 = await client.delete(f"/v1/video-packages/{vid}", headers=auth_headers)
        assert r3.status_code == 204
