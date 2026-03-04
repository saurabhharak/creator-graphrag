"""Seed all 14 books — Sarvam AI extraction + Postgres registration.

Two-phase pipeline (can run each phase independently):

  Phase 1 — Extract:
    Runs Sarvam AI Document Intelligence on each PDF in data/Books/ that has
    not yet been extracted. Writes document.md + metadata/ to data/extracted/.

  Phase 2 — Register:
    Inserts each extracted book folder into the Postgres `books` table using
    the same UUID5 that import_sarvam.py and extract_knowledge_units.py use,
    so all downstream scripts share a consistent book_id.

Prerequisites:
  - SARVAM_API_KEY env var (or --api-key) — only needed for Phase 1
  - Postgres running and DATABASE_URL in .env — needed for Phase 2
  - pip install sarvamai pymupdf langdetect psycopg2-binary

Usage — full pipeline (extract then register):
    python scripts/seed_books.py --api-key YOUR_SARVAM_KEY

Usage — register only (books already extracted):
    python scripts/seed_books.py --skip-extract

Usage — extract only (don't touch Postgres):
    python scripts/seed_books.py --api-key YOUR_SARVAM_KEY --skip-register

Usage — dry run (no API calls, no DB writes):
    python scripts/seed_books.py --dry-run

Options:
    --api-key KEY        Sarvam AI subscription key (or SARVAM_API_KEY env var)
    --books-dir DIR      Directory with source PDFs (default: data/Books)
    --extracted-dir DIR  Directory for extracted output (default: data/extracted)
    --pg-url URL         PostgreSQL URL (default: read from .env DATABASE_URL)
    --skip-extract       Skip Phase 1 — only register in Postgres
    --skip-register      Skip Phase 2 — only run Sarvam extraction
    --force-extract      Re-extract even if document.md already exists
    --force-register     Re-register books even if already in Postgres
    --book NAME          Process only this book (PDF stem or folder name)
    --dry-run            Print what would happen, no API calls or DB writes
    --lang CODE          Override language detection (e.g. en-IN, mr-IN, sa-IN)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import uuid
from pathlib import Path

# Fix Windows console encoding for Devanagari output
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Known book metadata — title, author, year, tags — keyed by PDF stem.
# Only used when registering in Postgres. Extend as needed.
BOOK_CATALOG: dict[str, dict] = {
    "Introduction to Natural Farming": {
        "title": "Introduction to Natural Farming",
        "author": "Subhash Palekar",
        "year": None,
        "tags": ["natural farming", "ZBNF", "biofertilizer"],
        "language_primary": "en-IN",
    },
    "An agricultural testament": {
        "title": "An Agricultural Testament",
        "author": "Sir Albert Howard",
        "year": 1940,
        "tags": ["organic farming", "compost", "indore process"],
        "language_primary": "en-IN",
    },
    "आपले हात जगन्नाथ": {
        "title": "आपले हात जगन्नाथ",
        "author": None,
        "year": None,
        "tags": ["natural farming", "marathi", "jeevamrit"],
        "language_primary": "mr-IN",
    },
    "IITKA_Book_Traditional-Knowledge-in-Agriculture-English_0_0": {
        "title": "Traditional Knowledge in Agriculture",
        "author": "IIT Kanpur",
        "year": None,
        "tags": ["indigenous knowledge", "ITK", "traditional agriculture"],
        "language_primary": "en-IN",
    },
    "Inventory-of-Indigenous-Technical-Knowledge-in-Agriculture-Document-1": {
        "title": "Inventory of Indigenous Technical Knowledge in Agriculture — Vol 1",
        "author": None,
        "year": None,
        "tags": ["ITK", "indigenous", "inventory"],
        "language_primary": "en-IN",
    },
    "Inventory-of-Indigenous-Technical-Knowledge-in-Agriculture-Documen-2.1": {
        "title": "Inventory of Indigenous Technical Knowledge in Agriculture — Vol 2",
        "author": None,
        "year": None,
        "tags": ["ITK", "indigenous", "inventory"],
        "language_primary": "en-IN",
    },
    "Natural Farming for Sustainable Agriculture": {
        "title": "Natural Farming for Sustainable Agriculture",
        "author": None,
        "year": None,
        "tags": ["natural farming", "sustainable agriculture"],
        "language_primary": "en-IN",
    },
    "Technical-Manual-on-Natural_Farming_10.03.2025": {
        "title": "Technical Manual on Natural Farming",
        "author": None,
        "year": 2025,
        "tags": ["natural farming", "technical manual"],
        "language_primary": "en-IN",
    },
    "2015.62133.Agriculture-And-Agriculturists-In-Ancient-India1932": {
        "title": "Agriculture and Agriculturists in Ancient India",
        "author": None,
        "year": 1932,
        "tags": ["history", "ancient India", "agriculture"],
        "language_primary": "en-IN",
    },
    "handbookofindian00mukerich": {
        "title": "Handbook of Indian Agriculture",
        "author": None,
        "year": None,
        "tags": ["handbook", "Indian agriculture"],
        "language_primary": "en-IN",
    },
    "Krishi_Parashar": {
        "title": "Krishi Parashar",
        "author": "Parashar",
        "year": None,
        "tags": ["Sanskrit", "classical", "Krishi Parashar"],
        "language_primary": "sa-IN",
    },
    "Vriksha Ayurveda of Surapala Nalini Sadhale 1996": {
        "title": "Vriksha Ayurveda of Surapala",
        "author": "Surapala (trans. Nalini Sadhale)",
        "year": 1996,
        "tags": ["Sanskrit", "plant science", "Vriksha Ayurveda"],
        "language_primary": "sa-IN",
    },
    "AgriHistory1": {
        "title": "Agricultural History — Vol 1",
        "author": None,
        "year": None,
        "tags": ["history", "agriculture"],
        "language_primary": "en-IN",
    },
    "AgriHistory2": {
        "title": "Agricultural History — Vol 2",
        "author": None,
        "year": None,
        "tags": ["history", "agriculture"],
        "language_primary": "en-IN",
    },
    "AgriHistory3": {
        "title": "Agricultural History — Vol 3",
        "author": None,
        "year": None,
        "tags": ["history", "agriculture"],
        "language_primary": "en-IN",
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _book_uuid(book_name: str) -> str:
    """Stable UUID5 from book folder name — MUST match import_sarvam.py."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"creator-graphrag.book.{book_name}"))


def _read_env(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val:
        return val
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return default


def _catalog_entry(folder_name: str, extraction_info: dict) -> dict:
    """Build a book record from catalog metadata + extraction_info fallbacks."""
    entry = BOOK_CATALOG.get(folder_name, {})
    lang = entry.get("language_primary") or extraction_info.get("language") or "en-IN"
    return {
        "book_id": _book_uuid(folder_name),
        "title": entry.get("title") or folder_name,
        "author": entry.get("author"),
        "year": entry.get("year"),
        "tags": entry.get("tags", []),
        "language_primary": lang,
    }


# ── Phase 1: Sarvam Extraction ─────────────────────────────────────────────────

def phase_extract(
    books_dir: Path,
    extracted_dir: Path,
    api_key: str,
    force: bool,
    lang_override: str | None,
    dry_run: bool,
    book_filter: str | None,
) -> list[Path]:
    """Run Sarvam AI extraction on all PDFs not yet extracted.

    Returns list of output directories that were produced (or already existed).
    """
    # Import from sarvam_extract.py (same package directory)
    sys.path.insert(0, str(SCRIPT_DIR))
    from sarvam_extract import extract_single, detect_sarvam_language

    pdfs = sorted(books_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {books_dir}")
        return []

    if book_filter:
        pdfs = [p for p in pdfs if book_filter.lower() in p.stem.lower()]
        if not pdfs:
            print(f"No PDFs match filter '{book_filter}'")
            return []

    print(f"\nPhase 1 — Sarvam extraction ({len(pdfs)} PDFs)")
    print(f"  Source:  {books_dir}")
    print(f"  Output:  {extracted_dir}\n")

    done_dirs: list[Path] = []
    for pdf_path in pdfs:
        out_dir = extracted_dir / pdf_path.stem
        doc_md = out_dir / "document.md"

        print(f"  [{pdf_path.name}]")
        if doc_md.exists() and not force:
            print(f"    SKIP  already extracted → {out_dir.name}/", flush=True)
            done_dirs.append(out_dir)
            continue

        if dry_run:
            print(f"    DRY   would extract → {out_dir.name}/", flush=True)
            done_dirs.append(out_dir)
            continue

        if lang_override:
            lang = lang_override
            print(f"    LANG  {lang} (override)", flush=True)
        else:
            lang, method = detect_sarvam_language(pdf_path)
            print(f"    LANG  {lang} ({method})", flush=True)

        try:
            extract_single(pdf_path, out_dir, lang, api_key, force=force)
            done_dirs.append(out_dir)
        except Exception as exc:
            print(f"    ERROR {exc}", file=sys.stderr)

    return done_dirs


# ── Phase 2: Postgres Registration ────────────────────────────────────────────

def phase_register(
    extracted_dir: Path,
    pg_url: str,
    force: bool,
    dry_run: bool,
    book_filter: str | None,
) -> None:
    """Register each extracted book folder in the Postgres `books` table.

    Uses ON CONFLICT DO NOTHING by default, or UPDATE when --force-register.
    book_id is UUID5(NAMESPACE_DNS, "creator-graphrag.book.{folder_name}")
    — identical to the ID used by import_sarvam.py and extract_knowledge_units.py.
    """
    # Convert asyncpg URL to psycopg2 URL
    sync_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print(
            "ERROR: psycopg2 not installed. Run: pip install psycopg2-binary",
            file=sys.stderr,
        )
        sys.exit(1)

    # Collect extracted book folders
    if not extracted_dir.is_dir():
        print(f"ERROR: extracted directory not found: {extracted_dir}", file=sys.stderr)
        sys.exit(1)

    folders = sorted(
        d for d in extracted_dir.iterdir()
        if d.is_dir() and (d / "document.md").exists()
    )
    if book_filter:
        folders = [f for f in folders if book_filter.lower() in f.name.lower()]

    if not folders:
        print("No extracted book folders found (expected document.md inside each).")
        return

    print(f"\nPhase 2 — Postgres registration ({len(folders)} books)")
    print(f"  DB: {sync_url[:sync_url.index('@') + 1]}***\n" if "@" in sync_url else f"  DB: {sync_url}\n")

    if dry_run:
        for folder in folders:
            info = {}
            info_file = folder / "extraction_info.json"
            if info_file.exists():
                info = json.loads(info_file.read_text(encoding="utf-8"))
            rec = _catalog_entry(folder.name, info)
            print(f"    DRY  [{rec['book_id']}] {rec['title']} ({rec['language_primary']})")
        return

    try:
        conn = psycopg2.connect(sync_url)
        conn.autocommit = False
    except Exception as exc:
        print(f"ERROR: Cannot connect to Postgres: {exc}", file=sys.stderr)
        sys.exit(1)

    inserted = skipped = 0
    try:
        with conn.cursor() as cur:
            for folder in folders:
                info = {}
                info_file = folder / "extraction_info.json"
                if info_file.exists():
                    info = json.loads(info_file.read_text(encoding="utf-8"))

                rec = _catalog_entry(folder.name, info)

                if force:
                    cur.execute(
                        """
                        INSERT INTO books
                            (book_id, title, author, year, language_primary, tags,
                             visibility, usage_rights)
                        VALUES
                            (%(book_id)s, %(title)s, %(author)s, %(year)s,
                             %(language_primary)s, %(tags)s, 'public', 'open_access')
                        ON CONFLICT (book_id) DO UPDATE
                            SET title            = EXCLUDED.title,
                                author           = EXCLUDED.author,
                                year             = EXCLUDED.year,
                                language_primary = EXCLUDED.language_primary,
                                tags             = EXCLUDED.tags,
                                updated_at       = NOW()
                        """,
                        {**rec, "tags": json.dumps(rec["tags"])},
                    )
                    print(f"    UPSERT [{rec['book_id']}] {rec['title']}")
                    inserted += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO books
                            (book_id, title, author, year, language_primary, tags,
                             visibility, usage_rights)
                        VALUES
                            (%(book_id)s, %(title)s, %(author)s, %(year)s,
                             %(language_primary)s, %(tags)s, 'public', 'open_access')
                        ON CONFLICT (book_id) DO NOTHING
                        """,
                        {**rec, "tags": json.dumps(rec["tags"])},
                    )
                    if cur.rowcount:
                        print(f"    INSERT [{rec['book_id']}] {rec['title']}")
                        inserted += 1
                    else:
                        print(f"    SKIP   [{rec['book_id']}] {rec['title']} (already registered)")
                        skipped += 1

        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"ERROR during registration: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()

    print(f"\n  Done: {inserted} inserted, {skipped} skipped")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Seed all 14 books: Sarvam AI extraction + Postgres registration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--api-key", metavar="KEY", default=None,
                   help="Sarvam AI subscription key (or SARVAM_API_KEY env var)")
    p.add_argument("--books-dir", metavar="DIR",
                   default=str(REPO_ROOT / "data" / "Books"),
                   help="Directory with source PDFs (default: data/Books)")
    p.add_argument("--extracted-dir", metavar="DIR",
                   default=str(REPO_ROOT / "data" / "extracted"),
                   help="Directory for extracted output (default: data/extracted)")
    p.add_argument("--pg-url", metavar="URL", default=None,
                   help="PostgreSQL connection URL (default: DATABASE_URL from .env)")
    p.add_argument("--skip-extract", action="store_true",
                   help="Skip Phase 1 — only register in Postgres")
    p.add_argument("--skip-register", action="store_true",
                   help="Skip Phase 2 — only run Sarvam extraction")
    p.add_argument("--force-extract", action="store_true",
                   help="Re-extract even if document.md already exists")
    p.add_argument("--force-register", action="store_true",
                   help="UPDATE book row if already registered (instead of skip)")
    p.add_argument("--book", metavar="NAME", default=None,
                   help="Process only books matching this name substring")
    p.add_argument("--lang", metavar="CODE", default=None,
                   help="Override language detection (e.g. en-IN, mr-IN, sa-IN)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would happen — no API calls or DB writes")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    books_dir = Path(args.books_dir)
    extracted_dir = Path(args.extracted_dir)
    extracted_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Extract ──────────────────────────────────────────────────────
    if not args.skip_extract:
        api_key = args.api_key or _read_env("SARVAM_API_KEY")
        if not api_key and not args.dry_run:
            print(
                "ERROR: Sarvam AI API key required for extraction.\n"
                "  Set SARVAM_API_KEY env var or pass --api-key KEY\n"
                "  To skip extraction and only register: use --skip-extract",
                file=sys.stderr,
            )
            sys.exit(1)

        phase_extract(
            books_dir=books_dir,
            extracted_dir=extracted_dir,
            api_key=api_key or "",
            force=args.force_extract,
            lang_override=args.lang,
            dry_run=args.dry_run,
            book_filter=args.book,
        )

    # ── Phase 2: Register ─────────────────────────────────────────────────────
    if not args.skip_register:
        pg_url = args.pg_url or _read_env("DATABASE_URL")
        if not pg_url and not args.dry_run:
            print(
                "ERROR: DATABASE_URL not found.\n"
                "  Set it in .env or pass --pg-url\n"
                "  To skip registration: use --skip-register",
                file=sys.stderr,
            )
            sys.exit(1)

        phase_register(
            extracted_dir=extracted_dir,
            pg_url=pg_url or "postgresql://cgr_user:changeme_required@localhost:5432/creator_graphrag",
            force=args.force_register,
            dry_run=args.dry_run,
            book_filter=args.book,
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
