"""
Transliteration utilities for canonical key generation.

Used for cross-lingual concept deduplication in the Knowledge Graph.
जीवामृत ↔ jeevamrut/jivamrut

Implements GAP-DATA from Section 5.2 and section 7.4:
- Canonical key: lowercase + remove punctuation + transliterate Devanagari to Latin
"""
from __future__ import annotations
import re
import unicodedata


def to_canonical_key(text: str) -> str:
    """
    Generate a stable canonical key for a concept term.

    Steps:
    1. Lowercase
    2. Transliterate Devanagari to Latin (using indic-transliteration)
    3. Remove punctuation and special characters
    4. Normalize whitespace to underscores

    Example:
        "जीवामृत" → "jivamrit"
        "Humus Soil" → "humus_soil"
    """
    if not text:
        return ""

    text = text.lower().strip()

    # Attempt Devanagari transliteration if Devanagari script detected
    if re.search(r"[\u0900-\u097F]", text):
        try:
            from indic_transliteration import sanscript
            from indic_transliteration.sanscript import transliterate
            text = transliterate(text, sanscript.DEVANAGARI, sanscript.IAST)
        except ImportError:
            pass  # Fall through to basic normalization

    # Remove diacritics from Latin
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))

    # Keep only alphanumeric and spaces
    text = re.sub(r"[^a-z0-9\s]", "", text)

    # Normalize whitespace to underscores
    text = re.sub(r"\s+", "_", text.strip())

    return text


def generate_latin_aliases(devanagari_term: str) -> list[str]:
    """
    Generate common Latin transliteration variants for a Devanagari term.
    Returns a list of stable alias strings.
    """
    aliases = []
    if not devanagari_term or not re.search(r"[\u0900-\u097F]", devanagari_term):
        return aliases

    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate

        # IAST (academic standard)
        aliases.append(transliterate(devanagari_term, sanscript.DEVANAGARI, sanscript.IAST).lower())
        # ITRANS (common informal)
        aliases.append(transliterate(devanagari_term, sanscript.DEVANAGARI, sanscript.ITRANS).lower())
        # Simplified Latin (remove diacritics from IAST)
        iast = aliases[0]
        simplified = unicodedata.normalize("NFKD", iast)
        simplified = "".join(c for c in simplified if not unicodedata.combining(c))
        if simplified not in aliases:
            aliases.append(simplified)
    except ImportError:
        pass

    return list(set(aliases))
