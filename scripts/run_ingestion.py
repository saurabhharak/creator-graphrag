"""Direct ingestion runner — bypasses Celery, runs IngestionPipeline inline.

Ingests all extracted books from data/extracted/ (or a single book with --book).
Each book's stable UUID5 (matching seed_books.py) is used as book_id.
Job records are inserted into Postgres before each run.

Usage:
    python scripts/run_ingestion.py               # ingest all books
    python scripts/run_ingestion.py --skip-done   # skip books already in Qdrant
    python scripts/run_ingestion.py --book "Vriksha Ayurveda of Surapala Nalini Sadhale 1996"
    python scripts/run_ingestion.py --no-graph    # skip Neo4j graph build
    python scripts/run_ingestion.py --no-units    # skip KU extraction
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

# ── Repo root setup ───────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
EXTRACTED_DIR = REPO_ROOT / "data" / "extracted"

sys.path.insert(0, str(REPO_ROOT / "apps" / "worker"))

# Load .env before importing worker modules
import os
env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from app.core.config import worker_settings
from app.pipelines.ingestion_pipeline import IngestionConfig, IngestionPipeline


def get_ingested_book_ids() -> set[str]:
    """Return set of book_ids that already have chunks in Qdrant."""
    from qdrant_client import QdrantClient
    try:
        client = QdrantClient(host=worker_settings.QDRANT_HOST, port=worker_settings.QDRANT_PORT)
        result = client.scroll(
            worker_settings.QDRANT_COLLECTION_NAME,
            limit=10000,
            with_payload=True,
            with_vectors=False,
        )
        return {p.payload.get("book_id") for p in result[0] if p.payload.get("book_id")}
    except Exception:
        return set()


def book_uuid(folder_name: str) -> uuid.UUID:
    """Stable UUID5 matching seed_books.py _book_uuid()."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"creator-graphrag.book.{folder_name}")


def create_job(book_id: uuid.UUID) -> uuid.UUID:
    """Insert a new ingestion_jobs row and return its job_id."""
    import psycopg2

    job_id = uuid.uuid4()
    sync_url = worker_settings.DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = psycopg2.connect(sync_url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ingestion_jobs (job_id, book_id, status, stage, progress, config_json)
            VALUES (%s, %s, 'queued', 'upload', 0, '{}')
            """,
            (str(job_id), str(book_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def get_book_title(book_id: uuid.UUID) -> str | None:
    """Fetch book title from Postgres."""
    import psycopg2

    sync_url = worker_settings.DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = psycopg2.connect(sync_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT title FROM books WHERE book_id = %s", (str(book_id),))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def ingest_book(
    folder: Path,
    extract_units: bool = True,
    build_graph: bool = True,
) -> None:
    b_id = book_uuid(folder.name)

    # Check book exists in DB (seed_books.py must have run first)
    title = get_book_title(b_id)
    if title is None:
        print(f"  WARN  book_id {b_id} not found in DB — run seed_books.py first. Skipping.")
        return

    job_id = create_job(b_id)

    print(f"\n{'=' * 60}")
    print(f"Book:    {folder.name}")
    print(f"Title:   {title}")
    print(f"book_id: {b_id}")
    print(f"job_id:  {job_id}")
    sys.stdout.flush()

    config = IngestionConfig(
        source_format="pre_extracted_sarvam",
        pre_extracted_dir=str(folder),
        extract_knowledge_units=extract_units,
        build_graph=build_graph,
    )
    pipeline = IngestionPipeline(
        job_id=job_id,
        book_id=b_id,
        config=config,
        book_title=title,
    )

    await pipeline.run()
    print(f"DONE     {folder.name}")


async def main(args: argparse.Namespace) -> None:
    folders = sorted(
        d for d in EXTRACTED_DIR.iterdir()
        if d.is_dir() and (d / "document.md").exists()
    )

    if args.book:
        folders = [f for f in folders if args.book.lower() in f.name.lower()]
        if not folders:
            print(f"No extracted folder matches '{args.book}'")
            sys.exit(1)

    # Check Qdrant for already-ingested books
    done_ids: set[str] = set()
    if args.skip_done:
        done_ids = get_ingested_book_ids()
        print(f"  skip-done: {len(done_ids)} book(s) already in Qdrant will be skipped")

    skipped = []
    print(f"Ingesting {len(folders)} book(s) from {EXTRACTED_DIR}")
    print(f"  extraction model: {worker_settings.LLM_EXTRACTION_MODEL}")
    print(f"  embedding model:  {worker_settings.EMBEDDING_MODEL} ({worker_settings.EMBEDDING_PROVIDER})")
    print(f"  qdrant:           {worker_settings.QDRANT_HOST}:{worker_settings.QDRANT_PORT}")

    failed = []
    for folder in folders:
        b_id = str(book_uuid(folder.name))
        if args.skip_done and b_id in done_ids:
            print(f"\n  SKIP  {folder.name}  (already in Qdrant)")
            skipped.append(folder.name)
            continue
        try:
            await ingest_book(folder, extract_units=not args.no_units, build_graph=not args.no_graph)
        except Exception as exc:
            print(f"\nERROR on {folder.name}: {exc}", file=sys.stderr)
            failed.append(folder.name)

    done_count = len(folders) - len(failed) - len(skipped)
    print(f"\n{'=' * 60}")
    print(f"Finished. {done_count} ingested, {len(skipped)} skipped, {len(failed)} failed.")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  - {name}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run ingestion pipeline for all extracted books.")
    p.add_argument("--book", metavar="NAME", default=None,
                   help="Ingest only books whose folder name contains this string")
    p.add_argument("--no-units", action="store_true",
                   help="Skip knowledge unit extraction (LLM calls)")
    p.add_argument("--no-graph", action="store_true",
                   help="Skip Neo4j graph build")
    p.add_argument("--skip-done", action="store_true",
                   help="Skip books that already have chunks in Qdrant")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
