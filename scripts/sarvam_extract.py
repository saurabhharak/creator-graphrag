"""Extract text from PDF books using Sarvam AI Document Intelligence.

Language is detected from actual PDF content (first 5 pages) using PyMuPDF
+ langdetect. For fully scanned PDFs with no text layer, filename Unicode
analysis is used as a fallback. Use --lang to override at any time.

Usage — single PDF:
    python scripts/sarvam_extract.py --pdf data/books/mybook.pdf

Usage — batch (all PDFs in a folder):
    python scripts/sarvam_extract.py --books-dir data/books/

Usage — explicit language override:
    python scripts/sarvam_extract.py --pdf data/books/farmers_guide.pdf --lang mr-IN

Output layout (inside --output-base / book title):
    data/extracted/<Book Name>/
        document.md          # full extracted text (Markdown)
        metadata/
            page_001.json    # per-page metrics from Sarvam AI
            page_002.json
            ...
        extraction_info.json # language detected, pages processed, timestamp

Requires:
    SARVAM_API_KEY environment variable (or --api-key flag).
    pip install sarvamai pymupdf langdetect
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ── Language Detection ────────────────────────────────────────────────────────

# Devanagari Unicode block (covers Hindi, Marathi, Sanskrit, Nepali, etc.)
_DEVANAGARI_START = 0x0900
_DEVANAGARI_END   = 0x097F

# ── Sarvam AI supported languages (source: docs.sarvam.ai/api-reference-docs) ──
#
# All codes require the -IN suffix when passed to the Sarvam API.
# To add a new language:
#   1. Add its BCP-47 constant below (e.g. _LANG_DOGRI = "doi-IN")
#   2. Add the langdetect code → Sarvam code mapping in _LANGDETECT_MAP
#   3. Add Devanagari/script stopwords to chunker.py _detect_language() if needed
#   4. Add a contextual prefix template in embedder.py build_context_prefix()
#
# Language       | Sarvam Code | Script
# ─────────────────────────────────────────────────────────────────────────────
# Hindi          | hi-IN       | Devanagari
# Bengali        | bn-IN       | Bengali script
# Tamil          | ta-IN       | Tamil script
# Telugu         | te-IN       | Telugu script
# Marathi        | mr-IN       | Devanagari
# Gujarati       | gu-IN       | Gujarati script
# Kannada        | kn-IN       | Kannada script
# Malayalam      | ml-IN       | Malayalam script
# Assamese       | as-IN       | Bengali script (variant)
# Urdu           | ur-IN       | Perso-Arabic (Nastaliq)
# Sanskrit       | sa-IN       | Devanagari (classical)
# Nepali         | ne-IN       | Devanagari
# Dogri          | doi-IN      | Devanagari / Takri
# Bodo           | brx-IN      | Devanagari
# Punjabi        | pa-IN       | Gurmukhi (Shahmukhi for Urdu Punjabi)
# Odia           | od-IN       | Odia script   ← NOTE: Sarvam uses "od-IN" not "or-IN"
# Konkani        | kok-IN      | Devanagari / Latin
# Maithili       | mai-IN      | Devanagari / Mithilakshar
# Sindhi         | sd-IN       | Perso-Arabic / Devanagari
# Kashmiri       | ks-IN       | Perso-Arabic / Devanagari
# Manipuri       | mni-IN      | Meitei Mayek / Bengali script
# Santali        | sat-IN      | Ol Chiki script
# English        | en-IN       | Latin
# ─────────────────────────────────────────────────────────────────────────────

_LANG_ENGLISH  = "en-IN"
_LANG_MARATHI  = "mr-IN"
_LANG_HINDI    = "hi-IN"

# langdetect code → Sarvam BCP-47
# langdetect does not support all 23 languages — unmapped ones fall back to
# Devanagari heuristics or en-IN. Add new entries as langdetect coverage improves.
_LANGDETECT_MAP: dict[str, str] = {
    "mr": _LANG_MARATHI,
    "hi": _LANG_HINDI,
    "en": _LANG_ENGLISH,
    "ne": "ne-IN",    # Nepali
    "bn": "bn-IN",    # Bengali
    "gu": "gu-IN",    # Gujarati
    "kn": "kn-IN",    # Kannada
    "ml": "ml-IN",    # Malayalam
    "or": "od-IN",    # Odia  ← Sarvam uses od-IN, langdetect outputs "or"
    "pa": "pa-IN",    # Punjabi
    "ta": "ta-IN",    # Tamil
    "te": "te-IN",    # Telugu
    "ur": "ur-IN",    # Urdu
    "sa": "sa-IN",    # Sanskrit
    # Not yet reliably detected by langdetect (add when detection improves):
    # "as"  → "as-IN"    Assamese
    # "doi" → "doi-IN"   Dogri
    # "brx" → "brx-IN"   Bodo
    # "kok" → "kok-IN"   Konkani
    # "mai" → "mai-IN"   Maithili
    # "sd"  → "sd-IN"    Sindhi
    # "ks"  → "ks-IN"    Kashmiri
    # "mni" → "mni-IN"   Manipuri
    # "sat" → "sat-IN"   Santali
}

# Marathi-specific stopwords (subset) — used to disambiguate mr vs hi
_MR_STOPWORDS = {
    "आणि", "आहे", "हे", "की", "ते", "त्या", "च", "या", "व", "नाही",
    "तो", "ती", "हा", "ही", "मी", "तू", "आम्ही", "आपण", "त्यांना",
    "होते", "होता", "होती", "असे", "म्हणजे", "पण", "तर", "जर",
}

# Hindi-specific stopwords (subset)
_HI_STOPWORDS = {
    "और", "है", "में", "से", "को", "के", "का", "की", "एक", "यह",
    "इस", "वह", "नहीं", "हैं", "पर", "भी", "तो", "कि", "जो", "इन",
    "वे", "था", "थी", "थे", "हो", "आप", "हम", "मैं", "तुम", "वो",
}

# Minimum extracted characters before trusting content-based detection
_MIN_CONTENT_CHARS = 150


def _devanagari_ratio(text: str) -> float:
    """Return fraction of alpha characters that are Devanagari."""
    alpha = [c for c in text if unicodedata.category(c).startswith("L")]
    if not alpha:
        return 0.0
    deva = sum(1 for c in alpha if _DEVANAGARI_START <= ord(c) <= _DEVANAGARI_END)
    return deva / len(alpha)


def _disambiguate_mr_hi(text: str) -> str:
    """Return 'mr-IN' or 'hi-IN' based on stopword distribution.

    Used when Devanagari content is confirmed but langdetect is uncertain
    between Marathi and Hindi. Defaults to Marathi on tie (project primary).
    """
    words = set(text.split())
    mr_hits = len(words & _MR_STOPWORDS)
    hi_hits = len(words & _HI_STOPWORDS)
    return _LANG_MARATHI if mr_hits >= hi_hits else _LANG_HINDI


def _extract_pdf_text_sample(pdf_path: Path, pages: int = 5) -> str:
    """Extract text from the first N pages of a PDF using PyMuPDF.

    Returns an empty string if PyMuPDF is unavailable or extraction fails.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""

    try:
        doc = fitz.open(str(pdf_path))
        texts: list[str] = []
        for page_num in range(min(pages, len(doc))):
            page = doc[page_num]
            texts.append(page.get_text())
        doc.close()
        return "\n".join(texts)
    except Exception:
        return ""


def _langdetect_to_sarvam(text: str) -> str:
    """Run langdetect on text and map the result to a Sarvam BCP-47 code.

    For Devanagari text where langdetect returns 'mr' or 'hi', we run an
    additional stopword-based disambiguation since langdetect can confuse the
    two closely related languages.

    Falls back to 'en' for unrecognised language codes.
    """
    try:
        from langdetect import detect
        detected = detect(text)
    except Exception:
        return _LANG_ENGLISH

    # For Devanagari languages (mr/hi), cross-validate with stopwords
    if detected in ("mr", "hi"):
        deva_ratio = _devanagari_ratio(text)
        if deva_ratio > 0.1:
            return _disambiguate_mr_hi(text)

    return _LANGDETECT_MAP.get(detected, "en-IN")


def _detect_from_filename(stem: str) -> str:
    """Fallback: detect language from filename using Devanagari ratio.

    Used only when the PDF has no extractable text layer (fully scanned).
    """
    ratio = _devanagari_ratio(stem)
    if ratio > 0.1:
        return _disambiguate_mr_hi(stem)
    return _LANG_ENGLISH


def detect_sarvam_language(pdf_path: Path) -> tuple[str, str]:
    """Detect the Sarvam AI language code for a PDF.

    Detection order:
    1. Extract text from PDF content (first 5 pages) using PyMuPDF.
    2. If >= 150 chars found → langdetect + stopword disambiguation.
    3. If text is insufficient (image-only/scanned) → filename Unicode analysis
       with a printed warning.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Tuple of (sarvam_lang_code, detection_method) where method is one of
        "content", "filename_fallback".
    """
    text = _extract_pdf_text_sample(pdf_path, pages=5)

    if len(text.strip()) >= _MIN_CONTENT_CHARS:
        lang = _langdetect_to_sarvam(text)
        return lang, "content"

    # Not enough extractable text — scanned/image PDF
    lang = _detect_from_filename(pdf_path.stem)
    return lang, "filename_fallback"


# ── Sarvam AI Extraction ──────────────────────────────────────────────────────

def _get_sarvam_client(api_key: str):
    try:
        from sarvamai import SarvamAI
    except ImportError:
        print(
            "ERROR: sarvamai package not installed. Run: pip install sarvamai",
            file=sys.stderr,
        )
        sys.exit(1)
    return SarvamAI(api_subscription_key=api_key)


SARVAM_MAX_PAGES = 490  # Sarvam limit is 500; use 490 as safe margin


def _get_pdf_page_count(pdf_path: Path) -> int:
    """Return total page count of a PDF using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        count = len(doc)
        doc.close()
        return count
    except ImportError:
        return 0  # PyMuPDF not installed — assume within limit


def _split_pdf(pdf_path: Path, temp_dir: Path, max_pages: int = SARVAM_MAX_PAGES) -> list[tuple[int, Path]]:
    """Split a large PDF into ≤max_pages chunks.

    Returns list of (page_offset, part_pdf_path) tuples.
    page_offset is the 0-based index of the first page in that part.
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    total = len(doc)
    temp_dir.mkdir(parents=True, exist_ok=True)
    parts: list[tuple[int, Path]] = []

    for start in range(0, total, max_pages):
        end = min(start + max_pages, total) - 1  # fitz uses inclusive end
        part = fitz.open()
        part.insert_pdf(doc, from_page=start, to_page=end)
        part_path = temp_dir / f"part_{start:04d}_{end:04d}.pdf"
        part.save(str(part_path))
        part.close()
        parts.append((start, part_path))
        print(f"  SPLIT part {len(parts)}: pages {start + 1}–{end + 1} → {part_path.name}")

    doc.close()
    return parts


def _extract_part(
    part_path: Path,
    part_out_dir: Path,
    lang: str,
    api_key: str,
    part_num: int,
    total_parts: int,
) -> tuple[str, int]:
    """Extract a single PDF part via Sarvam. Returns (markdown_text, pages_processed)."""
    client = _get_sarvam_client(api_key)

    print(f"  JOB   Part {part_num}/{total_parts} — Creating Sarvam AI job…")
    sys.stdout.flush()
    job = client.document_intelligence.create_job(language=lang, output_format="md")

    size_mb = part_path.stat().st_size / 1024 / 1024
    print(f"  UP    Part {part_num}/{total_parts} — Uploading ({size_mb:.1f} MB)…")
    sys.stdout.flush()
    job.upload_file(str(part_path))

    print(f"  RUN   Part {part_num}/{total_parts} — Processing (may take several minutes)…")
    sys.stdout.flush()
    job.start()
    state = job.wait_until_complete()
    print(f"  DONE  Part {part_num}/{total_parts} — Job state: {state.job_state}")
    sys.stdout.flush()

    metrics = job.get_page_metrics()
    pages_processed = metrics.get("pages_processed", 0)

    zip_path = part_out_dir / "output.zip"
    part_out_dir.mkdir(parents=True, exist_ok=True)
    job.download_output(str(zip_path))

    md_text = ""
    metadata_dir = part_out_dir / "metadata"
    metadata_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".md"):
                md_text = zf.read(name).decode("utf-8")
            elif name.endswith(".json"):
                (metadata_dir / Path(name).name).write_bytes(zf.read(name))

    zip_path.unlink()
    return md_text, pages_processed


def extract_single(
    pdf_path: Path,
    output_dir: Path,
    lang: str,
    api_key: str,
    force: bool = False,
) -> bool:
    """Extract a single PDF with Sarvam AI.

    Automatically splits PDFs that exceed Sarvam's 500-page limit into
    ≤490-page chunks, extracts each part, and merges the markdown output.

    Args:
        pdf_path: Path to the input PDF.
        output_dir: Directory where document.md + metadata/ will be written.
        lang: Sarvam AI BCP-47 language code.
        api_key: Sarvam AI subscription key.
        force: If False, skip when document.md already exists.

    Returns:
        True on success, False on skip.
    """
    doc_md = output_dir / "document.md"
    if doc_md.exists() and not force:
        print(f"  SKIP  '{pdf_path.name}' — already extracted (use --force to re-run)")
        return False

    output_dir.mkdir(parents=True, exist_ok=True)

    total_pages = _get_pdf_page_count(pdf_path)
    print(f"  PAGES {total_pages} total pages")
    sys.stdout.flush()

    # ── Large PDF: split → extract each part → merge ───────────────────────
    if total_pages > 500:
        print(f"  SPLIT PDF exceeds 500-page Sarvam limit — splitting into chunks of {SARVAM_MAX_PAGES}…")
        sys.stdout.flush()

        temp_dir = output_dir / "_parts"
        parts = _split_pdf(pdf_path, temp_dir)
        total_parts = len(parts)

        merged_md_sections: list[str] = []
        total_pages_processed = 0
        metadata_dir = output_dir / "metadata"
        metadata_dir.mkdir(exist_ok=True)

        for i, (page_offset, part_path) in enumerate(parts, 1):
            part_out_dir = temp_dir / f"out_{i:02d}"
            md_text, pages_done = _extract_part(
                part_path, part_out_dir, lang, api_key, i, total_parts
            )
            merged_md_sections.append(md_text)
            total_pages_processed += pages_done

            # Copy per-page JSON metadata, renaming with global page numbers
            part_meta = part_out_dir / "metadata"
            if part_meta.is_dir():
                for jf in sorted(part_meta.glob("*.json")):
                    # Rename page_001.json → page_0491.json etc. using page_offset
                    try:
                        local_num = int(jf.stem.split("_")[-1])
                        global_num = local_num + page_offset
                        dest = metadata_dir / f"page_{global_num:04d}.json"
                    except ValueError:
                        dest = metadata_dir / jf.name
                    dest.write_bytes(jf.read_bytes())

            # Clean up temp part PDF
            part_path.unlink(missing_ok=True)

        # Merge markdown: join parts with page separator
        merged_md = "\n---\n".join(merged_md_sections)
        doc_md.write_text(merged_md, encoding="utf-8")
        print(f"  MD    document.md merged from {total_parts} parts ({doc_md.stat().st_size / 1024 / 1024:.1f} MB)")
        sys.stdout.flush()

        # Clean up temp directories
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

        info = {
            "source_pdf": str(pdf_path),
            "language": lang,
            "pages_processed": total_pages_processed,
            "total_pages": total_pages,
            "parts": total_parts,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        (output_dir / "extraction_info.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  OUT   {output_dir}")
        sys.stdout.flush()
        return True

    # ── Normal PDF: single Sarvam job ─────────────────────────────────────
    client = _get_sarvam_client(api_key)

    print(f"  LANG  {lang}")
    print(f"  JOB   Creating Sarvam AI job (lang={lang}, format=md)…")
    sys.stdout.flush()
    job = client.document_intelligence.create_job(
        language=lang,
        output_format="md",
    )

    print(f"  UP    Uploading {pdf_path.name} ({pdf_path.stat().st_size / 1024 / 1024:.1f} MB)…")
    sys.stdout.flush()
    job.upload_file(str(pdf_path))

    print("  RUN   Processing (this may take several minutes)…")
    sys.stdout.flush()
    job.start()
    state = job.wait_until_complete()
    print(f"  DONE  Job state: {state.job_state}")
    sys.stdout.flush()

    metrics = job.get_page_metrics()
    pages_processed = metrics.get("pages_processed", "unknown")
    print(f"  PAGES Pages processed: {pages_processed}")
    sys.stdout.flush()

    zip_path = output_dir / "output.zip"
    job.download_output(str(zip_path))

    # Unpack ZIP: document.md → output_dir, *.json → metadata/
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".md"):
                dest = output_dir / "document.md"
                dest.write_bytes(zf.read(name))
                print(f"  MD    document.md  ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
            elif name.endswith(".json"):
                dest = metadata_dir / Path(name).name
                dest.write_bytes(zf.read(name))

    zip_path.unlink()

    json_count = len(list(metadata_dir.glob("*.json")))
    print(f"  JSON  {json_count} page metadata files → metadata/")
    sys.stdout.flush()

    # Save extraction info for the ingestion pipeline
    info = {
        "source_pdf": str(pdf_path),
        "language": lang,
        "pages_processed": pages_processed,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "sarvam_job_state": str(state.job_state),
    }
    (output_dir / "extraction_info.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"  OUT   {output_dir}")
    sys.stdout.flush()
    return True


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract text from PDF books via Sarvam AI Document Intelligence.\n"
            "Language is detected from PDF content (PyMuPDF + langdetect). "
            "For image-only PDFs the filename Unicode script is used as fallback. "
            "Use --lang to override at any time."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input — mutually exclusive: single PDF or batch directory
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--pdf",
        metavar="PATH",
        help="Path to a single PDF file.",
    )
    input_group.add_argument(
        "--books-dir",
        metavar="DIR",
        help="Directory containing PDF files for batch extraction.",
    )

    parser.add_argument(
        "--output",
        metavar="DIR",
        default=None,
        help=(
            "Output directory for single-PDF mode. "
            "Defaults to 'data/extracted/<pdf_stem>/'."
        ),
    )
    parser.add_argument(
        "--output-base",
        metavar="DIR",
        default="data/extracted",
        help=(
            "Base directory for batch mode. Each book is written to "
            "<output-base>/<pdf_stem>/. "
            "Default: 'data/extracted'."
        ),
    )
    parser.add_argument(
        "--lang",
        metavar="CODE",
        default=None,
        help=(
            "Override auto-detected language with a Sarvam AI BCP-47 code "
            "(e.g. mr-IN, hi-IN, en). Skips all auto-detection when set."
        ),
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        default=None,
        help="Sarvam AI subscription key. Falls back to SARVAM_API_KEY env var.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even if document.md already exists.",
    )
    return parser.parse_args()


def _resolve_output_base(script_path: Path) -> Path:
    """Return the absolute path to 'data/extracted' from repo root."""
    repo_root = script_path.parent.parent  # scripts/ → repo root
    return repo_root / "data" / "extracted"


def _resolve_lang(pdf_path: Path, lang_override: str | None) -> str:
    """Resolve the Sarvam language code, printing detection details."""
    if lang_override:
        print(f"  LANG  {lang_override} (--lang override)")
        return lang_override

    lang, method = detect_sarvam_language(pdf_path)

    if method == "content":
        print(f"  LANG  {lang} (detected from PDF content)")
    else:
        print(
            f"  LANG  {lang} (filename fallback — no extractable text in PDF)",
            file=sys.stderr,
        )
        print(
            "        Use --lang to override if this is wrong.",
            file=sys.stderr,
        )
    return lang


def main() -> None:
    args = parse_args()

    api_key = args.api_key or os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print(
            "ERROR: Sarvam AI API key not found. "
            "Set SARVAM_API_KEY env var or pass --api-key.",
            file=sys.stderr,
        )
        sys.exit(1)

    script_path = Path(__file__).resolve()

    # ── Single PDF mode ──────────────────────────────────────────────────────
    if args.pdf:
        pdf_path = Path(args.pdf).resolve()
        if not pdf_path.exists():
            print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
            sys.exit(1)

        if args.output:
            output_dir = Path(args.output).resolve()
        else:
            output_dir = _resolve_output_base(script_path) / pdf_path.stem

        print(f"\n{'-' * 60}")
        print(f"  FILE  {pdf_path.name}")
        lang = _resolve_lang(pdf_path, args.lang)
        extract_single(pdf_path, output_dir, lang, api_key, force=args.force)
        print(f"{'-' * 60}\n")
        return

    # ── Batch mode ───────────────────────────────────────────────────────────
    books_dir = Path(args.books_dir).resolve()
    if not books_dir.is_dir():
        print(f"ERROR: Directory not found: {books_dir}", file=sys.stderr)
        sys.exit(1)

    pdfs = sorted(books_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in: {books_dir}")
        sys.exit(0)

    output_base = Path(args.output_base)
    if not output_base.is_absolute():
        output_base = (script_path.parent.parent / output_base).resolve()

    print(f"\nBatch extraction: {len(pdfs)} PDF(s) found in {books_dir}")
    print(f"Output base:      {output_base}\n")

    results: list[dict] = []
    for i, pdf_path in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf_path.name}")
        lang = _resolve_lang(pdf_path, args.lang)
        output_dir = output_base / pdf_path.stem

        try:
            success = extract_single(pdf_path, output_dir, lang, api_key, force=args.force)
            results.append({"pdf": pdf_path.name, "lang": lang, "status": "done" if success else "skipped"})
        except Exception as exc:
            print(f"  ERROR {exc}", file=sys.stderr)
            results.append({"pdf": pdf_path.name, "lang": lang, "status": f"error: {exc}"})
        print()

    # Summary
    print("-" * 60)
    print("Batch summary:")
    for r in results:
        icon = "✓" if r["status"] in ("done", "skipped") else "✗"
        print(f"  {icon}  [{r['lang']}]  {r['pdf']}  →  {r['status']}")
    print("-" * 60)


if __name__ == "__main__":
    main()
