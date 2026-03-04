"""Generate draft golden-facts JSONL files from PDFs in data/Books.

This is a bootstrap utility to create 20 candidate facts per book so the
annotation team can review/refine quickly.
"""
from __future__ import annotations

import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# Ensure Unicode output is readable on Windows terminals.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


BOOKS: list[dict[str, str]] = [
    {
        "filename": "Introduction to Natural Farming.pdf",
        "slug": "introduction-to-natural-farming",
        "code": "NF",
        "language": "en",
    },
    {
        "filename": "An agricultural testament.pdf",
        "slug": "an-agricultural-testament",
        "code": "AT",
        "language": "en",
    },
    {
        "filename": "2015.62133.Agriculture-And-Agriculturists-In-Ancient-India1932.pdf",
        "slug": "agriculture-ancient-india",
        "code": "AHI",
        "language": "en",
    },
    {
        "filename": "AgriHistory1.pdf",
        "slug": "agrihistory-vol1",
        "code": "AGH1",
        "language": "en",
    },
    {
        "filename": "AgriHistory2.pdf",
        "slug": "agrihistory-vol2",
        "code": "AGH2",
        "language": "en",
    },
    {
        "filename": "AgriHistory3.pdf",
        "slug": "agrihistory-vol3",
        "code": "AGH3",
        "language": "en",
    },
    {
        "filename": "handbookofindian00mukerich.pdf",
        "slug": "handbook-indian-agriculture",
        "code": "HI",
        "language": "en",
    },
    {
        "filename": "IITKA_Book_Traditional-Knowledge-in-Agriculture-English_0_0.pdf",
        "slug": "iitka-traditional-knowledge",
        "code": "IITK",
        "language": "en",
    },
    {
        "filename": "Inventory-of-Indigenous-Technical-Knowledge-in-Agriculture-Document-1.pdf",
        "slug": "inventory-itk-vol1",
        "code": "ITK1",
        "language": "en",
    },
    {
        "filename": "Inventory-of-Indigenous-Technical-Knowledge-in-Agriculture-Documen-2.1.pdf",
        "slug": "inventory-itk-vol2",
        "code": "ITK2",
        "language": "en",
    },
    {
        "filename": "Krishi_Parashar.pdf",
        "slug": "krishi-parashar",
        "code": "KP",
        "language": "sa",
    },
    {
        "filename": "Natural Farming for Sustainable Agriculture.pdf",
        "slug": "natural-farming-sustainable-agriculture",
        "code": "NFSA",
        "language": "en",
    },
    {
        "filename": "Technical-Manual-on-Natural_Farming_10.03.2025.pdf",
        "slug": "technical-manual-natural-farming",
        "code": "TM",
        "language": "en",
    },
    {
        "filename": "Vriksha Ayurveda of Surapala Nalini Sadhale 1996.pdf",
        "slug": "vriksha-ayurveda",
        "code": "VA",
        "language": "sa",
    },
]

EXTRACTED_FALLBACK: dict[str, str] = {
    "An agricultural testament.pdf": "data/extracted/An agricultural testament/document.md",
    "Introduction to Natural Farming.pdf": "data/extracted/Introduction to Natural Farming/document.md",
}


UNITS = (
    "kg",
    "g",
    "litre",
    "liter",
    "ml",
    "ton",
    "acre",
    "hectare",
    "day",
    "days",
    "hour",
    "hours",
    "%",
)

VERB_HINTS = (
    "increase",
    "increases",
    "increased",
    "reduce",
    "reduces",
    "improve",
    "improves",
    "enhance",
    "enhances",
    "should",
    "must",
    "recommended",
    "apply",
    "applied",
    "prepared",
    "ferment",
    "compost",
    "compared",
    "versus",
    "means",
    "defined as",
    "is",
    "are",
)

SKIP_PHRASES = (
    "table of contents",
    "copyright",
    "all rights reserved",
    "isbn",
    "www.",
    "http://",
    "https://",
    "chapter",
    "bibliography",
    "references",
    "index",
)

REQUIRES_OBJECT = {"claim", "comparison", "prescription"}
NULL_OBJECT_TYPES = {"definition", "observation", "principle"}


@dataclass
class Candidate:
    page: int
    sentence: str
    score: float


def _clean_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    raw = re.split(r"(?<=[\.\?\!;:])\s+", text)
    out: list[str] = []
    for s in raw:
        s = s.strip(" -\t\r\n")
        if s:
            out.append(s)
    return out


def _looks_like_sentence(s: str) -> bool:
    if len(s) < 60 or len(s) > 330:
        return False
    words = s.split()
    if len(words) < 8 or len(words) > 65:
        return False
    lower = s.lower()
    if any(x in lower for x in SKIP_PHRASES):
        return False
    # Avoid lines that are mostly non-alphabetic.
    alpha = sum(1 for c in s if c.isalpha())
    if alpha < len(s) * 0.45:
        return False
    return True


def _score_sentence(s: str) -> float:
    lower = s.lower()
    score = 0.0
    if re.search(r"\d", s):
        score += 2.0
    if any(u in lower for u in UNITS):
        score += 1.5
    if any(v in lower for v in VERB_HINTS):
        score += 2.0
    if any(w in lower for w in ("soil", "crop", "seed", "water", "yield", "pest", "disease")):
        score += 1.0
    if any(w in lower for w in ("compared", "versus", "than")):
        score += 1.0
    if any(w in lower for w in ("may", "might", "possibly")):
        score -= 0.5
    return score


def _infer_type(s: str) -> str:
    lower = s.lower()
    if any(k in lower for k in ("compared", "versus", "than")):
        return "comparison"
    if any(k in lower for k in ("should", "must", "recommended", "advised", "apply")):
        return "prescription"
    if any(k in lower for k in ("is defined as", "means", "is known as", "is called")):
        return "definition"
    if any(k in lower for k in ("prepared by", "method", "process", "mix", "ferment", "composting")):
        return "practice"
    if any(k in lower for k in ("increases", "reduces", "improves", "enhances", "leads to", "results in")):
        return "claim"
    if any(k in lower for k in ("kharif", "rabi", "monsoon", "summer", "winter", "rainfall")):
        return "observation"
    if any(k in lower for k in ("principle", "philosophy", "law of return")):
        return "principle"
    return "claim"


def _infer_domain_type(s: str) -> str:
    lower = s.lower()
    if any(k in lower for k in ("soil", "humus", "organic matter")):
        return "soil"
    if any(k in lower for k in ("rain", "irrigation", "water")):
        return "water"
    if any(k in lower for k in ("pest", "disease", "fungus", "insect", "weed")):
        return "pest"
    if any(k in lower for k in ("seed", "crop", "wheat", "rice", "millet", "cotton", "pulse")):
        return "crop"
    if any(k in lower for k in ("manure", "dung", "urine", "fertilizer", "fertiliser", "jaggery", "compost")):
        return "input_material"
    if any(k in lower for k in ("kharif", "rabi", "summer", "winter", "season", "month", "monsoon")):
        return "season"
    if any(k in lower for k in ("method", "practice", "process", "farming", "cultivation", "composting")):
        return "practice"
    if any(k in lower for k in ("india", "state", "region", "district", "zone")):
        return "region"
    return "general"


def _normalize_phrase(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip(" ,;:-")
    return text


def _extract_spo(sentence: str, fact_type: str) -> tuple[str, str, str | None]:
    s = sentence.strip()
    low = s.lower()
    markers = [
        " is defined as ",
        " is known as ",
        " is called ",
        " is ",
        " are ",
        " should be ",
        " should ",
        " must be ",
        " must ",
        " can ",
        " may ",
        " increases ",
        " reduces ",
        " improves ",
        " enhances ",
        " leads to ",
        " results in ",
        " compared to ",
        " versus ",
        " than ",
        " prepared by ",
        " applied to ",
        " used for ",
        " consists of ",
        " contains ",
    ]

    hit = None
    idx = -1
    for m in markers:
        idx = low.find(m)
        if idx > 1:
            hit = m.strip()
            break

    if hit is None:
        words = s.split()
        subject = _normalize_phrase(" ".join(words[:4]), 90)
        predicate = "states"
        obj = _normalize_phrase(" ".join(words[4:]), 220) or None
    else:
        subject = _normalize_phrase(s[:idx], 90)
        predicate = _normalize_phrase(hit, 50) or "states"
        obj = _normalize_phrase(s[idx + len(hit) + 2 :], 220)  # +2 compensates for edge spaces
        if not obj:
            obj = None

    if not subject:
        subject = _normalize_phrase(s.split()[0], 90)

    if fact_type in NULL_OBJECT_TYPES:
        obj = None
    elif fact_type in REQUIRES_OBJECT and obj is None:
        obj = _normalize_phrase(s, 220)

    return subject, predicate, obj


def _extract_conditions(sentence: str) -> str | None:
    lower = sentence.lower()
    # Keep dosage/timing markers as conditions.
    m = re.search(
        r"(during|before|after|within|for|at|in)\s+[^.]{0,90}(\d+[^.]{0,40}|kharif|rabi|monsoon|summer|winter)",
        lower,
    )
    if not m:
        return None
    cond = _normalize_phrase(sentence[m.start() : m.end()], 120)
    return cond or None


def _confidence(sentence: str, language: str) -> float:
    lower = sentence.lower()
    if language == "sa":
        if any(w in lower for w in ("may", "might", "can")):
            return 0.65
        return 0.70
    if any(w in lower for w in ("may", "might", "possibly", "can")):
        return 0.65
    if any(w in lower for w in ("approximately", "around", "about")):
        return 0.70
    return 0.80


def _collect_candidates(pdf_path: Path) -> list[Candidate]:
    reader = PdfReader(str(pdf_path))
    candidates: list[Candidate] = []
    for i, page in enumerate(reader.pages, start=1):
        text = _clean_text(page.extract_text() or "")
        if not text:
            continue
        for sent in _split_sentences(text):
            if not _looks_like_sentence(sent):
                continue
            sent = _normalize_phrase(sent, 300)
            score = _score_sentence(sent)
            if score >= 2.0:
                candidates.append(Candidate(page=i, sentence=sent, score=score))
    return candidates


def _collect_candidates_from_markdown(doc_path: Path) -> list[Candidate]:
    raw = doc_path.read_text(encoding="utf-8", errors="replace")
    pages = raw.split("\n---\n")
    candidates: list[Candidate] = []

    for page_num, page_raw in enumerate(pages, start=1):
        # Remove embedded base64 image payloads from Sarvam markdown.
        page_raw = re.sub(r"!\[Image\]\(data:image/[^\)]*\)", " ", page_raw)
        page_raw = re.sub(r"\*The image is.*?\*", " ", page_raw, flags=re.IGNORECASE | re.DOTALL)
        text = _clean_text(page_raw)
        if not text:
            continue
        for sent in _split_sentences(text):
            if not _looks_like_sentence(sent):
                continue
            sent = _normalize_phrase(sent, 300)
            score = _score_sentence(sent)
            if score >= 2.0:
                candidates.append(Candidate(page=page_num, sentence=sent, score=score))

    return candidates


def _select_20(candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        return []
    # De-duplicate near-identical snippets.
    seen: set[str] = set()
    deduped: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: (-x.score, x.page)):
        key = re.sub(r"\W+", "", c.sentence.lower())[:200]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    # First pass: one per page to maximize spread.
    per_page_seen: set[int] = set()
    selected: list[Candidate] = []
    for c in deduped:
        if c.page in per_page_seen:
            continue
        selected.append(c)
        per_page_seen.add(c.page)
        if len(selected) == 20:
            return selected

    # Second pass: fill to 20 by score.
    for c in deduped:
        if len(selected) == 20:
            break
        if c in selected:
            continue
        selected.append(c)

    return selected[:20]


def _build_fact(book: dict[str, str], idx: int, c: Candidate) -> dict:
    fact_type = _infer_type(c.sentence)
    subject, predicate, obj = _extract_spo(c.sentence, fact_type)
    fact = {
        "fact_id": f"{book['language']}-{book['code']}-{idx:03d}",
        "book_slug": book["slug"],
        "language": book["language"],
        "type": fact_type,
        "domain_type": _infer_domain_type(c.sentence),
        "subject": subject,
        "predicate": _normalize_phrase(predicate, 50) or "states",
        "object": obj,
        "conditions": _extract_conditions(c.sentence),
        "verbatim_snippet": _normalize_phrase(c.sentence, 300),
        "page_numbers": [c.page],
        "confidence_floor": _confidence(c.sentence, book["language"]),
        "notes": "Auto-generated from PDF text; review and refine manually.",
    }
    return fact


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    books_dir = repo_root / "data" / "Books"
    out_dir = repo_root / "tests" / "golden_facts"

    generated = 0
    for book in BOOKS:
        pdf_path = books_dir / book["filename"]
        if not pdf_path.exists():
            print(f"[WARN] Missing PDF: {pdf_path}")
            continue

        print(f"[INFO] Reading: {pdf_path.name}")
        candidates = _collect_candidates(pdf_path)
        if len(candidates) < 20:
            fallback_rel = EXTRACTED_FALLBACK.get(book["filename"])
            if fallback_rel:
                fallback_doc = repo_root / fallback_rel
                if fallback_doc.exists():
                    print(f"[INFO] Using extracted markdown fallback: {fallback_doc.relative_to(repo_root)}")
                    candidates.extend(_collect_candidates_from_markdown(fallback_doc))

        selected = _select_20(candidates)
        if len(selected) < 20:
            print(f"[WARN] Only found {len(selected)} candidates for {book['slug']}")

        facts = [_build_fact(book, i + 1, c) for i, c in enumerate(selected)]

        out_path = out_dir / f"{book['slug']}.jsonl"
        _write_jsonl(out_path, facts)
        generated += 1
        print(f"[OK] Wrote {len(facts):02d} facts -> {out_path.relative_to(repo_root)}")

    print(f"[DONE] Generated files: {generated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
