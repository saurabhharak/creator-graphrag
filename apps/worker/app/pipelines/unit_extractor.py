"""Knowledge unit extraction logic for the ingestion pipeline.

Renders the Jinja2 prompt template, calls the LLM, validates the
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
# Safety cap: ignore more than this many units per chunk (runaway extraction).
# Raised from 20 → 35 for dense ICAR ITK inventory pages (Fix 13).
MAX_UNITS_PER_CHUNK = 35

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


# Fix 10: Extended type set for traditional/classical knowledge texts.
# New types:
#   observation  — seasonal/weather/astronomical patterns (Krishi-Parashara nakshatra timing)
#   practice     — specific farm operation with named inputs/outputs (Jeevamrit recipe)
#   principle    — overarching belief or guideline ("soil is a living organism")
#   prescription — if-then recommendation with quantities/timing
UnitType = Literal[
    "claim",
    "definition",
    "process",
    "comparison",
    "observation",
    "practice",
    "principle",
    "prescription",
]

# Fix 8: Domain type for typed Neo4j node labels alongside base :Concept.
# The LLM assigns domain_type based on the subject's role in agriculture.
# "general" → :Concept only (no extra label)
# Others   → :Concept:<DomainLabel>  (e.g. :Concept:Crop, :Concept:InputMaterial)
#
# Domain types and their semantics:
#   crop           — plant species, seed varieties, grains, vegetables, fruits
#   practice       — named farming operations and techniques (Jeevamrit, SRI)
#   input_material — soil amendments, fertilizers, cow dung, pesticides, biostimulants
#   season         — seasons, months, nakshatras, lunar phases, planting windows
#   region         — geographic areas, agro-climatic zones, soil zones
#   pest           — insect pests, plant diseases, weeds, pathogens
#   soil           — soil types, properties, horizons, texture classes
#   water          — water sources, rainfall patterns, irrigation methods
#   general        — everything else (abstract concepts, relationships, outcomes)
DomainType = Literal[
    "crop",
    "practice",
    "input_material",
    "season",
    "region",
    "pest",
    "soil",
    "water",
    "general",
]


class ExtractedUnit(BaseModel):
    type: UnitType
    language: str
    domain_type: DomainType = "general"  # Fix 8: typed Neo4j node label
    subject: str | None = None
    predicate: str | None = Field(None, max_length=50)
    object: str | None = None
    conditions: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)
    payload: dict[str, Any] = {}

    @model_validator(mode="after")
    def validate_spo(self) -> "ExtractedUnit":
        """Enforce subject+object requirement for claim/comparison/prescription types."""
        if self.type in ("claim", "comparison", "prescription"):
            if not self.subject or not self.object:
                raise ValueError(
                    f"type={self.type} requires both subject and object"
                )
        if self.type == "principle" and not self.subject:
            raise ValueError("type=principle requires subject (the principle statement)")
        if self.type in ("observation", "practice") and not self.subject:
            raise ValueError(f"type={self.type} requires at least subject")
        return self


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_canonical_key(text: str) -> str:
    """Normalize text to a stable deduplication key.

    Steps:
    1. Unicode NFC normalization (Devanagari composition)
    2. Lowercase
    3. Strip characters that are not alphanumeric, spaces, or Devanagari
    4. Collapse whitespace
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
        chapter_id="",  # chapters table not populated yet (TODO)
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


def _to_db_dict(unit: ExtractedUnit, source_book_id: str, chunk_id: str | None = None) -> dict:
    """Convert a validated ExtractedUnit to a dict ready for asyncpg INSERT.

    Args:
        unit: Validated ExtractedUnit from the LLM.
        source_book_id: UUID string of the source book.
        chunk_id: Fix 4 — Qdrant point ID (uuid5) of the source chunk, used
                  to build the evidence trail from unit → chunk → book.
    """
    status = (
        "needs_review" if unit.confidence < NEEDS_REVIEW_THRESHOLD else "extracted"
    )
    canonical = make_canonical_key(unit.subject) if unit.subject else None

    # Fix 8: inject domain_type into payload_jsonb so graph_builder can
    # create typed Neo4j nodes (:Concept:Crop, :Concept:Practice, etc.)
    # without requiring a separate DB column (payload_jsonb is already JSONB).
    payload_with_domain = {**unit.payload, "domain_type": unit.domain_type}

    return {
        "unit_id": str(uuid.uuid4()),
        "source_book_id": source_book_id,
        "source_chunk_id": chunk_id,   # Fix 4: was always None
        "type": unit.type,
        "language_detected": unit.language,
        "language_confidence": None,
        "subject": unit.subject,
        "predicate": unit.predicate,
        "object": unit.object,
        "payload_jsonb": payload_with_domain,
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
    chunk_id: str | None = None,  # Fix 4: Qdrant point ID for evidence trail
) -> tuple[list[dict], int, int]:
    """Extract knowledge units from a single text chunk via LLM.

    Args:
        chunk: TextChunk from the chunker (carries language, page, section metadata).
        book_id: Source book UUID string.
        book_title: Book title for the prompt (was previously hardcoded to "").
        chapter_title: Current chapter/section title for the prompt.
        openai_api_key: OpenAI-compatible API key (Zenmux, OpenAI, etc.).
        openai_base_url: Optional custom base URL.
        extraction_model: Model ID.
        chunk_id: Fix 4 — Qdrant point ID of this chunk for source_chunk_id FK.

    Returns:
        Tuple of (unit_dicts_for_db, input_tokens, output_tokens).
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
    # Fix 4: pass chunk_id so source_chunk_id FK is populated in DB
    db_dicts = [_to_db_dict(u, book_id, chunk_id) for u in units]

    logger.info(
        "chunk_units_extracted",
        book_id=book_id,
        chunk_hash=chunk.text_hash[:8],
        chunk_id=chunk_id,
        units_extracted=len(db_dicts),
        needs_review=sum(1 for d in db_dicts if d["status"] == "needs_review"),
    )

    return db_dicts, llm_resp.input_tokens, llm_resp.output_tokens
