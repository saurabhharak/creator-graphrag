"""Neo4j graph build logic for the ingestion pipeline.

Takes knowledge units extracted from chunks and MERGEs them into the
Neo4j graph as typed Concept nodes and typed relationships.

Called synchronously (via asyncio.to_thread) from the async ingestion
pipeline to avoid blocking the event loop while Neo4j writes are in progress.

Fix 6 — UNWIND batch writes:
  Instead of one session.run() per unit (N round-trips), units are grouped
  by type and written in batched UNWIND calls regardless of unit count:
    1. claims + comparisons  → Concept pairs + typed relationships
    2. definitions           → single Concept node enrichment
    3. processes             → Concept + Process node pair + Step sequence

Fix 8 — Typed domain node labels:
  Concepts get a typed Neo4j label alongside :Concept for domain-specific
  queries. The base :Concept label is always applied; domain labels are additive.
  domain_type is read from unit["payload_jsonb"]["domain_type"] (stored there
  by unit_extractor._to_db_dict — no additional DB column required).

  domain_type → Neo4j label:
    crop           → :Crop            (plant species, varieties, grains)
    practice       → :Practice        (named farming techniques, Jeevamrit, SRI)
    input_material → :InputMaterial   (cow dung, jaggery, fertilizers, pesticides)
    season         → :Season          (seasons, nakshatras, lunar phases)
    region         → :Region          (geographic areas, agro-climatic zones)
    pest           → :Pest            (insects, diseases, weeds, pathogens)
    soil           → :Soil            (soil types, texture classes, properties)
    water          → :Water           (water sources, rainfall, irrigation)
    general        → (no extra label) — generic :Concept only

  Example Cypher generated for crop subject:
    MERGE (s:Concept:Crop {canonical_key: "rice"})

Fix 14 — Process step sequences:
  Process and practice units with payload.steps are expanded into ordered
  :Step nodes linked to the :Process node:
    (Concept)-[:DESCRIBES_PROCESS]->(Process)-[:STEP {order:0}]->(Step)
                                              -[:STEP {order:1}]->(Step) ...
  Uses Cypher FOREACH with a list comprehension over the steps array.
  FOREACH is safe with empty step lists — it simply does nothing.

Node types:
  Concept       — general concept node (subject or object of any unit type)
  <DomainLabel> — optional additional label on Concept nodes (Fix 8)
  Process       — procedural knowledge node (source of process/practice units)
  Step          — individual ordered step within a Process

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
    "recommends": "RECOMMENDS",
    "recommend": "RECOMMENDS",
    "prescribes": "RECOMMENDS",
    "used for": "USED_FOR",
    "applied to": "APPLIED_TO",
    "derived from": "DERIVED_FROM",
    "part of": "PART_OF",
    "associated with": "ASSOCIATED_WITH",
}
DEFAULT_RELATION_TYPE = "RELATES_TO"

# Fix 8: Domain type → extra Neo4j node label.
# "" means no extra label (subject gets only :Concept).
# All non-empty values are added as a second label: :Concept:<DomainLabel>.
#
# To add a new domain (e.g. "livestock"):
#   1. Add "livestock": "Livestock" to _DOMAIN_LABEL_MAP below
#   2. Add "livestock" to DomainType Literal in unit_extractor.py
#   3. Add a row in the domain_type guidance table in unit_extraction.jinja2
_DOMAIN_LABEL_MAP: dict[str, str] = {
    "crop":           "Crop",           # plant species, seed varieties, grains
    "practice":       "Practice",       # named farming operations and techniques
    "input_material": "InputMaterial",  # soil amendments, fertilizers, cow dung
    "season":         "Season",         # seasons, months, nakshatras, lunar phases
    "region":         "Region",         # geographic areas, agro-climatic zones
    "pest":           "Pest",           # insect pests, plant diseases, weeds
    "soil":           "Soil",           # soil types, texture classes, properties
    "water":          "Water",          # water sources, rainfall, irrigation
    "general":        "",               # no extra label — generic :Concept only
}
# Pre-approved domain label strings for Cypher interpolation safety
_SAFE_DOMAIN_LABELS = frozenset(v for v in _DOMAIN_LABEL_MAP.values() if v)

# Language label property on Concept nodes.
# Maps the 2-3 char language code (from chunker._detect_language) to the
# Neo4j property name used to store the concept label in that language.
#
# Currently supported:
#   "en" → label_en   (English)
#   "mr" → label_mr   (Marathi)
#   "hi" → label_hi   (Hindi)
#   "sa" → label_sa   (Sanskrit)
#   other → label_en  (fallback for unimplemented languages)
#
# To add a new language (e.g. Bengali "bn"):
#   1. Add "bn": "label_bn" to _LANG_PROP below
#   2. Ensure chunker._detect_language() can return "bn"
#   3. Add a Qdrant payload index for language_detected = "bn" if needed
#   4. Add a prefix template in embedder.build_context_prefix() for "bn"
#
# Full language code reference (Sarvam AI BCP-47, all require -IN suffix):
#   hi-IN  mr-IN  bn-IN  ta-IN  te-IN  gu-IN  kn-IN  ml-IN  as-IN  ur-IN
#   sa-IN  ne-IN  doi-IN brx-IN pa-IN  od-IN  kok-IN mai-IN sd-IN  ks-IN
#   mni-IN sat-IN en-IN
#   (Source: docs.sarvam.ai/api-reference-docs/getting-started/models/sarvam-vision)
_LANG_PROP = {
    "en":  "label_en",   # English
    "mr":  "label_mr",   # Marathi
    "hi":  "label_hi",   # Hindi
    "sa":  "label_sa",   # Sanskrit (classical texts)
    # Add new languages here ↓
    # "bn":  "label_bn",   # Bengali
    # "ta":  "label_ta",   # Tamil
    # "te":  "label_te",   # Telugu
    # "gu":  "label_gu",   # Gujarati
    # "kn":  "label_kn",   # Kannada
    # "ml":  "label_ml",   # Malayalam
    # "pa":  "label_pa",   # Punjabi
    # "od":  "label_od",   # Odia
    # "ur":  "label_ur",   # Urdu
    # "ne":  "label_ne",   # Nepali
}
_DEFAULT_LANG_PROP = "label_en"

# Relationship types that are safe to use directly in Cypher (pre-approved list)
_SAFE_REL_TYPES = frozenset(RELATION_TYPE_MAP.values()) | {DEFAULT_RELATION_TYPE}


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
    # Normalize unknown predicate — uppercase, spaces/hyphens → underscores, strip non-word
    normalized = re.sub(r"[\s\-]+", "_", key.upper())
    normalized = re.sub(r"[^\w]", "", normalized)
    # Only use if it passes basic safety checks (non-empty, starts with letter)
    if normalized and normalized[0].isalpha():
        return normalized[:50]
    return DEFAULT_RELATION_TYPE


def _lang_prop(language: str) -> str:
    """Return the Neo4j label property name for a language code."""
    return _LANG_PROP.get(language.lower(), _DEFAULT_LANG_PROP)


def _norm_key(text: str) -> str:
    """Canonical key for object/process nodes (same logic as unit_extractor)."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _domain_label(domain_type: str | None) -> str:
    """Return the extra Neo4j node label for a domain type (Fix 8).

    Returns '' for 'general' or unknown types → subject gets only :Concept.
    Returns a validated label name for known domain types → :Concept:<Label>.

    The return value is always safe to interpolate into Cypher because it
    is validated against _SAFE_DOMAIN_LABELS before being returned.
    """
    if not domain_type:
        return ""
    label = _DOMAIN_LABEL_MAP.get(domain_type.lower(), "")
    # Double-check: ensure the label is in the approved set
    if label and label not in _SAFE_DOMAIN_LABELS:
        return ""
    return label


def _subject_label(domain_lbl: str) -> str:
    """Return the Cypher label expression for a subject node.

    domain_lbl=""         → "Concept"
    domain_lbl="Crop"     → "Concept:Crop"
    """
    return f"Concept:{domain_lbl}" if domain_lbl else "Concept"


def build_graph_for_units(driver: neo4j.Driver, units: list[dict]) -> int:
    """MERGE Concept nodes and relationships for a batch of knowledge units.

    Fix 6: Uses UNWIND to batch all writes into grouped session.run() calls
    instead of N individual calls. Calls are grouped by (rel_type, domain_lbl,
    lang_prop) to avoid Cypher label/type parameterization limits.

    Fix 8: Creates typed subject nodes (:Concept:Crop, :Concept:Practice, etc.)
    based on domain_type stored in unit["payload_jsonb"]["domain_type"].

    Fix 14: For process/practice units, expands payload.steps into ordered
    :Step nodes linked to the :Process node via FOREACH (safe with empty lists).

    Skips rejected units and units without a canonical_key. Safe to call
    multiple times (idempotent via MERGE).

    Args:
        driver: An open synchronous neo4j.Driver.
        units: List of unit dicts as returned by unit_extractor._to_db_dict.

    Returns:
        Number of Concept nodes that were targeted for MERGE.
    """
    claim_params: list[dict] = []
    definition_params: list[dict] = []
    process_params: list[dict] = []

    for unit in units:
        if unit.get("status") == "rejected":
            continue

        unit_type = unit.get("type", "")
        lang = unit.get("language_detected", "en")
        lang_p = _lang_prop(lang)
        book_id = unit.get("source_book_id", "")
        unit_id = unit.get("unit_id", "")
        confidence = float(unit.get("confidence", 0.5))
        evidence_count = len(unit.get("evidence_jsonb") or [])
        subject = unit.get("subject")
        subject_key = unit.get("canonical_key")
        obj = unit.get("object")
        predicate = unit.get("predicate")

        # Fix 8: domain_type is stored in payload_jsonb by unit_extractor._to_db_dict
        payload = unit.get("payload_jsonb") or {}
        domain_type = payload.get("domain_type", "general")
        domain_lbl = _domain_label(domain_type)

        if not subject or not subject_key:
            continue

        if unit_type in ("claim", "comparison", "prescription"):
            if not obj:
                continue
            claim_params.append({
                "s_key": subject_key,
                "subject": subject,
                "o_key": _norm_key(obj),
                "obj": obj,
                "rel_type": _to_rel_type(predicate),
                "lang_prop": lang_p,
                "domain_lbl": domain_lbl,
                "book_id": book_id,
                "unit_id": unit_id,
                "confidence": confidence,
                "ev_count": evidence_count,
            })

        elif unit_type in ("definition", "principle", "observation"):
            definition_params.append({
                "s_key": subject_key,
                "subject": subject,
                "lang_prop": lang_p,
                "domain_lbl": domain_lbl,
                "book_id": book_id,
            })

        elif unit_type in ("process", "practice"):
            obj_key = _norm_key(subject + "_process")
            # Fix 14: extract ordered steps + inputs/output from payload
            steps = payload.get("steps") or []
            process_params.append({
                "s_key": subject_key,
                "subject": subject,
                "p_id": obj_key,
                "lang_prop": lang_p,
                "domain_lbl": domain_lbl,
                "book_id": book_id,
                "unit_id": unit_id,
                "confidence": confidence,
                "steps": steps,                          # Fix 14: list[str]
                "inputs": payload.get("inputs") or [],   # Fix 14: list[str]
                "output": payload.get("output") or "",   # Fix 14: str
            })

    merged_count = 0

    with driver.session() as session:
        # ── Batch 1: claims / comparisons / prescriptions ──────────────────────
        # Grouping: rel_type → domain_lbl → lang_prop
        # Each innermost group → one UNWIND session.run() call.
        if claim_params:
            by_rel: dict[str, list[dict]] = {}
            for p in claim_params:
                by_rel.setdefault(p["rel_type"], []).append(p)

            for rel_type, rel_batch in by_rel.items():
                if not _safe_rel_type(rel_type):
                    rel_type = DEFAULT_RELATION_TYPE

                by_domain: dict[str, list[dict]] = {}
                for p in rel_batch:
                    by_domain.setdefault(p["domain_lbl"], []).append(p)

                for domain_lbl, domain_batch in by_domain.items():
                    s_label = _subject_label(domain_lbl)

                    by_lang: dict[str, list[dict]] = {}
                    for p in domain_batch:
                        by_lang.setdefault(p["lang_prop"], []).append(p)

                    for lang_p, lbatch in by_lang.items():
                        session.run(
                            f"""
                            UNWIND $units AS u
                            MERGE (s:{s_label} {{canonical_key: u.s_key}})
                            ON CREATE SET s.created_at = datetime()
                            SET s.{lang_p} = u.subject,
                                s.book_ids = CASE WHEN u.book_id IN coalesce(s.book_ids, [])
                                             THEN s.book_ids
                                             ELSE coalesce(s.book_ids, []) + [u.book_id] END
                            MERGE (o:Concept {{canonical_key: u.o_key}})
                            ON CREATE SET o.created_at = datetime()
                            SET o.{lang_p} = u.obj,
                                o.book_ids = CASE WHEN u.book_id IN coalesce(o.book_ids, [])
                                             THEN o.book_ids
                                             ELSE coalesce(o.book_ids, []) + [u.book_id] END
                            MERGE (s)-[r:{rel_type} {{unit_id: u.unit_id}}]->(o)
                            SET r.confidence = u.confidence, r.evidence_count = u.ev_count
                            """,
                            units=lbatch,
                        )
                        merged_count += len(lbatch) * 2  # subject + object per unit

        # ── Batch 2: definitions / principles / observations ──────────────────
        # Grouping: domain_lbl → lang_prop
        if definition_params:
            by_domain = {}
            for p in definition_params:
                by_domain.setdefault(p["domain_lbl"], []).append(p)

            for domain_lbl, domain_batch in by_domain.items():
                s_label = _subject_label(domain_lbl)

                by_lang = {}
                for p in domain_batch:
                    by_lang.setdefault(p["lang_prop"], []).append(p)

                for lang_p, lbatch in by_lang.items():
                    session.run(
                        f"""
                        UNWIND $units AS u
                        MERGE (s:{s_label} {{canonical_key: u.s_key}})
                        ON CREATE SET s.created_at = datetime()
                        SET s.{lang_p} = u.subject,
                            s.book_ids = CASE WHEN u.book_id IN coalesce(s.book_ids, [])
                                         THEN s.book_ids
                                         ELSE coalesce(s.book_ids, []) + [u.book_id] END
                        """,
                        units=lbatch,
                    )
                    merged_count += len(lbatch)

        # ── Batch 3: processes / practices ────────────────────────────────────
        # Grouping: domain_lbl → lang_prop
        # Fix 14: FOREACH over steps creates ordered :Step nodes.
        #   [idx IN range(0, size(u.steps)-1) | {i:idx, text:u.steps[idx]}]
        #   evaluates to [] when u.steps is empty → FOREACH does nothing.
        if process_params:
            by_domain = {}
            for p in process_params:
                by_domain.setdefault(p["domain_lbl"], []).append(p)

            for domain_lbl, domain_batch in by_domain.items():
                s_label = _subject_label(domain_lbl)

                by_lang = {}
                for p in domain_batch:
                    by_lang.setdefault(p["lang_prop"], []).append(p)

                for lang_p, lbatch in by_lang.items():
                    session.run(
                        f"""
                        UNWIND $units AS u
                        MERGE (s:{s_label} {{canonical_key: u.s_key}})
                        ON CREATE SET s.created_at = datetime()
                        SET s.{lang_p} = u.subject,
                            s.book_ids = CASE WHEN u.book_id IN coalesce(s.book_ids, [])
                                         THEN s.book_ids
                                         ELSE coalesce(s.book_ids, []) + [u.book_id] END
                        MERGE (p:Process {{id: u.p_id}})
                        ON CREATE SET p.created_at = datetime(), p.book_id = u.book_id
                        SET p.inputs = u.inputs, p.output = u.output
                        MERGE (s)-[r:DESCRIBES_PROCESS {{unit_id: u.unit_id}}]->(p)
                        SET r.confidence = u.confidence
                        WITH s, p, u
                        FOREACH (sd IN [idx IN range(0, size(u.steps)-1) |
                                        {{i: idx, text: u.steps[idx]}}] |
                            MERGE (st:Step {{process_id: u.p_id, order: sd.i}})
                            SET st.text = sd.text
                            MERGE (p)-[:STEP {{order: sd.i}}]->(st)
                        )
                        """,
                        units=lbatch,
                    )
                    merged_count += len(lbatch)

    logger.info(
        "graph_build_complete",
        units_processed=len(units),
        claims=len(claim_params),
        definitions=len(definition_params),
        processes=len(process_params),
        nodes_merged=merged_count,
    )
    return merged_count


def _safe_rel_type(rel_type: str) -> bool:
    """Check that a relationship type string is safe to interpolate into Cypher.

    Accepts only strings from the pre-approved set or strings that consist
    entirely of uppercase letters, digits, and underscores — the standard
    Neo4j relationship type format.
    """
    if rel_type in _SAFE_REL_TYPES:
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", rel_type))
