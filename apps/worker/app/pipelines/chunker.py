"""Text chunker for Sarvam AI extracted documents.

Splits document.md (pages separated by '\n---\n') into overlapping text
chunks with page provenance and section-title tracking.

Page separator: Sarvam AI uses '\n---\n' between pages in document.md.
Images are embedded as base64 data URIs and are stripped before chunking.
Sentence boundaries respect Devanagari dandā (।) and double-dandā (॥).

── Language detection ─────────────────────────────────────────────────────────
Internal language codes (ISO 639-1 without -IN suffix) returned by _detect_language():

  Code  | Language   | Script          | Detection method
  ──────┼────────────┼─────────────────┼───────────────────────────────────────
  en    | English    | Latin           | Devanagari ratio < 0.05
  mr    | Marathi    | Devanagari      | Devanagari ratio > 0.6 + Marathi stopwords
  hi    | Hindi      | Devanagari      | Devanagari ratio > 0.6 + Hindi stopwords
  sa    | Sanskrit   | Devanagari      | Devanagari ratio > 0.6 + dandā present + no mr/hi hits
  mixed | Mixed      | —               | Devanagari ratio 0.15–0.6
  unknown| Unknown   | —               | Devanagari ratio 0.05–0.15

Currently NOT auto-detected (Sarvam extracts them fine; chunker emits "unknown"):
  bn (Bengali), ta (Tamil), te (Telugu), gu (Gujarati), kn (Kannada),
  ml (Malayalam), pa (Punjabi), od (Odia), ur (Urdu), as (Assamese),
  ne (Nepali), doi (Dogri), brx (Bodo), kok (Konkani), mai (Maithili),
  sd (Sindhi), ks (Kashmiri), mni (Manipuri), sat (Santali)

To add detection for a new language:
  1. Add its distinctive stopwords as a set (e.g. _TA_STOPWORDS)
  2. Add a Unicode range check if the script is non-Devanagari
  3. Add a detection branch in _detect_language()
  4. Add a contextual prefix template in embedder.py build_context_prefix()

Full Sarvam AI language codes (all require -IN suffix when calling the API):
  hi-IN  mr-IN  bn-IN  ta-IN  te-IN  gu-IN  kn-IN  ml-IN  as-IN  ur-IN
  sa-IN  ne-IN  doi-IN brx-IN pa-IN  od-IN  kok-IN mai-IN sd-IN  ks-IN
  mni-IN sat-IN en-IN
  (Source: docs.sarvam.ai/api-reference-docs/getting-started/models/sarvam-vision)
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
# Sanskrit classical text indicators (common in Krishi-Parashara, Vriksha Ayurveda)
_SA_INDICATORS = {
    "च", "वा", "तु", "एव", "हि", "इति", "अथ", "यदा", "तदा",
    "कृषि", "भूमि", "बीज", "वृक्ष", "फल", "पुष्प", "धान्य",
}
# Dandā characters used as sentence terminators in Sanskrit/Devanagari texts
_DANDA_RE = re.compile(r"[।॥]")


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

# Indic equivalents for Marathi / Hindi chunk type classification
_PROCESS_RE_INDIC = re.compile(
    r"(विधि|प्रक्रिया|पद्धत|पद्धती|कसे|चरण|टप्पे|तरीका|विधान|कार्यपद्धती"
    r"|कृती|उपाय|पायरी|पद्धत)",
)
_EVIDENCE_RE_INDIC = re.compile(
    r"(संशोधन|अभ्यास|पुरावा|टक्के|परिणाम|आढळले|दाखवते|माहिती"
    r"|शोध|अनुसार|प्रमाणे|सिद्ध|तपास)",
)
_CONCEPT_RE_INDIC = re.compile(
    r"(म्हणजे|परिभाषा|ओळखले जाते|याचा अर्थ|संकल्पना|व्याख्या"
    r"|मतलब|परिभाषित|कहते हैं|जिसे|अर्थात)",
)


# ── Markdown cleanup ───────────────────────────────────────────────────────────

# Strip inline base64 images: ![alt](data:image/...;base64,<data>)
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
    language_detected: str  # mr | hi | en | sa | mixed | unknown
    language_confidence: float


# ── Internal helpers ───────────────────────────────────────────────────────────

def _devanagari_ratio(text: str) -> float:
    alpha = [c for c in text if unicodedata.category(c).startswith("L")]
    if not alpha:
        return 0.0
    deva = sum(1 for c in alpha if _DEVANAGARI_START <= ord(c) <= _DEVANAGARI_END)
    return deva / len(alpha)


def _detect_language(text: str) -> tuple[str, float]:
    """Detect the primary language of a text chunk.

    Returns (lang_code, confidence) where lang_code is a 2-3 char ISO code
    WITHOUT the -IN suffix (e.g. "mr", not "mr-IN").

    Supported output codes:
      "en"      English          — Devanagari ratio < 0.05
      "mr"      Marathi          — Devanagari + Marathi stopword hits
      "hi"      Hindi            — Devanagari + Hindi stopword hits
      "sa"      Sanskrit         — Devanagari + dandā (।) + no mr/hi stopwords
      "mixed"   Mixed script     — Devanagari ratio 0.15–0.60
      "unknown" Unclassified     — Devanagari ratio 0.05–0.15

    All other Sarvam-supported languages (bn, ta, te, gu, kn, ml, pa, od, ur,
    as, ne, doi, brx, kok, mai, sd, ks, mni, sat) return "unknown" until their
    script detection is added here. See module docstring for how to extend.

    Sanskrit detection heuristic:
      - High Devanagari ratio (> 0.6)
      - Neither Hindi nor Marathi stopwords present
      - Contains dandā (।) — the classical verse terminator
      - OR contains ≥ 2 known Sanskrit agricultural indicator words
    """
    ratio = _devanagari_ratio(text)
    if ratio > 0.6:
        words = set(text.split())
        mr_hits = len(words & _MR_STOPWORDS)
        hi_hits = len(words & _HI_STOPWORDS)
        sa_hits = len(words & _SA_INDICATORS)
        has_danda = bool(_DANDA_RE.search(text))

        # Sanskrit: dandā present + no strong Hindi/Marathi matches
        if has_danda and mr_hits == 0 and hi_hits == 0:
            return "sa", 0.82
        # Sanskrit fallback: SA indicators present, no Hindi/Marathi matches
        if sa_hits >= 2 and mr_hits == 0 and hi_hits == 0:
            return "sa", 0.75

        if mr_hits == 0 and hi_hits == 0:
            return "mr", 0.70   # default Devanagari → Marathi (primary project lang)
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
    """Classify chunk by content type. Checks Indic patterns when text is Devanagari."""
    deva_ratio = _devanagari_ratio(text)
    if deva_ratio > 0.4:
        # Prefer Indic pattern matching for Devanagari-heavy text
        if _PROCESS_RE_INDIC.search(text):
            return "process"
        if _EVIDENCE_RE_INDIC.search(text):
            return "evidence"
        if _CONCEPT_RE_INDIC.search(text):
            return "concept"
    # English / mixed / fallback
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


def _find_split_point(buffer: str, max_chars: int) -> int:
    """Find the best character index to split the buffer at.

    Priority:
    1. Paragraph break (\\n\\n) in second half of buffer
    2. Dandā (।) or double-dandā (॥) — Sanskrit/Indic verse boundary
    3. Period/newline — English sentence boundary
    4. Hard split at max_chars
    """
    candidate = buffer[:max_chars]

    # 1. Paragraph boundary
    split_at = candidate.rfind("\n\n")
    if split_at > max_chars // 2:
        return split_at

    # 2. Dandā (Devanagari verse/sentence terminator)
    danda_match = None
    for m in _DANDA_RE.finditer(candidate):
        if m.start() > max_chars // 2:
            danda_match = m
    if danda_match:
        return danda_match.end()

    # 3. English sentence boundary
    split_at = candidate.rfind(". ")
    if split_at > max_chars // 2:
        return split_at + 1  # include the period

    return max_chars


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

# Splits a raw page on ## / ### header lines while capturing each header.
# re.split with a captured group returns [before, header1, text1, header2, text2, ...]
_RAW_HEADER_SPLIT_RE = re.compile(r"^(#{1,3} .+)$", re.MULTILINE)
# Extracts the title text from a raw header line (e.g. "## Soil Prep" → "Soil Prep")
_RAW_HEADER_TITLE_RE = re.compile(r"^#{1,3} (.+)$")


def _emit_section(
    out: list[TextChunk],
    body: str,
    page_start: int,
    page_end: int,
    title: str | None,
    max_chars: int,
    overlap_chars: int,
) -> None:
    """Emit one or more TextChunks for a section body.

    Emits a single chunk when ``body`` fits within ``max_chars``.
    Splits into overlapping sub-chunks (dandā-aware) when too large.
    """
    if not body.strip():
        return
    if len(body) <= max_chars:
        _emit(out, body, page_start, page_end, title)
    else:
        buf = body
        while len(buf) >= max_chars:
            split_at = _find_split_point(buf, max_chars)
            _emit(out, buf[:split_at], page_start, page_end, title)
            slide = max(0, split_at - overlap_chars)
            buf = buf[slide:]
        if buf.strip():
            _emit(out, buf, page_start, page_end, title)


def chunk_document_by_headers(
    document_md: str,
    max_chars: int = 6000,
    overlap_chars: int = 200,
    page_offset: int = 1,
) -> list[TextChunk]:
    """Split a Sarvam AI document.md using ## section headers as chunk boundaries.

    Primary strategy: one TextChunk per ## / ### section.  When a section
    exceeds ``max_chars``, it is further split with the same character-based
    logic used by ``chunk_document`` (dandā-aware for Indic text).

    Falls back to ``chunk_document`` when no ## headers are found.

    Key implementation detail
    -------------------------
    ``_clean_page`` strips the ``##`` markers (they are Markdown heading tokens,
    not document content).  Header detection therefore operates on the *raw*
    page text **before** cleaning.  Each raw page is split by its ``##`` headers
    using ``re.split`` with a captured group; the title is extracted from the raw
    header line, then the body text segment is cleaned and accumulated under the
    current section.

    Advantages over ``chunk_document``
    -----------------------------------
    - Chunks align to semantic section boundaries, not arbitrary character counts.
    - ``section_title`` is the actual header of the section, not a stale heading
      from a page that happened to remain in the sliding-window buffer.
    - Avoids splitting mid-paragraph across unrelated topics.
    - Better KU extraction: each chunk covers exactly one section topic.

    Args:
        document_md:   Full text of document.md (pages separated by ``\\n---\\n``).
        max_chars:     Hard cap per chunk; large sections are split further.
        overlap_chars: Overlap characters when a section is split.
        page_offset:   Page number of the first page (default 1).

    Returns:
        List of TextChunk objects ordered by page_start.
    """
    raw_pages = document_md.split("\n---\n")

    chunks: list[TextChunk] = []

    # Current section accumulator
    current_title: str | None = None
    current_body: str = ""
    section_page_start: int = page_offset
    section_page_end: int = page_offset
    found_any_header = False

    for page_idx, raw_page in enumerate(raw_pages):
        page_num = page_offset + page_idx

        # Split this raw page on ## headers BEFORE cleaning so markers survive.
        # Result: [preamble, header_line, body_text, header_line, body_text, ...]
        parts = _RAW_HEADER_SPLIT_RE.split(raw_page)

        if len(parts) == 1:
            # No headers on this page — append clean text to current section.
            clean = _clean_page(raw_page)
            if clean:
                current_body = (current_body + "\n\n" + clean) if current_body else clean
                if not current_body or current_body == clean:
                    section_page_start = page_num
                section_page_end = page_num
        else:
            found_any_header = True

            # Preamble before the first header on this page
            preamble = _clean_page(parts[0])
            if preamble:
                current_body = (current_body + "\n\n" + preamble) if current_body else preamble
                section_page_end = page_num

            # Process (header_line, body_text) pairs
            i = 1
            while i < len(parts):
                raw_header = parts[i]
                raw_body = parts[i + 1] if i + 1 < len(parts) else ""

                # Emit whatever we had accumulated before this header
                _emit_section(
                    chunks, current_body,
                    section_page_start, section_page_end,
                    current_title, max_chars, overlap_chars,
                )

                # Start new section
                m = _RAW_HEADER_TITLE_RE.match(raw_header)
                current_title = m.group(1).strip() if m else raw_header.strip()
                current_body = _clean_page(raw_body)
                section_page_start = page_num
                section_page_end = page_num

                i += 2

    # Emit the final accumulated section
    _emit_section(
        chunks, current_body,
        section_page_start, section_page_end,
        current_title, max_chars, overlap_chars,
    )

    if not found_any_header:
        # No ## headers anywhere — fall back to character-based chunker
        return chunk_document(document_md, max_chars, overlap_chars, page_offset)

    return chunks


def chunk_document(
    document_md: str,
    max_chars: int = 2000,
    overlap_chars: int = 250,
    page_offset: int = 1,
) -> list[TextChunk]:
    """Split a Sarvam AI document.md into overlapping text chunks.

    Args:
        document_md: Full text of document.md (pages separated by '\n---\n').
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
            split_at = _find_split_point(buffer, max_chars)
            chunk_text = buffer[:split_at]

            _emit(chunks, chunk_text, buffer_page_start, buffer_page_end, current_section)

            # Slide with overlap — respect dandā boundaries for Indic text
            slide = max(0, split_at - overlap_chars)
            buffer = buffer[slide:]
            buffer_page_start = buffer_page_end  # approximation for overlapping window

    # Emit remainder
    if buffer.strip():
        _emit(chunks, buffer, buffer_page_start, buffer_page_end, current_section)

    return chunks
