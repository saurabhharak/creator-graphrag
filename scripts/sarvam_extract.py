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

# Sarvam AI BCP-47 language codes (all require -IN suffix)
_LANG_ENGLISH  = "en-IN"
_LANG_MARATHI  = "mr-IN"
_LANG_HINDI    = "hi-IN"

# langdetect code → Sarvam BCP-47
_LANGDETECT_MAP: dict[str, str] = {
    "mr": _LANG_MARATHI,
    "hi": _LANG_HINDI,
    "en": _LANG_ENGLISH,
    "ne": "ne-IN",   # Nepali
    "bn": "bn-IN",   # Bengali
    "gu": "gu-IN",   # Gujarati
    "kn": "kn-IN",   # Kannada
    "ml": "ml-IN",   # Malayalam
    "or": "or-IN",   # Odia
    "pa": "pa-IN",   # Punjabi
    "ta": "ta-IN",   # Tamil
    "te": "te-IN",   # Telugu
    "ur": "ur-IN",   # Urdu
    "sa": "sa-IN",   # Sanskrit
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


def extract_single(
    pdf_path: Path,
    output_dir: Path,
    lang: str,
    api_key: str,
    force: bool = False,
) -> bool:
    """Extract a single PDF with Sarvam AI.

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
    client = _get_sarvam_client(api_key)

    print(f"  LANG  {lang}")
    print(f"  JOB   Creating Sarvam AI job (lang={lang}, format=md)…")
    job = client.document_intelligence.create_job(
        language=lang,
        output_format="md",
    )

    print(f"  UP    Uploading {pdf_path.name} ({pdf_path.stat().st_size / 1024 / 1024:.1f} MB)…")
    job.upload_file(str(pdf_path))

    print("  RUN   Processing (this may take several minutes)…")
    job.start()
    state = job.wait_until_complete()
    print(f"  DONE  Job state: {state.job_state}")

    metrics = job.get_page_metrics()
    pages_processed = metrics.get("pages_processed", "unknown")
    print(f"  PAGES Pages processed: {pages_processed}")

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
