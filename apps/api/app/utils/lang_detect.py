"""
Language detection utilities for Marathi, Hindi, and English.

Implements the strategy from Section 5.1 + GAP-PIPE-05/06:
- Primary detection: fastText
- Devanagari disambiguation: Marathi vs Hindi via stopword distribution
- Confidence threshold: 0.80 (below → 'mixed')
"""
from __future__ import annotations
import re
from pathlib import Path

# Devanagari Unicode block range
DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]")
LATIN_RANGE = re.compile(r"[a-zA-Z]")

# Paths to stopword files (populated in data/stopwords/)
_STOPWORDS_MR: set[str] | None = None
_STOPWORDS_HI: set[str] | None = None


def _load_stopwords() -> None:
    global _STOPWORDS_MR, _STOPWORDS_HI
    base = Path(__file__).parent.parent.parent.parent.parent / "data" / "stopwords"
    mr_path = base / "mr.txt"
    hi_path = base / "hi.txt"
    _STOPWORDS_MR = set(mr_path.read_text(encoding="utf-8").splitlines()) if mr_path.exists() else set()
    _STOPWORDS_HI = set(hi_path.read_text(encoding="utf-8").splitlines()) if hi_path.exists() else set()


def detect_script(text: str) -> str:
    """Detect dominant script: 'devanagari', 'latin', or 'mixed'."""
    dev_count = len(DEVANAGARI_RANGE.findall(text))
    lat_count = len(LATIN_RANGE.findall(text))
    total = dev_count + lat_count
    if total == 0:
        return "unknown"
    dev_ratio = dev_count / total
    if dev_ratio > 0.7:
        return "devanagari"
    if dev_ratio < 0.3:
        return "latin"
    return "mixed"


def disambiguate_devanagari(text: str) -> tuple[str, float]:
    """
    Disambiguate Marathi vs Hindi for Devanagari text using stopword distribution.

    Returns: (language_code, confidence)
    - language_code: 'mr' | 'hi' | 'mixed'
    - confidence: 0.0 to 1.0
    """
    _load_stopwords()

    words = re.findall(r"[\u0900-\u097F]+", text.lower())
    if not words:
        return "mixed", 0.0

    mr_hits = sum(1 for w in words if w in _STOPWORDS_MR)
    hi_hits = sum(1 for w in words if w in _STOPWORDS_HI)
    total_hits = mr_hits + hi_hits

    if total_hits == 0:
        return "mixed", 0.0

    mr_ratio = mr_hits / total_hits
    hi_ratio = hi_hits / total_hits

    if mr_ratio > 0.6:
        return "mr", mr_ratio
    if hi_ratio > 0.6:
        return "hi", hi_ratio
    return "mixed", max(mr_ratio, hi_ratio)


def detect_language(text: str, confidence_threshold: float = 0.80) -> tuple[str, float]:
    """
    Detect language of a text chunk.

    Returns: (language_code, confidence)
    - language_code: 'mr' | 'hi' | 'en' | 'mixed' | 'unknown'
    - confidence: 0.0 to 1.0

    Strategy:
    1. Check dominant script
    2. If Devanagari → disambiguate mr vs hi
    3. If Latin → fastText (or default 'en' if model unavailable)
    4. If below threshold → 'mixed'
    """
    from app.core.config import settings

    script = detect_script(text)

    if script == "devanagari":
        lang, conf = disambiguate_devanagari(text)
        if conf < confidence_threshold:
            return "mixed", conf
        return lang, conf

    if script == "latin":
        # TODO(#0): use fastText model if available; fall back to 'en'
        try:
            lang, conf = _fasttext_detect(text)
            if conf < confidence_threshold:
                return "mixed", conf
            return lang, conf
        except Exception:
            return "en", 1.0  # default for Latin script without model

    if script == "mixed":
        return "mixed", 0.5

    return "unknown", 0.0


def _fasttext_detect(text: str) -> tuple[str, float]:
    """Run fastText language detection. Returns (language_code, confidence)."""
    from app.core.config import settings
    from pathlib import Path
    import fasttext
    model_path = Path(settings.FASTTEXT_MODEL_PATH)
    if not model_path.exists():
        raise FileNotFoundError(f"fastText model not found: {model_path}")
    model = fasttext.load_model(str(model_path))
    labels, probs = model.predict(text.replace("\n", " "), k=1)
    lang = labels[0].replace("__label__", "")
    # Map fastText labels to our codes
    lang_map = {"mr": "mr", "hi": "hi", "en": "en"}
    return lang_map.get(lang, "unknown"), float(probs[0])
