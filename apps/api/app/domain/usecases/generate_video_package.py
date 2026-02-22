"""GenerateVideoPackageUsecase — orchestrates evidence retrieval + LLM generation + DB save.

Pipeline:
  1. Sanitize topic (injection guard).
  2. Retrieve evidence chunks from Qdrant (embed topic → ANN search with optional book filters).
  3. Render video_script.jinja2 prompt and call LLM.
  4. Parse + validate JSON response.
  5. Apply citation enforcement policy.
  6. Build evidence_map, citations_report, visual_spec, script_md.
  7. Save VideoPackage + version snapshot to DB.
  8. Log LLM token usage.
  9. Return serialized package dict.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from uuid import UUID

import httpx
import jinja2
import openai
import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.policies.citation_enforcement import (
    CitationEnforcementError,
    CitationEnforcementPolicy,
    CitationRepairMode,
    Paragraph,
)
from app.infrastructure.db.repositories.video_package_repository import (
    VideoPackageRepository,
)
from app.infrastructure.llm.client import call_llm
from app.infrastructure.vector.qdrant import vector_search

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_INJECTION_RE = re.compile(
    r"^\s*(ignore\s+(previous|all)\s+instructions?|system\s*:)", re.IGNORECASE
)

FORMAT_DESCRIPTIONS = {
    "shorts": "YouTube Shorts: 60-90 second vertical video, punchy hook + 2-3 key points + strong CTA",
    "explainer": "Explainer video: 4-6 minutes, educational depth with examples and visual aids",
    "deep_dive": "Deep-dive documentary: 10+ minutes, comprehensive coverage with multiple perspectives",
}

FORMAT_SCENE_RANGES = {
    "shorts": (5, 8),
    "explainer": (8, 15),
    "deep_dive": (15, 30),
}

TONE_DESCRIPTIONS = {
    "teacher": "Educational and patient — clear explanations, analogies, step-by-step",
    "storyteller": "Narrative-driven — engaging stories and examples drawn from the source texts",
    "myth_buster": "Evidence-based challenge to common misconceptions — critical and persuasive",
    "step_by_step": "Practical and actionable — numbered steps, clear instructions",
}

LANGUAGE_DESCRIPTIONS = {
    "mr": "Pure Marathi (मराठी) — all narration in Marathi",
    "hi": "Pure Hindi (हिंदी) — all narration in Hindi",
    "en": "English — all narration in English",
    "hinglish": "Hinglish — Hindi sentence structure with English technical terms",
    "mr_plus_en_terms": "Marathi sentences with English for scientific/technical nouns",
    "hi_plus_en_terms": "Hindi sentences with English for scientific/technical nouns",
}

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "infrastructure" / "llm" / "prompts" / "generation"
_SYSTEM_PROMPTS_DIR = _PROMPTS_DIR / "system"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_PROMPTS_DIR)),
    autoescape=False,
    undefined=jinja2.StrictUndefined,
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a top-tier YouTube scriptwriter who creates engaging, non-robotic "
    "video scripts. Write as spoken language — conversational, vivid, hook-driven. "
    "You MUST return valid JSON only. No markdown, no explanation outside JSON."
)


def _load_system_prompt(format: str) -> str:
    """Load format-specific system prompt from prompts/generation/system/{format}.md."""
    prompt_file = _SYSTEM_PROMPTS_DIR / f"{format}.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()
    logger.warning("system_prompt_not_found", format=format, path=str(prompt_file))
    return _DEFAULT_SYSTEM_PROMPT


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sanitize_topic(topic: str) -> str:
    cleaned = _INJECTION_RE.sub("", topic).strip()
    cleaned = "".join(c for c in cleaned if c >= " " or c in "\t\n")
    return cleaned[:500]


async def _embed_query(query: str) -> list[float]:
    """Embed a query string. Uses EMBEDDING_PROVIDER setting (ollama or huggingface)."""
    if settings.EMBEDDING_PROVIDER == "huggingface":
        return await _embed_via_huggingface(query)
    return await _embed_via_ollama(query)


async def _embed_via_ollama(query: str) -> list[float]:
    """Embed via local Ollama instance."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                f"{settings.OLLAMA_ENDPOINT}/api/embeddings",
                json={"model": settings.EMBEDDING_MODEL, "prompt": query},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Ollama timed out (model may be loading): {exc}",
            )
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ollama unavailable: {exc}",
            )


async def _embed_via_huggingface(query: str) -> list[float]:
    """Embed via HuggingFace Inference API (Scaleway endpoint)."""
    if not settings.HF_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HF_TOKEN not configured. Set HF_TOKEN in .env to use HuggingFace embeddings.",
        )
    # Convert Ollama model name to HF format: "qwen3-embedding:8b" → "qwen3-embedding-8b"
    hf_model = settings.EMBEDDING_MODEL.replace(":", "-")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                settings.HF_EMBEDDING_URL,
                headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
                json={"input": query, "model": hf_model},
            )
            resp.raise_for_status()
            data = resp.json()
            # HF returns {"data": [{"embedding": [...]}]} (OpenAI-compatible)
            if isinstance(data, dict) and "data" in data:
                return data["data"][0]["embedding"]
            # Or direct list format
            if isinstance(data, list) and len(data) > 0:
                return data[0] if isinstance(data[0], list) else data
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Unexpected HF embedding response format: {str(data)[:200]}",
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"HuggingFace embedding timed out: {exc}",
            )
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"HuggingFace embedding error: {exc}",
            )


def _resolve_scene_range(
    format: str, min_scenes: int, max_scenes: int
) -> tuple[int, int]:
    """Use format defaults but honour explicit user overrides if within format bounds."""
    default_min, default_max = FORMAT_SCENE_RANGES.get(format, (5, 10))
    return max(min_scenes, default_min), min(max_scenes, default_max)


def _build_script_md(scenes: list[dict]) -> str:
    parts = []
    for scene in scenes:
        parts.append(f"### Scene {scene['scene_number']}: {scene.get('title', '')}")
        parts.append(scene.get("voiceover", ""))
        parts.append("")
    return "\n".join(parts)


def _build_evidence_map(scenes: list[dict], evidence_chunks: list[dict]) -> dict:
    """Map scene numbers → chunk IDs based on evidence_chunk_indices."""
    paragraphs = []
    for scene in scenes:
        indices = scene.get("evidence_chunk_indices") or []
        refs = []
        for idx in indices:
            if 0 <= idx < len(evidence_chunks):
                chunk = evidence_chunks[idx]
                refs.append({
                    "chunk_id": chunk["id"],
                    "book_id": chunk["payload"].get("book_id", ""),
                    "book_title": chunk["payload"].get("book_title", ""),
                    "page_start": chunk["payload"].get("page_start"),
                    "page_end": chunk["payload"].get("page_end"),
                    "snippet": (chunk["payload"].get("text") or "")[:300],
                })
        paragraphs.append({
            "paragraph_id": f"scene-{scene['scene_number']}",
            "scene_number": scene["scene_number"],
            "script_text": scene.get("voiceover", ""),
            "evidence_refs": refs,
        })
    return {"paragraphs": paragraphs}


def _build_citations_report(evidence_map: dict, evidence_chunks: list[dict]) -> dict:
    """Summarise which books/chunks contributed evidence."""
    books_used: dict[str, dict] = {}
    cited_chunk_ids: set[str] = set()

    for para in evidence_map.get("paragraphs", []):
        for ref in para.get("evidence_refs", []):
            chunk_id = ref["chunk_id"]
            book_id = ref["book_id"]
            cited_chunk_ids.add(chunk_id)
            if book_id not in books_used:
                books_used[book_id] = {
                    "book_id": book_id,
                    "book_title": ref.get("book_title", ""),
                    "cited_chunk_count": 0,
                    "pages_cited": [],
                }
            books_used[book_id]["cited_chunk_count"] += 1
            if ref.get("page_start"):
                books_used[book_id]["pages_cited"].append(ref["page_start"])

    total_scenes = len(evidence_map.get("paragraphs", []))
    supported = sum(
        1 for p in evidence_map.get("paragraphs", []) if p.get("evidence_refs")
    )

    return {
        "citation_coverage": round(supported / total_scenes, 3) if total_scenes else 1.0,
        "total_scenes": total_scenes,
        "supported_scenes": supported,
        "books": list(books_used.values()),
        "cited_chunk_ids": list(cited_chunk_ids),
    }


def _apply_needs_citation_policy(
    scenes: list[dict],
    repair_mode: str,
    warnings: list[str],
) -> list[dict]:
    """Apply citation_repair_mode to scenes that are missing evidence."""
    result = []
    mode = CitationRepairMode(repair_mode)

    for scene in scenes:
        if not scene.get("needs_citation") or scene.get("evidence_chunk_indices"):
            result.append(scene)
            continue

        if mode == CitationRepairMode.REMOVE_PARAGRAPH:
            warnings.append(
                f"scene:{scene['scene_number']} removed — no evidence found"
            )
        elif mode == CitationRepairMode.LABEL_INTERPRETATION:
            scene = dict(scene)
            scene["voiceover"] = f"[Interpretation] {scene.get('voiceover', '')}"
            warnings.append(
                f"scene:{scene['scene_number']} labeled [Interpretation] — no evidence"
            )
            result.append(scene)
        elif mode == CitationRepairMode.FAIL_GENERATION:
            raise CitationEnforcementError(
                f"Scene {scene['scene_number']} has no evidence and mode=fail_generation"
            )

    return result


# ── Main usecase ──────────────────────────────────────────────────────────────

class GenerateVideoPackageUsecase:
    """Orchestrate the full video package generation pipeline."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = VideoPackageRepository(db)

    async def execute(
        self,
        user_id: UUID,
        topic: str,
        format: str,
        audience_level: str,
        language_mode: str,
        tone: str,
        strict_citations: bool,
        citation_repair_mode: str | None,
        min_scenes: int,
        max_scenes: int,
        book_ids: list[str],
        prefer_languages: list[str],
        template_id: str | None,
    ) -> dict:
        # ── 1. Sanitize topic ─────────────────────────────────────────────
        topic_clean = _sanitize_topic(topic)
        if not topic_clean:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Topic is empty after sanitization.",
            )

        repair_mode = citation_repair_mode or settings.CITATION_REPAIR_MODE

        # ── 2. Evidence retrieval ─────────────────────────────────────────
        query_vector = await _embed_query(topic_clean)
        evidence_chunks = await vector_search(
            collection=settings.QDRANT_COLLECTION_NAME,
            query_vector=query_vector,
            top_k=20,
            book_ids=book_ids or None,
            languages=prefer_languages or None,
        )

        if not evidence_chunks and book_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No relevant content found in the specified books for this topic.",
            )

        # ── 3. Render prompt + call LLM ────────────────────────────────────
        scene_min, scene_max = _resolve_scene_range(format, min_scenes, max_scenes)

        # Build evidence context for the prompt
        evidence_for_prompt = [
            {
                "id": c["id"],
                "book_title": c["payload"].get("book_title", "Unknown"),
                "chapter_title": c["payload"].get("chapter_title") or c["payload"].get("section_title"),
                "page_start": c["payload"].get("page_start", 0),
                "page_end": c["payload"].get("page_end", 0),
                "language_detected": c["payload"].get("language_detected", ""),
                "text": (c["payload"].get("text") or "")[:500],
            }
            for c in evidence_chunks
        ]

        template = _jinja_env.get_template("video_script.jinja2")
        user_prompt = template.render(
            topic=topic_clean,
            format=format,
            format_description=FORMAT_DESCRIPTIONS.get(format, format),
            tone=tone,
            tone_description=TONE_DESCRIPTIONS.get(tone, tone),
            audience_level=audience_level,
            language_mode=language_mode,
            language_description=LANGUAGE_DESCRIPTIONS.get(language_mode, language_mode),
            scene_min=scene_min,
            scene_max=scene_max,
            evidence_chunks=evidence_for_prompt,
            evidence_count=len(evidence_for_prompt),
        )

        system_prompt = _load_system_prompt(format)

        try:
            llm_resp = await call_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=settings.LLM_GENERATION_MODEL,
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                temperature=0.7,
                max_tokens=8000,
            )
        except openai.AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"LLM authentication failed: {exc}",
            )
        except openai.APIError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"LLM service error: {exc}",
            )

        # ── 4. Parse LLM output ────────────────────────────────────────────
        try:
            raw = json.loads(llm_resp.content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "llm_json_parse_error",
                error=str(exc),
                content_preview=llm_resp.content[:200],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="LLM returned invalid JSON. Please retry.",
            )

        scenes: list[dict] = raw.get("scenes") or []
        outline_md: str = raw.get("outline_md") or f"# {topic_clean}\n"
        visual_spec_raw: dict = raw.get("visual_spec") or {}
        llm_warnings: list[str] = raw.get("warnings") or []

        if not scenes:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="LLM returned no scenes. Please retry.",
            )

        # ── 5. Citation enforcement ────────────────────────────────────────
        try:
            scenes = _apply_needs_citation_policy(scenes, repair_mode, llm_warnings)
        except CitationEnforcementError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

        # ── 6. Build package components ────────────────────────────────────
        script_md = _build_script_md(scenes)
        storyboard = {"scenes": scenes}
        visual_spec = {
            "diagrams": visual_spec_raw.get("diagram_suggestions") or [],
            "icon_suggestions": visual_spec_raw.get("icon_suggestions") or [],
        }
        evidence_map = _build_evidence_map(scenes, evidence_chunks)
        citations_report = _build_citations_report(evidence_map, evidence_chunks)
        source_filters = {
            "book_ids": book_ids,
            "prefer_languages": prefer_languages,
        }

        # ── 7. Save to DB ──────────────────────────────────────────────────
        pkg = await self.repo.create(
            created_by=user_id,
            topic=topic_clean,
            format=format,
            audience_level=audience_level,
            language_mode=language_mode,
            tone=tone,
            outline_md=outline_md,
            script_md=script_md,
            storyboard=storyboard,
            visual_spec=visual_spec,
            citations_report=citations_report,
            evidence_map=evidence_map,
            warnings=llm_warnings,
            source_filters=source_filters,
            strict_citations=strict_citations,
            citation_repair_mode=repair_mode,
        )

        # Initial version snapshot
        await self.repo.create_version_snapshot(
            video_id=pkg.video_id,
            version_number=1,
            snapshot=_make_snapshot(pkg, outline_md, script_md, storyboard, visual_spec,
                                    citations_report, evidence_map, llm_warnings),
        )

        # ── 8. Log LLM usage ──────────────────────────────────────────────
        logger.info(
            "video_package_generated",
            video_id=str(pkg.video_id),
            topic=topic_clean[:60],
            scenes=len(scenes),
            input_tokens=llm_resp.input_tokens,
            output_tokens=llm_resp.output_tokens,
            model=llm_resp.model_id,
        )

        await self.db.commit()

        # ── 9. Return response dict ────────────────────────────────────────
        return _format_response(
            pkg, outline_md, script_md, storyboard, visual_spec,
            citations_report, evidence_map, llm_warnings,
        )


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _make_snapshot(pkg, outline_md, script_md, storyboard, visual_spec,
                   citations_report, evidence_map, warnings) -> dict:
    return {
        "video_id": str(pkg.video_id),
        "version": pkg.version,
        "topic": pkg.topic,
        "format": pkg.format,
        "audience_level": pkg.audience_level,
        "language_mode": pkg.language_mode,
        "tone": pkg.tone,
        "outline_md": outline_md,
        "script_md": script_md,
        "storyboard": storyboard,
        "visual_spec": visual_spec,
        "citations_report": citations_report,
        "evidence_map": evidence_map,
        "warnings": warnings,
    }


def _format_response(pkg, outline_md, script_md, storyboard, visual_spec,
                     citations_report, evidence_map, warnings) -> dict:
    return {
        "video_id": str(pkg.video_id),
        "version": pkg.version,
        "topic": pkg.topic,
        "format": pkg.format,
        "audience_level": pkg.audience_level,
        "language_mode": pkg.language_mode,
        "tone": pkg.tone,
        "outline_md": outline_md,
        "script_md": script_md,
        "storyboard": storyboard,
        "visual_spec": visual_spec,
        "citations_report": citations_report,
        "evidence_map": evidence_map,
        "warnings": warnings,
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
    }
