"""Neo4j graph build logic for the ingestion pipeline.

Takes knowledge units extracted from chunks and MERGEs them into the
Neo4j graph as typed Concept nodes and typed relationships.

Called synchronously (via asyncio.to_thread) from the async ingestion
pipeline to avoid blocking the event loop while Neo4j writes are in progress.

Node types:
  Concept — general concept node (subject or object of claim/definition/comparison)
  Process — procedural knowledge (source of process-type units)

Relationship types are derived from the unit's predicate field via
RELATION_TYPE_MAP with a RELATES_TO fallback.
"""
from __future__ import annotations

import re
import unicodedata

import neo4j
import structlog

logger = structlog.get_logger(__name__)

# Predicate → Neo4j relationship type
RELATION_TYPE_MAP: dict[str, str] = {
    "is": "IS_A",
    "is a": "IS_A",
    "defines": "IS_A",
    "defined as": "IS_A",
    "improves": "IMPROVES",
    "improve": "IMPROVES",
    "causes": "CAUSES",
    "cause": "CAUSES",
    "leads to": "CAUSES",
    "requires": "REQUIRES",
    "require": "REQUIRES",
    "needs": "REQUIRES",
    "produces": "PRODUCES",
    "produce": "PRODUCES",
    "results in": "PRODUCES",
    "inhibits": "INHIBITS",
    "inhibit": "INHIBITS",
    "reduces": "INHIBITS",
    "contains": "CONTAINS",
    "contain": "CONTAINS",
    "includes": "CONTAINS",
    "supports": "SUPPORTS",
    "support": "SUPPORTS",
    "promotes": "SUPPORTS",
    "compares": "COMPARED_TO",
    "compared to": "COMPARED_TO",
}
DEFAULT_RELATION_TYPE = "RELATES_TO"

# Language label property on Concept nodes
_LANG_PROP = {"en": "label_en", "mr": "label_mr", "hi": "label_hi"}
_DEFAULT_LANG_PROP = "label_en"


def _to_rel_type(predicate: str | None) -> str:
    """Normalize a free-text predicate to a Neo4j relationship type label."""
    if not predicate:
        return DEFAULT_RELATION_TYPE
    key = predicate.strip().lower()
    # Exact match
    if key in RELATION_TYPE_MAP:
        return RELATION_TYPE_MAP[key]
    # First-word match (e.g. "improves soil health" → IMPROVES)
    first_word = key.split()[0] if key.split() else key
    if first_word in RELATION_TYPE_MAP:
        return RELATION_TYPE_MAP[first_word]
    # Fallback: uppercase + replace spaces/hyphens with _
    normalized = re.sub(r"[\s\-]+", "_", key.upper())
    normalized = re.sub(r"[^\w]", "", normalized)
    return normalized[:50] or DEFAULT_RELATION_TYPE


def _lang_prop(language: str) -> str:
    """Return the Neo4j label property name for a language code."""
    return _LANG_PROP.get(language.lower(), _DEFAULT_LANG_PROP)


def build_graph_for_units(driver: neo4j.Driver, units: list[dict]) -> int:
    """MERGE Concept nodes and relationships for a batch of knowledge units.

    Skips rejected units and units without a canonical_key. Safe to call
    multiple times (idempotent via MERGE).

    Args:
        driver: An open synchronous neo4j.Driver.
        units: List of unit dicts as returned by unit_extractor._to_db_dict.

    Returns:
        Number of Concept nodes that were MERGED (created or matched).
    """
    merged_count = 0

    with driver.session() as session:
        for unit in units:
            if unit.get("status") == "rejected":
                continue

            unit_type = unit.get("type", "")
            lang = unit.get("language_detected", "en")
            lang_prop = _lang_prop(lang)
            book_id = unit.get("source_book_id", "")
            unit_id = unit.get("unit_id", "")
            confidence = float(unit.get("confidence", 0.5))
            evidence_count = len(unit.get("evidence_jsonb") or [])

            subject = unit.get("subject")
            subject_key = unit.get("canonical_key")
            obj = unit.get("object")
            predicate = unit.get("predicate")

            if unit_type == "process":
                # Process: create a single Concept + a Process node linked to it
                if not subject or not subject_key:
                    continue
                obj_key = _norm_key(subject + "_process")

                session.run(
                    f"""
                    MERGE (s:Concept {{canonical_key: $s_key}})
                    ON CREATE SET s.created_at = datetime()
                    SET s.{lang_prop} = $subject,
                        s.book_ids = CASE WHEN $book_id IN coalesce(s.book_ids, [])
                                     THEN s.book_ids
                                     ELSE coalesce(s.book_ids, []) + [$book_id] END
                    MERGE (p:Process {{id: $p_id}})
                    ON CREATE SET p.created_at = datetime(), p.book_id = $book_id
                    MERGE (s)-[r:DESCRIBES_PROCESS {{unit_id: $unit_id}}]->(p)
                    SET r.confidence = $confidence
                    """,
                    s_key=subject_key,
                    subject=subject,
                    p_id=obj_key,
                    book_id=book_id,
                    unit_id=unit_id,
                    confidence=confidence,
                )
                merged_count += 1

            elif unit_type == "definition" and subject and subject_key:
                # Definition: create/enrich the subject Concept node only
                session.run(
                    f"""
                    MERGE (s:Concept {{canonical_key: $s_key}})
                    ON CREATE SET s.created_at = datetime()
                    SET s.{lang_prop} = $subject,
                        s.book_ids = CASE WHEN $book_id IN coalesce(s.book_ids, [])
                                     THEN s.book_ids
                                     ELSE coalesce(s.book_ids, []) + [$book_id] END
                    """,
                    s_key=subject_key,
                    subject=subject,
                    book_id=book_id,
                )
                merged_count += 1

            elif unit_type in ("claim", "comparison") and subject and subject_key and obj:
                # Claim/comparison: two Concept nodes + a typed relationship
                obj_key = _norm_key(obj)
                rel_type = _to_rel_type(predicate)
                obj_lang_prop = lang_prop

                session.run(
                    f"""
                    MERGE (s:Concept {{canonical_key: $s_key}})
                    ON CREATE SET s.created_at = datetime()
                    SET s.{lang_prop} = $subject,
                        s.book_ids = CASE WHEN $book_id IN coalesce(s.book_ids, [])
                                     THEN s.book_ids
                                     ELSE coalesce(s.book_ids, []) + [$book_id] END
                    MERGE (o:Concept {{canonical_key: $o_key}})
                    ON CREATE SET o.created_at = datetime()
                    SET o.{obj_lang_prop} = $obj,
                        o.book_ids = CASE WHEN $book_id IN coalesce(o.book_ids, [])
                                     THEN o.book_ids
                                     ELSE coalesce(o.book_ids, []) + [$book_id] END
                    MERGE (s)-[r:{rel_type} {{unit_id: $unit_id}}]->(o)
                    SET r.confidence = $confidence, r.evidence_count = $ev_count
                    """,
                    s_key=subject_key,
                    subject=subject,
                    o_key=obj_key,
                    obj=obj,
                    book_id=book_id,
                    unit_id=unit_id,
                    confidence=confidence,
                    ev_count=evidence_count,
                )
                merged_count += 2  # subject + object nodes

    logger.info("graph_build_complete", units_processed=len(units), nodes_merged=merged_count)
    return merged_count


def _norm_key(text: str) -> str:
    """Canonical key for object/process nodes (same logic as unit_extractor)."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())
