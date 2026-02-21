"""Extract knowledge units from Qdrant chunks using LLM.

Reads text chunks from the Qdrant `chunks_multilingual` collection,
sends each to an OpenAI-compatible LLM (GPT-4.1 via Zenmux by default),
validates the structured JSON output, and inserts knowledge units into
the PostgreSQL `knowledge_units` table.

Does NOT require Celery or the API server — runs standalone.

Prerequisites:
  1. Qdrant running with chunks already imported (import_sarvam.py)
  2. PostgreSQL running with migration 0006 applied
  3. OPENAI_API_KEY set (or ZENMUX_API_KEY for Zenmux proxy)

Usage — extract all books:
    python scripts/extract_knowledge_units.py

Usage — extract a single book:
    python scripts/extract_knowledge_units.py --book "Introduction to Natural Farming"

Usage — dry run (shows chunk count, no LLM calls):
    python scripts/extract_knowledge_units.py --dry-run

Usage — resume after interruption:
    python scripts/extract_knowledge_units.py --resume
    (skips chunks that already have knowledge units)

Options:
    --book NAME          Single book name to extract from
    --qdrant-host HOST   Qdrant host (default: localhost)
    --qdrant-port PORT   Qdrant port (default: 6333)
    --collection NAME    Qdrant collection (default: chunks_multilingual)
    --pg-url URL         PostgreSQL URL (default: from .env)
    --model MODEL        LLM model ID (default: openai/gpt-4.1)
    --api-key KEY        OpenAI API key (default: from .env OPENAI_API_KEY)
    --base-url URL       OpenAI base URL (default: from .env OPENAI_BASE_URL)
    --batch-size N       Chunks per progress report (default: 10)
    --max-chunks N       Max chunks to process (default: all)
    --resume             Skip chunks that already have units in DB
    --dry-run            Count chunks without calling LLM
    --force              Re-extract even if units exist for chunk
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Literal

# ── Pydantic models (inline, no worker dependency) ────────────────────────────

from pydantic import BaseModel, Field, model_validator

# Confidence threshold below which units are flagged for human review
NEEDS_REVIEW_THRESHOLD = 0.65
MAX_UNITS_PER_CHUNK = 20


class EvidenceItem(BaseModel):
    book_id: str
    chapter_id: str = ""
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
        if self.type in ("claim", "comparison"):
            if not self.subject or not self.object:
                raise ValueError(f"type={self.type} requires both subject and object")
        return self


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_canonical_key(text: str) -> str:
    """Normalize text to a stable deduplication key."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
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
    """Render the extraction prompt (inline, no Jinja2 dependency)."""
    return f"""You are a knowledge extraction specialist. Extract structured knowledge units from the provided text chunk.

**Text Language:** {language_detected}
**Source:** Book "{book_title}", Chapter "{chapter_title}", Pages {page_start}–{page_end}

**Instructions:**
1. Extract ALL distinct knowledge units: definitions, claims, processes, comparisons.
2. For each unit, identify the subject, predicate, and object (where applicable).
3. Every unit MUST cite at least one evidence snippet from the provided text.
4. Do NOT generate claims not supported by the text.
5. Return ONLY valid JSON. No explanation outside the JSON.

**Output Contract:**
```json
{{
  "units": [
    {{
      "type": "claim|definition|process|comparison",
      "language": "{language_detected}",
      "subject": "string or null",
      "predicate": "string or null (max 50 chars)",
      "object": "string or null",
      "conditions": "string or null",
      "confidence": 0.0–1.0,
      "evidence": [
        {{
          "book_id": "{book_id}",
          "chapter_id": "",
          "page_start": {page_start},
          "page_end": {page_end},
          "snippet": "verbatim quote, max 600 chars"
        }}
      ],
      "payload": {{}}
    }}
  ]
}}
```

**Quality Rules:**
- Reject: missing subject/object (except for definition/process types)
- Reject: evidence snippet > 600 characters
- Set confidence < 0.65 if you are uncertain
- For process type: use payload.steps array instead of subject/predicate/object

**Text to extract from:**
---
{chunk_text}
---"""


def _parse_and_validate(raw_json: str) -> list[ExtractedUnit]:
    """Parse LLM JSON response and validate each unit."""
    # Strip markdown code fences if present
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        # Remove ```json or ``` at start and ``` at end
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"    WARN: JSON parse failed: {e}")
        return []

    raw_units = data.get("units", [])
    if not isinstance(raw_units, list):
        print(f"    WARN: unexpected shape, keys={list(data.keys())}")
        return []

    valid: list[ExtractedUnit] = []
    for i, raw in enumerate(raw_units[:MAX_UNITS_PER_CHUNK]):
        try:
            valid.append(ExtractedUnit.model_validate(raw))
        except Exception as exc:
            pass  # silently skip invalid units

    return valid


def _to_db_dict(unit: ExtractedUnit, source_book_id: str, chunk_id: str | None = None) -> dict:
    """Convert an ExtractedUnit to a dict for DB insertion."""
    status = "needs_review" if unit.confidence < NEEDS_REVIEW_THRESHOLD else "extracted"
    canonical = make_canonical_key(unit.subject) if unit.subject else None

    return {
        "unit_id": str(uuid.uuid4()),
        "source_book_id": source_book_id,
        "source_chunk_id": chunk_id,
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


# ── LLM call ──────────────────────────────────────────────────────────────────


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> tuple[str, int, int]:
    """Call OpenAI-compatible chat completion. Returns (content, in_tokens, out_tokens)."""
    import openai

    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=4000,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    usage = response.usage
    in_tok = usage.prompt_tokens if usage else 0
    out_tok = usage.completion_tokens if usage else 0
    return content, in_tok, out_tok


# ── Qdrant reader ─────────────────────────────────────────────────────────────


def _get_chunks_from_qdrant(
    host: str,
    port: int,
    collection: str,
    book_name: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Scroll all chunks from Qdrant, optionally filtered by book_name."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = QdrantClient(host=host, port=port, check_compatibility=False)

    scroll_filter = None
    if book_name:
        scroll_filter = Filter(
            must=[FieldCondition(key="book_name", match=MatchValue(value=book_name))]
        )

    all_points = []
    offset = None
    batch_limit = 100

    while True:
        points, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=batch_limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(points)
        if next_offset is None or (limit and len(all_points) >= limit):
            break
        offset = next_offset

    if limit:
        all_points = all_points[:limit]

    return [
        {
            "point_id": str(p.id),
            "text": p.payload.get("text", ""),
            "book_id": p.payload.get("book_id", ""),
            "book_name": p.payload.get("book_name", ""),
            "chunk_type": p.payload.get("chunk_type", "general"),
            "language_detected": p.payload.get("language_detected", "en"),
            "page_start": p.payload.get("page_start", 0),
            "page_end": p.payload.get("page_end", 0),
            "section_title": p.payload.get("section_title", ""),
            "text_hash": p.payload.get("text_hash", ""),
        }
        for p in all_points
    ]


# ── PostgreSQL helpers ─────────────────────────────────────────────────────────


async def _insert_knowledge_units(pg_url: str, units: list[dict]) -> None:
    """Bulk INSERT knowledge units via asyncpg."""
    if not units:
        return

    import asyncpg

    # Strip SQLAlchemy dialect prefix
    raw_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    sql = """
        INSERT INTO knowledge_units (
            unit_id, source_book_id, source_chunk_id,
            type, language_detected, language_confidence,
            subject, predicate, object,
            payload_jsonb, confidence, status,
            evidence_jsonb, canonical_key
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid,
            $4, $5, $6,
            $7, $8, $9,
            $10::jsonb, $11, $12,
            $13::jsonb, $14
        )
        ON CONFLICT (unit_id) DO NOTHING
    """

    rows = [
        (
            u["unit_id"],
            u["source_book_id"],
            u.get("source_chunk_id"),
            u["type"],
            u["language_detected"],
            u.get("language_confidence"),
            u.get("subject"),
            u.get("predicate"),
            u.get("object"),
            json.dumps(u.get("payload_jsonb", {})),
            float(u["confidence"]),
            u.get("status", "extracted"),
            json.dumps(u.get("evidence_jsonb", [])),
            u.get("canonical_key"),
        )
        for u in units
    ]

    conn = await asyncpg.connect(raw_url)
    try:
        await conn.executemany(sql, rows)
        print(f"    DB: inserted {len(rows)} knowledge units")
    finally:
        await conn.close()


async def _log_llm_usage(
    pg_url: str,
    *,
    operation_type: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    book_id: str | None = None,
) -> None:
    """Log LLM usage for cost tracking."""
    import asyncpg

    raw_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    _COST_PER_1K = {"input": 0.005, "output": 0.015}
    estimated_cost = round(
        (input_tokens * _COST_PER_1K["input"] + output_tokens * _COST_PER_1K["output"]) / 1000, 6
    )

    sql = """
        INSERT INTO llm_usage_logs (
            log_id, operation_type, model_id,
            input_tokens, output_tokens, estimated_cost_usd,
            book_id
        ) VALUES (
            $1::uuid, $2, $3,
            $4, $5, $6,
            $7::uuid
        )
    """

    conn = await asyncpg.connect(raw_url)
    try:
        await conn.execute(
            sql,
            str(uuid.uuid4()),
            operation_type,
            model_id,
            input_tokens,
            output_tokens,
            estimated_cost,
            book_id,
        )
    except Exception:
        pass  # Non-critical
    finally:
        await conn.close()


async def _get_existing_book_ids_with_units(pg_url: str) -> set[str]:
    """Get book_ids that already have knowledge units (for --resume)."""
    import asyncpg

    raw_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT source_book_id::text FROM knowledge_units WHERE deleted_at IS NULL"
        )
        return {r["source_book_id"] for r in rows}
    finally:
        await conn.close()


async def _count_units_for_book(pg_url: str, book_id: str) -> int:
    """Count existing KU rows for a book."""
    import asyncpg

    raw_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)
    try:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM knowledge_units WHERE source_book_id = $1::uuid AND deleted_at IS NULL",
            book_id,
        )
        return row["cnt"] if row else 0
    finally:
        await conn.close()


# ── Ensure books exist in PostgreSQL ───────────────────────────────────────────


async def _ensure_books_in_postgres(pg_url: str, books: dict[str, list[dict]]) -> None:
    """Create book records in PostgreSQL if they don't exist.

    import_sarvam.py only writes to Qdrant. The knowledge_units table has
    a FK to books.book_id, so we need to ensure book rows exist first.
    """
    import asyncpg

    raw_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)

    try:
        for book_name, book_chunks in books.items():
            book_id = book_chunks[0]["book_id"]

            # Check if book already exists
            existing = await conn.fetchrow(
                "SELECT book_id FROM books WHERE book_id = $1::uuid", book_id
            )
            if existing:
                continue

            # Detect primary language from chunks
            lang_counts: dict[str, int] = {}
            for c in book_chunks:
                lang = c["language_detected"]
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            primary_lang = max(lang_counts, key=lang_counts.get)  # type: ignore

            # Insert minimal book record
            await conn.execute(
                """
                INSERT INTO books (book_id, title, language_primary, tags)
                VALUES ($1::uuid, $2, $3, '[]'::jsonb)
                ON CONFLICT (book_id) DO NOTHING
                """,
                book_id,
                book_name,
                primary_lang,
            )
            print(f"  DB: created book record for '{book_name}' ({book_id})")
    finally:
        await conn.close()


# ── Main extraction loop ──────────────────────────────────────────────────────


async def run_extraction(args: argparse.Namespace) -> None:
    """Main extraction loop."""
    # Load .env if available
    env_path = Path(__file__).resolve().parent.parent / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env_vars[key.strip()] = val.strip()

    api_key = args.api_key or env_vars.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = args.base_url or env_vars.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    pg_url = args.pg_url or env_vars.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    model = args.model

    if not api_key:
        print("ERROR: No API key provided. Use --api-key or set OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    if not pg_url:
        print("ERROR: No PostgreSQL URL. Use --pg-url or set DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    # ── Read chunks from Qdrant ───────────────────────────────────────────────
    print(f"\nReading chunks from Qdrant ({args.qdrant_host}:{args.qdrant_port})...")
    chunks = _get_chunks_from_qdrant(
        args.qdrant_host,
        args.qdrant_port,
        args.collection,
        book_name=args.book,
        limit=args.max_chunks,
    )
    print(f"  Found {len(chunks)} chunks")

    if not chunks:
        print("No chunks found. Make sure books are imported with import_sarvam.py.")
        sys.exit(0)

    # Group chunks by book
    books: dict[str, list[dict]] = {}
    for c in chunks:
        bname = c["book_name"]
        books.setdefault(bname, []).append(c)

    print(f"  Books: {len(books)}")
    for bname, bchunks in books.items():
        print(f"    - {bname}: {len(bchunks)} chunks")

    if args.dry_run:
        print("\n[DRY RUN] No LLM calls or DB writes.")
        lang_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        for c in chunks:
            lang_counts[c["language_detected"]] = lang_counts.get(c["language_detected"], 0) + 1
            type_counts[c["chunk_type"]] = type_counts.get(c["chunk_type"], 0) + 1
        print(f"  Language distribution: {lang_counts}")
        print(f"  Chunk type distribution: {type_counts}")
        return

    # ── Ensure book records exist in PostgreSQL ───────────────────────────────
    # import_sarvam.py writes only to Qdrant; the knowledge_units table has
    # a FK to books.book_id, so we need to ensure book rows exist first.
    await _ensure_books_in_postgres(pg_url, books)

    # ── Resume logic ──────────────────────────────────────────────────────────
    skip_book_ids: set[str] = set()
    if args.resume:
        skip_book_ids = await _get_existing_book_ids_with_units(pg_url)
        if skip_book_ids:
            print(f"\n[RESUME] Found existing units for {len(skip_book_ids)} books, will skip them")

    # ── Process each book ─────────────────────────────────────────────────────
    total_units = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_errors = 0

    for book_name, book_chunks in books.items():
        book_id = book_chunks[0]["book_id"]

        if args.resume and book_id in skip_book_ids:
            existing = await _count_units_for_book(pg_url, book_id)
            print(f"\n{'─' * 60}")
            print(f"  BOOK  {book_name}")
            print(f"  SKIP  already has {existing} units (use --force to re-extract)")
            continue

        print(f"\n{'─' * 60}")
        print(f"  BOOK  {book_name}")
        print(f"  ID    {book_id}")
        print(f"  CHUNKS {len(book_chunks)}")
        print(f"  MODEL  {model}")
        print(f"  LLM    {base_url or 'https://api.openai.com/v1'}")

        book_units: list[dict] = []
        book_in_tok = 0
        book_out_tok = 0
        book_errors = 0

        for i, chunk in enumerate(book_chunks):
            try:
                system_prompt = _render_prompt(
                    language_detected=chunk["language_detected"],
                    book_title=book_name,
                    chapter_title=chunk["section_title"] or "",
                    page_start=chunk["page_start"],
                    page_end=chunk["page_end"],
                    book_id=book_id,
                    chunk_text=chunk["text"],
                )

                content, in_tok, out_tok = await _call_llm(
                    system_prompt=system_prompt,
                    user_prompt="Extract knowledge units from the text above.",
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                )

                book_in_tok += in_tok
                book_out_tok += out_tok

                units = _parse_and_validate(content)
                db_dicts = [_to_db_dict(u, book_id) for u in units]
                book_units.extend(db_dicts)

            except Exception as exc:
                book_errors += 1
                print(f"    ERROR chunk {i + 1}: {type(exc).__name__}: {str(exc)[:100]}")

            # Progress reporting
            if (i + 1) % args.batch_size == 0 or (i + 1) == len(book_chunks):
                nr = sum(1 for u in book_units if u["status"] == "needs_review")
                print(
                    f"    [{i + 1}/{len(book_chunks)}] "
                    f"units={len(book_units)} "
                    f"(needs_review={nr}) "
                    f"tokens={book_in_tok + book_out_tok:,} "
                    f"errors={book_errors}"
                )

        # Log LLM usage
        if book_in_tok > 0:
            await _log_llm_usage(
                pg_url,
                operation_type="extraction",
                model_id=model,
                input_tokens=book_in_tok,
                output_tokens=book_out_tok,
                book_id=book_id,
            )

        # Insert knowledge units
        if book_units:
            await _insert_knowledge_units(pg_url, book_units)

        # Book summary
        needs_review = sum(1 for u in book_units if u["status"] == "needs_review")
        type_dist = {}
        for u in book_units:
            type_dist[u["type"]] = type_dist.get(u["type"], 0) + 1

        print(f"  DONE  {len(book_units)} units extracted")
        print(f"    Types: {type_dist}")
        print(f"    Needs review: {needs_review}")
        print(f"    Tokens: {book_in_tok:,} in + {book_out_tok:,} out = {book_in_tok + book_out_tok:,}")

        total_units += len(book_units)
        total_input_tokens += book_in_tok
        total_output_tokens += book_out_tok
        total_errors += book_errors

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("Extraction Summary:")
    print(f"  Total units:    {total_units}")
    print(f"  Total tokens:   {total_input_tokens + total_output_tokens:,}")
    print(f"  Total errors:   {total_errors}")
    est_cost = (total_input_tokens * 0.005 + total_output_tokens * 0.015) / 1000
    print(f"  Est. cost:      ${est_cost:.4f}")
    print("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract knowledge units from Qdrant chunks using LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--book", metavar="NAME", default=None,
                   help="Single book name to extract from")
    p.add_argument("--qdrant-host", default="localhost")
    p.add_argument("--qdrant-port", type=int, default=6333)
    p.add_argument("--collection", default="chunks_multilingual")
    p.add_argument("--pg-url", metavar="URL", default=None,
                   help="PostgreSQL URL (default: from .env DATABASE_URL)")
    p.add_argument("--model", default="openai/gpt-4.1",
                   help="LLM model ID (default: openai/gpt-4.1)")
    p.add_argument("--api-key", metavar="KEY", default=None,
                   help="OpenAI API key (default: from .env OPENAI_API_KEY)")
    p.add_argument("--base-url", metavar="URL", default=None,
                   help="OpenAI base URL (default: from .env OPENAI_BASE_URL)")
    p.add_argument("--batch-size", type=int, default=10,
                   help="Chunks per progress report (default: 10)")
    p.add_argument("--max-chunks", type=int, default=None,
                   help="Max chunks to process (default: all)")
    p.add_argument("--resume", action="store_true",
                   help="Skip books that already have units in DB")
    p.add_argument("--dry-run", action="store_true",
                   help="Count chunks without calling LLM")
    p.add_argument("--force", action="store_true",
                   help="Re-extract even if units exist for book")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_extraction(args))


if __name__ == "__main__":
    main()
