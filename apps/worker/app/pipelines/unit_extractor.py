"""Knowledge unit extraction logic for the ingestion pipeline.

Renders the Jinja2 prompt template, calls OpenAI gpt-4o, validates the
structured output with Pydantic, and returns DB-ready dicts.

Designed as pure functions with no Celery dependency so they can be
tested in isolation without a running worker.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Literal

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, model_validator

from app.infrastructure.llm_client import LlmResponse, call_openai
from app.pipelines.chunker import TextChunk

logger = structlog.get_logger(__name__)

# Confidence threshold below which units are flagged for human review
NEEDS_REVIEW_THRESHOLD = 0.65
# Safety cap: ignore more than this many units per chunk (runaway extraction)
MAX_UNITS_PER_CHUNK = 20

_PROMPT_DIR = Path(__file__).parent / "prompts"
_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(_PROMPT_DIR)),
    autoescape=select_autoescape(enabled_extensions=()),  # plain text template
)


# ── Pydantic validation models ────────────────────────────────────────────────


class EvidenceItem(BaseModel):
    book_id: str
    chapter_id: str
    page_start: int
    page_end: int
    snippet: str = Field(max_length=600)


class ExtractedUnit(BaseModel):
    type: Literal["claim", "definition", "process", "comparison"]
    language: str
    subject: str | None = None
    predicate: str | None = Field(None, max_length=50)
    object: str | None = None
    conditions: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)
    payload: dict[str, Any] = {}

    @model_validator(mode="after")
    def validate_spo(self) -> "ExtractedUnit":
        """Enforce subject+object requirement for claim/comparison types."""
        if self.type in ("claim", "comparison"):
            if not self.subject or not self.object:
                raise ValueError(
                    f"type={self.type} requires both subject and object"
                )
        return self


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_canonical_key(text: str) -> str:
    """Normalize text to a stable deduplication key.

    Steps:
    1. Unicode NFC normalization (Devanagari composition)
    2. Lowercase
    3. Strip characters that are not alphanumeric, spaces, or Devanagari
    4. Collapse whitespace

    Args:
        text: Raw subject or concept string.

    Returns:
        Normalized key suitable for Neo4j MERGE deduplication.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    # Keep: letters (all scripts), digits, spaces
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _render_prompt(
    *,
    language_detected: str,
    book_title: str,
    chapter_title: str,
    page_start: int,
    page_end: int,
    book_id: str,
    chunk_text: str,
) -> str:
    """Render the Jinja2 extraction prompt."""
    template = _JINJA_ENV.get_template("unit_extraction.jinja2")
    return template.render(
        language_detected=language_detected,
        book_title=book_title,
        chapter_title=chapter_title,
        page_start=page_start,
        page_end=page_end,
        book_id=book_id,
        chapter_id="",  # chapters table not populated yet (Phase 1 stub)
        chunk_text=chunk_text,
    )


def _parse_and_validate(raw_json: str) -> list[ExtractedUnit]:
    """Parse the LLM JSON response and validate each unit with Pydantic.

    Returns only valid units; logs and skips invalid ones.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("unit_extraction_json_parse_failed", error=str(e))
        return []

    raw_units = data.get("units", [])
    if not isinstance(raw_units, list):
        logger.warning("unit_extraction_unexpected_shape", data_keys=list(data.keys()))
        return []

    valid: list[ExtractedUnit] = []
    for i, raw in enumerate(raw_units[:MAX_UNITS_PER_CHUNK]):
        try:
            valid.append(ExtractedUnit.model_validate(raw))
        except Exception as exc:
            logger.debug("unit_validation_skipped", index=i, reason=str(exc))

    return valid


def _to_db_dict(unit: ExtractedUnit, source_book_id: str) -> dict:
    """Convert a validated ExtractedUnit to a dict ready for asyncpg INSERT."""
    status = (
        "needs_review" if unit.confidence < NEEDS_REVIEW_THRESHOLD else "extracted"
    )
    canonical = make_canonical_key(unit.subject) if unit.subject else None

    return {
        "unit_id": str(uuid.uuid4()),
        "source_book_id": source_book_id,
        "source_chunk_id": None,  # chunks table not populated in Phase 1
        "type": unit.type,
        "language_detected": unit.language,
        "language_confidence": None,
        "subject": unit.subject,
        "predicate": unit.predicate,
        "object": unit.object,
        "payload_jsonb": unit.payload,
        "confidence": unit.confidence,
        "status": status,
        "evidence_jsonb": [e.model_dump() for e in unit.evidence],
        "canonical_key": canonical,
    }


# ── Main extraction function ──────────────────────────────────────────────────


async def extract_units_for_chunk(
    chunk: TextChunk,
    *,
    book_id: str,
    book_title: str,
    chapter_title: str,
    openai_api_key: str,
    openai_base_url: str | None = None,
    extraction_model: str = "openai/gpt-4.1",
) -> tuple[list[dict], int, int]:
    """Extract knowledge units from a single text chunk via LLM.

    Args:
        chunk: TextChunk from the chunker (carries language, page, section metadata).
        book_id: Source book UUID string.
        book_title: Book title for the prompt.
        chapter_title: Current chapter/section title for the prompt.
        openai_api_key: OpenAI-compatible API key (Zenmux, OpenAI, etc.).
        openai_base_url: Optional custom base URL (e.g. https://zenmux.ai/api/v1).
        extraction_model: Model ID (default gpt-4.1).

    Returns:
        Tuple of (unit_dicts_for_db, input_tokens, output_tokens).
        unit_dicts_for_db is empty on parse/validation failure (graceful degradation).
        Token counts are 0 on failure.
    """
    system_prompt = _render_prompt(
        language_detected=chunk.language_detected or "en",
        book_title=book_title,
        chapter_title=chapter_title,
        page_start=chunk.page_start or 0,
        page_end=chunk.page_end or 0,
        book_id=book_id,
        chunk_text=chunk.text,
    )

    try:
        llm_resp: LlmResponse = await call_openai(
            system_prompt=system_prompt,
            user_prompt="Extract knowledge units from the text above.",
            model=extraction_model,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )
    except Exception as exc:
        logger.warning(
            "unit_extraction_llm_failed",
            book_id=book_id,
            error=str(exc),
            exc_info=True,
        )
        return [], 0, 0

    units = _parse_and_validate(llm_resp.content)
    db_dicts = [_to_db_dict(u, book_id) for u in units]

    logger.info(
        "chunk_units_extracted",
        book_id=book_id,
        chunk_hash=chunk.text_hash[:8],
        units_extracted=len(db_dicts),
        needs_review=sum(1 for d in db_dicts if d["status"] == "needs_review"),
    )

    return db_dicts, llm_resp.input_tokens, llm_resp.output_tokens
