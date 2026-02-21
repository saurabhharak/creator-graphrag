"""Text chunker for Sarvam AI extracted documents.

Splits document.md (pages separated by '\\n---\\n') into overlapping text
chunks with page provenance and section-title tracking.

Page separator: Sarvam AI uses '\\n---\\n' between pages in document.md.
Images are embedded as base64 data URIs and are stripped before chunking.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

# ── Language detection ─────────────────────────────────────────────────────────

_DEVANAGARI_START = 0x0900
_DEVANAGARI_END   = 0x097F

_MR_STOPWORDS = {
    "आणि", "आहे", "हे", "की", "ते", "त्या", "च", "या", "व", "नाही",
    "तो", "ती", "हा", "ही", "मी", "तू", "आम्ही", "आपण", "त्यांना",
    "होते", "होता", "होती", "असे", "म्हणजे", "पण", "तर", "जर",
}
_HI_STOPWORDS = {
    "और", "है", "में", "से", "को", "के", "का", "की", "एक", "यह",
    "इस", "वह", "नहीं", "हैं", "पर", "भी", "तो", "कि", "जो", "इन",
    "वे", "था", "थी", "थे", "हो", "आप", "हम", "मैं", "तुम", "वो",
}

# ── Chunk type heuristics ──────────────────────────────────────────────────────

_PROCESS_RE = re.compile(
    r"\b(step|steps|method|procedure|process|how to|instruction|guideline)\b",
    re.IGNORECASE,
)
_EVIDENCE_RE = re.compile(
    r"\b(according to|research|study|studies|evidence|found that|shows?|"
    r"demonstrated?|percent|%|result[s]?|data)\b",
    re.IGNORECASE,
)
_CONCEPT_RE = re.compile(
    r"\b(is defined as|refers? to|means?|concept of|definition|known as)\b",
    re.IGNORECASE,
)

# ── Markdown cleanup ───────────────────────────────────────────────────────────

# Strip inline base64 images: ![alt](data:image/...;base64,<data>)
# base64 alphabet never contains ')' so [^)]+ safely matches any length URI.
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:[^)]+\)", re.DOTALL)
# Strip residual HTML tags (e.g. <th>, <td> from Sarvam table output)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Strip markdown heading markers (#, ##, ###) but keep heading text
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
# Extract heading text for section tracking
_HEADING_EXTRACT_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


@dataclass
class TextChunk:
    text: str
    text_hash: str          # SHA-256 hex of utf-8 encoded text
    page_start: int
    page_end: int
    section_title: str | None
    chunk_type: str         # concept | process | evidence | general
    language_detected: str  # mr | hi | en | mixed | unknown
    language_confidence: float


# ── Internal helpers ───────────────────────────────────────────────────────────

def _devanagari_ratio(text: str) -> float:
    alpha = [c for c in text if unicodedata.category(c).startswith("L")]
    if not alpha:
        return 0.0
    deva = sum(1 for c in alpha if _DEVANAGARI_START <= ord(c) <= _DEVANAGARI_END)
    return deva / len(alpha)


def _detect_language(text: str) -> tuple[str, float]:
    """Return (lang_code, confidence). Codes: 'mr' | 'hi' | 'en' | 'mixed' | 'unknown'."""
    ratio = _devanagari_ratio(text)
    if ratio > 0.6:
        words = set(text.split())
        mr_hits = len(words & _MR_STOPWORDS)
        hi_hits = len(words & _HI_STOPWORDS)
        if mr_hits == 0 and hi_hits == 0:
            return "mr", 0.70   # default to Marathi (primary project language)
        total = mr_hits + hi_hits
        if mr_hits >= hi_hits:
            return "mr", round(0.5 + 0.5 * mr_hits / total, 2)
        return "hi", round(0.5 + 0.5 * hi_hits / total, 2)
    if ratio > 0.15:
        return "mixed", round(ratio, 2)
    if ratio < 0.05:
        return "en", round(1.0 - ratio * 4, 2)
    return "unknown", 0.5


def _classify_chunk_type(text: str) -> str:
    if _PROCESS_RE.search(text):
        return "process"
    if _EVIDENCE_RE.search(text):
        return "evidence"
    if _CONCEPT_RE.search(text):
        return "concept"
    return "general"


def _clean_page(raw: str) -> str:
    """Strip images, HTML, and normalize markdown from one page's text."""
    text = _IMAGE_RE.sub("", raw)
    text = _HTML_TAG_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _last_section_title(raw_page: str) -> str | None:
    headings = _HEADING_EXTRACT_RE.findall(raw_page)
    return headings[-1].strip() if headings else None


def _emit(
    out: list[TextChunk],
    text: str,
    page_start: int,
    page_end: int,
    section_title: str | None,
) -> None:
    clean = text.strip()
    if len(clean) < 50:
        return
    lang, conf = _detect_language(clean)
    out.append(
        TextChunk(
            text=clean,
            text_hash=hashlib.sha256(clean.encode("utf-8")).hexdigest(),
            page_start=page_start,
            page_end=page_end,
            section_title=section_title,
            chunk_type=_classify_chunk_type(clean),
            language_detected=lang,
            language_confidence=conf,
        )
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def chunk_document(
    document_md: str,
    max_chars: int = 2000,
    overlap_chars: int = 250,
    page_offset: int = 1,
) -> list[TextChunk]:
    """Split a Sarvam AI document.md into overlapping text chunks.

    Args:
        document_md: Full text of document.md (pages separated by '\\n---\\n').
        max_chars: Maximum characters per chunk.
        overlap_chars: Characters of overlap between consecutive chunks.
        page_offset: Page number of the first page (default 1).

    Returns:
        List of TextChunk objects ordered by page_start.
    """
    raw_pages = document_md.split("\n---\n")

    chunks: list[TextChunk] = []
    buffer = ""
    buffer_page_start = page_offset
    buffer_page_end = page_offset
    current_section: str | None = None

    for page_idx, raw_page in enumerate(raw_pages):
        page_num = page_offset + page_idx

        # Track section title (last ## heading on this page)
        title = _last_section_title(raw_page)
        if title:
            current_section = title

        page_text = _clean_page(raw_page)
        if not page_text:
            continue

        if buffer:
            buffer += "\n\n" + page_text
        else:
            buffer = page_text
            buffer_page_start = page_num
        buffer_page_end = page_num

        # Emit when buffer is full
        while len(buffer) >= max_chars:
            chunk_text = buffer[:max_chars]
            # Prefer breaking at paragraph boundary
            split_at = chunk_text.rfind("\n\n")
            if split_at > max_chars // 2:
                chunk_text = buffer[:split_at]

            _emit(chunks, chunk_text, buffer_page_start, buffer_page_end, current_section)

            # Slide with overlap
            slide = max(0, len(chunk_text) - overlap_chars)
            buffer = buffer[slide:]
            buffer_page_start = buffer_page_end  # approximation for overlapping window

    # Emit remainder
    if buffer.strip():
        _emit(chunks, buffer, buffer_page_start, buffer_page_end, current_section)

    return chunks
