"""Fix incomplete PDF extractions.

Fix 1 — AgriHistory1:
  Part 1 (pages 1-490) metadata JSONs already exist. Reconstruct markdown
  from the per-page block JSON, then extract part 2 (88 pages) via Sarvam,
  merge, and write document.md + extraction_info.json.

Fix 2 — Agriculture and Agriculturists in Ancient India:
  The PDF was already extracted under the 2015.62133 folder name. Re-run
  to populate the cleaner-named output directory.

Usage:
    python scripts/fix_incomplete_extractions.py [--api-key KEY]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ── Markdown reconstruction from per-page block JSON ──────────────────────────

# Map Sarvam layout_tag → markdown prefix/style
_TAG_SKIP = {"footer", "header", "page-number"}

def _blocks_to_markdown(blocks: list[dict]) -> str:
    """Convert a list of Sarvam block dicts (one page) to markdown text."""
    # Sort by reading_order; fall back to block_id order
    blocks = sorted(blocks, key=lambda b: (b.get("reading_order", 9999), b.get("block_id", "")))

    lines: list[str] = []
    for block in blocks:
        tag = block.get("layout_tag", "paragraph")
        text = block.get("text", "").strip()
        if not text:
            continue
        if tag in _TAG_SKIP:
            continue

        if tag == "section-title":
            lines.append(f"## {text}")
        elif tag == "headline":
            lines.append(f"### {text}")
        elif tag in ("image", "chart"):
            lines.append(f"*{text}*")
        elif tag == "image-caption":
            lines.append(f"*{text}*")
        elif tag == "footnote":
            lines.append(f"[^fn]: {text}")
        elif tag in ("reference", "sidebar"):
            lines.append(f"> {text}")
        else:
            # paragraph, list, list-item, table, etc.
            lines.append(text)

    return "\n\n".join(lines)


def reconstruct_markdown_from_metadata(metadata_dir: Path) -> tuple[str, int]:
    """Reconstruct combined markdown from all per-page JSON files in metadata_dir.

    Returns (markdown_text, page_count).
    """
    page_files = sorted(metadata_dir.glob("page_*.json"))
    if not page_files:
        raise FileNotFoundError(f"No page_*.json files found in {metadata_dir}")

    pages_md: list[str] = []
    for jf in page_files:
        with open(jf, encoding="utf-8") as f:
            page_data = json.load(f)
        blocks = page_data.get("blocks", [])
        page_md = _blocks_to_markdown(blocks)
        if page_md.strip():
            pages_md.append(page_md)

    combined = "\n\n---\n\n".join(pages_md)
    return combined, len(page_files)


# ── Sarvam extraction (single part) ──────────────────────────────────────────

def extract_via_sarvam(
    part_path: Path,
    part_out_dir: Path,
    lang: str,
    api_key: str,
    label: str = "part",
) -> tuple[str, int, list[Path]]:
    """Extract a PDF part via Sarvam AI.

    Returns (markdown_text, pages_processed, list_of_metadata_json_paths).
    """
    try:
        from sarvamai import SarvamAI
    except ImportError:
        print("ERROR: sarvamai package not installed. Run: pip install sarvamai", file=sys.stderr)
        sys.exit(1)

    client = SarvamAI(api_subscription_key=api_key)

    print(f"  JOB   [{label}] Creating Sarvam AI job (lang={lang})…")
    sys.stdout.flush()
    job = client.document_intelligence.create_job(language=lang, output_format="md")

    size_mb = part_path.stat().st_size / 1024 / 1024
    print(f"  UP    [{label}] Uploading {part_path.name} ({size_mb:.1f} MB)…")
    sys.stdout.flush()
    job.upload_file(str(part_path))

    print(f"  RUN   [{label}] Processing (may take several minutes)…")
    sys.stdout.flush()
    job.start()
    state = job.wait_until_complete()
    print(f"  DONE  [{label}] Job state: {state.job_state}")
    sys.stdout.flush()

    metrics = job.get_page_metrics()
    pages_processed = metrics.get("pages_processed", 0)

    zip_path = part_out_dir / "output.zip"
    part_out_dir.mkdir(parents=True, exist_ok=True)
    job.download_output(str(zip_path))

    md_text = ""
    meta_dir = part_out_dir / "metadata"
    meta_dir.mkdir(exist_ok=True)
    meta_files: list[Path] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".md"):
                md_text = zf.read(name).decode("utf-8")
            elif name.endswith(".json"):
                dest = meta_dir / Path(name).name
                dest.write_bytes(zf.read(name))
                meta_files.append(dest)

    zip_path.unlink()
    return md_text, pages_processed, meta_files


# ── Fix 1: AgriHistory1 ───────────────────────────────────────────────────────

def fix_agrihistory1(output_dir: Path, api_key: str) -> None:
    print("\n" + "=" * 60)
    print("FIX 1: AgriHistory1")
    print("=" * 60)

    metadata_dir = output_dir / "metadata"
    parts_dir = output_dir / "_parts"
    part2_pdf = parts_dir / "part_0490_0577.pdf"
    doc_md = output_dir / "document.md"
    info_json = output_dir / "extraction_info.json"

    if not metadata_dir.is_dir():
        print(f"ERROR: metadata dir not found: {metadata_dir}", file=sys.stderr)
        sys.exit(1)
    if not part2_pdf.exists():
        print(f"ERROR: part 2 PDF not found: {part2_pdf}", file=sys.stderr)
        sys.exit(1)

    # Step 1: Reconstruct part 1 markdown from existing metadata JSONs
    print("\n[Step 1] Reconstructing part 1 markdown from 488 metadata JSON files…")
    part1_md, part1_pages = reconstruct_markdown_from_metadata(metadata_dir)
    print(f"  Reconstructed: {part1_pages} pages, {len(part1_md)//1024}KB")

    # Step 2: Extract part 2 via Sarvam
    print("\n[Step 2] Extracting part 2 via Sarvam AI…")
    part2_out = parts_dir / "out_02"
    part2_md, part2_pages, part2_meta_files = extract_via_sarvam(
        part2_pdf, part2_out, lang="en-IN", api_key=api_key, label="part2/2"
    )

    # Step 3: Copy part 2 metadata to global metadata dir with offset
    print("\n[Step 3] Copying part 2 metadata with global page numbers…")
    PAGE_OFFSET = 490  # part 2 starts at global page 490
    for jf in sorted(part2_meta_files):
        try:
            local_num = int(jf.stem.split("_")[-1])
            global_num = local_num + PAGE_OFFSET
            dest = metadata_dir / f"page_{global_num:04d}.json"
        except ValueError:
            dest = metadata_dir / jf.name
        dest.write_bytes(jf.read_bytes())
    print(f"  Copied {len(part2_meta_files)} metadata files (global offset +{PAGE_OFFSET})")

    # Step 4: Merge and write document.md
    print("\n[Step 4] Merging parts and writing document.md…")
    merged = part1_md + "\n\n---\n\n" + part2_md
    doc_md.write_text(merged, encoding="utf-8")
    print(f"  document.md written ({doc_md.stat().st_size // 1024}KB)")

    # Step 5: Write extraction_info.json
    info = {
        "source_pdf": str(output_dir.parent / "Books" / "AgriHistory1.pdf"),
        "language": "en-IN",
        "pages_processed": part1_pages + part2_pages,
        "total_pages": part1_pages + part2_pages,
        "parts": 2,
        "part1_reconstructed_from_metadata": True,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    info_json.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"  extraction_info.json written")

    print("\nAgriHistory1 DONE!")


# ── Fix 2: Agriculture and Agriculturists in Ancient India ───────────────────

def fix_agriculture_and_agriculturists(output_dir: Path, pdf_path: Path, api_key: str) -> None:
    print("\n" + "=" * 60)
    print("FIX 2: Agriculture and Agriculturists in Ancient India")
    print("=" * 60)

    doc_md = output_dir / "document.md"
    if doc_md.exists():
        print(f"  SKIP  document.md already exists at {output_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  PDF:  {pdf_path.name}")
    print(f"  OUT:  {output_dir}")
    print(f"  SIZE: {pdf_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Use sarvam_extract.py's extract_single directly
    sys.path.insert(0, str(pdf_path.parent.parent.parent / "scripts"))
    from sarvam_extract import extract_single
    extract_single(pdf_path, output_dir, lang="en-IN", api_key=api_key, force=False)

    print("\nAgriculture and Agriculturists in Ancient India DONE!")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Fix incomplete PDF extractions.")
    parser.add_argument("--api-key", default=None, help="Sarvam AI subscription key.")
    parser.add_argument(
        "--fix",
        choices=["agrihistory1", "agriculture", "all"],
        default="all",
        help="Which fix to run (default: all).",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print("ERROR: Set SARVAM_API_KEY env var or pass --api-key.", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    extracted_base = repo_root / "data" / "extracted"
    books_dir = repo_root / "data" / "Books"

    if args.fix in ("agrihistory1", "all"):
        fix_agrihistory1(
            output_dir=extracted_base / "AgriHistory1",
            api_key=api_key,
        )

    if args.fix in ("agriculture", "all"):
        fix_agriculture_and_agriculturists(
            output_dir=extracted_base / "Agriculture and Agriculturists in Ancient India",
            pdf_path=books_dir / "2015.62133.Agriculture-And-Agriculturists-In-Ancient-India1932.pdf",
            api_key=api_key,
        )

    print("\n" + "=" * 60)
    print("All fixes complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
