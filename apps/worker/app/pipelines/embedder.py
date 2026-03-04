"""Embedding client — supports Ollama and HuggingFace (OpenAI-compatible) backends.

Provider selection is controlled by EMBEDDING_PROVIDER env var:
  "ollama"       — POST to Ollama /api/embed (batch) or /api/embeddings (sequential)
  "huggingface"  — POST to HF_EMBEDDING_URL with Bearer HF_TOKEN (OpenAI-compatible)

Contextual prefix (Fix 2):
  Each text is prefixed with book/section/page metadata before embedding.
  The raw chunk text is stored in Qdrant; only the prefixed version is sent
  to the model. This improves retrieval recall by 20-49%.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ── Cached batch-support flag for Ollama ──────────────────────────────────────
_ollama_batch_supported: bool | None = None


@dataclass
class EmbedResult:
    vector: list[float]
    model_id: str


# ── Contextual prefix builder ─────────────────────────────────────────────────

def build_context_prefix(
    book_title: str,
    section_title: str | None,
    page_start: int,
    page_end: int,
    language: str = "en",
) -> str:
    """Build a contextual prefix to prepend before embedding.

    Anchors the embedding in document context, improving retrieval recall
    by 20-49% (Anthropic contextual retrieval benchmarks, 2024).

    The prefix is language-aware so the embedding model sees native-script
    metadata rather than English labels mixed with Devanagari content.

    Currently localised (language code → prefix language):
      "en"  → English:   "Book: X | Section: Y | Pages N-M"
      "mr"  → Marathi:   "पुस्तक: X | विभाग: Y | पाने N-M"
      "hi"  → Hindi:     "पुस्तक: X | अनुभाग: Y | पृष्ठ N-M"
      "sa"  → Sanskrit:  "ग्रन्थ: X | अध्याय: Y | पृष्ठ N-M"
      other → English fallback (covers mixed/unknown and unimplemented languages)

    To add a new language prefix (e.g. Bengali "bn"):
      1. Add an elif branch: `elif language == "bn":`
      2. Use the native words for Book / Section / Pages in Bengali script
         Bengali: পুস্তক / অধ্যায় / পৃষ্ঠা
      3. Add a test chunk in the relevant test file

    Full Sarvam AI language codes for reference:
      hi-IN  mr-IN  bn-IN  ta-IN  te-IN  gu-IN  kn-IN  ml-IN  as-IN  ur-IN
      sa-IN  ne-IN  doi-IN brx-IN pa-IN  od-IN  kok-IN mai-IN sd-IN  ks-IN
      mni-IN sat-IN en-IN
      Native "Book | Section | Pages" terms per language (for future implementation):
        bn: পুস্তক | অধ্যায় | পৃষ্ঠা
        ta: புத்தகம் | பிரிவு | பக்கங்கள்
        te: పుస్తకం | విభాగం | పేజీలు
        gu: પુસ્તક | વિભાગ | પૃષ્ઠો
        kn: ಪುಸ್ತಕ | ವಿಭಾಗ | ಪುಟಗಳು
        ml: പുസ്തകം | വിഭാഗം | പേജുകൾ
        pa: ਪੁਸਤਕ | ਭਾਗ | ਪੰਨੇ
        ur: کتاب | باب | صفحات  (RTL — may need special handling)
        ne: पुस्तक | अध्याय | पृष्ठ  (same as Hindi — Devanagari)
    """
    if not book_title:
        return ""

    page_str = f"{page_start}" if page_start == page_end else f"{page_start}-{page_end}"

    if language == "mr":
        parts = [f"पुस्तक: {book_title}"]
        if section_title:
            parts.append(f"विभाग: {section_title}")
        parts.append(f"पाने {page_str}")
    elif language == "hi":
        parts = [f"पुस्तक: {book_title}"]
        if section_title:
            parts.append(f"अनुभाग: {section_title}")
        parts.append(f"पृष्ठ {page_str}")
    elif language == "sa":
        parts = [f"ग्रन्थ: {book_title}"]
        if section_title:
            parts.append(f"अध्याय: {section_title}")
        parts.append(f"पृष्ठ {page_str}")
    else:
        parts = [f"Book: {book_title}"]
        if section_title:
            parts.append(f"Section: {section_title}")
        parts.append(f"Pages {page_str}")

    return " | ".join(parts) + "\n\n"


# ── HuggingFace (OpenAI-compatible) backend ───────────────────────────────────

def _embed_hf(
    client: httpx.Client,
    url: str,
    token: str,
    model: str,
    texts: list[str],
    timeout: float,
) -> list[list[float]]:
    """POST to HuggingFace OpenAI-compatible /embeddings endpoint.

    The endpoint accepts:
      POST {url}
      Authorization: Bearer {token}
      {"model": "...", "input": ["text1", "text2", ...]}

    Returns a list of embedding vectors in the same order as inputs.
    """
    response = client.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"model": model, "input": texts},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    # OpenAI-compatible response: {"data": [{"index": i, "embedding": [...]}]}
    items = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in items]


# ── Ollama backend ────────────────────────────────────────────────────────────

def _check_ollama_batch_support(client: httpx.Client, endpoint: str, model: str) -> bool:
    global _ollama_batch_supported
    if _ollama_batch_supported is not None:
        return _ollama_batch_supported
    try:
        r = client.post(
            f"{endpoint}/api/embed",
            json={"model": model, "input": ["test"]},
            timeout=10.0,
        )
        _ollama_batch_supported = r.status_code == 200 and "embeddings" in r.json()
    except Exception:
        _ollama_batch_supported = False
    logger.info("ollama_batch_embed_support", supported=_ollama_batch_supported)
    return _ollama_batch_supported  # type: ignore[return-value]


def _embed_ollama_batch(
    client: httpx.Client,
    endpoint: str,
    model: str,
    texts: list[str],
    timeout: float,
) -> list[list[float]]:
    response = client.post(
        f"{endpoint}/api/embed",
        json={"model": model, "input": texts},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def _embed_ollama_sequential(
    client: httpx.Client,
    endpoint: str,
    model: str,
    texts: list[str],
    timeout: float,
    log_every: int,
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i, text in enumerate(texts):
        r = client.post(
            f"{endpoint}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=timeout,
        )
        r.raise_for_status()
        vectors.append(r.json()["embedding"])
        if (i + 1) % log_every == 0:
            logger.info("embedding_progress", done=i + 1, total=len(texts))
    return vectors


# ── Public API ────────────────────────────────────────────────────────────────

def embed_batch(
    texts: list[str],
    provider: str = "ollama",
    model: str = "qwen3-embedding:8b",
    # Ollama-specific
    endpoint: str = "http://localhost:11434",
    # HuggingFace-specific
    hf_url: str = "https://router.huggingface.co/scaleway/v1/embeddings",
    hf_token: str | None = None,
    timeout: float = 300.0,
    log_every: int = 25,
    prefixes: list[str] | None = None,
) -> list[EmbedResult]:
    """Embed a list of texts using the configured provider.

    Fix 2: Prepends contextual prefix to each text before embedding.
          The prefix is NOT stored anywhere — only improves the vector.

    Args:
        texts: Raw chunk strings to embed (stored in Qdrant as-is).
        provider: "ollama" or "huggingface".
        model: Model identifier (Ollama model name or HF model ID).
        endpoint: Ollama base URL (ignored for huggingface provider).
        hf_url: Full HuggingFace embeddings endpoint URL.
        hf_token: HuggingFace Bearer token.
        timeout: HTTP timeout in seconds.
        log_every: Log progress every N texts (Ollama sequential mode only).
        prefixes: Optional per-text context prefixes, same length as texts.

    Returns:
        List of EmbedResult, same length and order as non-empty inputs.
    """
    # Build prefixed texts (raw texts go to Qdrant, prefixed to model)
    embed_texts: list[str] = []
    for i, text in enumerate(texts):
        if not text.strip():
            continue
        prefix = (prefixes[i] if prefixes and i < len(prefixes) else None) or ""
        embed_texts.append(prefix + text if prefix else text)

    if not embed_texts:
        return []

    with httpx.Client(timeout=timeout) as client:
        if provider == "huggingface":
            if not hf_token:
                raise ValueError("HF_TOKEN is required for EMBEDDING_PROVIDER=huggingface")
            logger.info(
                "embedding_hf_start",
                total=len(embed_texts),
                model=model,
                url=hf_url,
            )
            vectors = _embed_hf(client, hf_url, hf_token, model, embed_texts, timeout)
            logger.info("embedding_hf_done", total=len(vectors), model=model)

        else:  # ollama
            use_batch = _check_ollama_batch_support(client, endpoint, model)
            if use_batch:
                logger.info("embedding_batch_start", total=len(embed_texts), model=model)
                try:
                    vectors = _embed_ollama_batch(client, endpoint, model, embed_texts, timeout)
                except Exception as exc:
                    logger.warning(
                        "embedding_batch_failed_fallback_sequential",
                        error=str(exc),
                        total=len(embed_texts),
                    )
                    global _ollama_batch_supported
                    _ollama_batch_supported = False
                    vectors = _embed_ollama_sequential(
                        client, endpoint, model, embed_texts, timeout, log_every
                    )
            else:
                vectors = _embed_ollama_sequential(
                    client, endpoint, model, embed_texts, timeout, log_every
                )
            logger.info("embedding_done", total=len(vectors), model=model)

    return [EmbedResult(vector=v, model_id=model) for v in vectors]


def check_embedding_service(
    provider: str = "ollama",
    endpoint: str = "http://localhost:11434",
    hf_url: str = "https://router.huggingface.co/scaleway/v1/embeddings",
    hf_token: str | None = None,
) -> bool:
    """Return True if the configured embedding service is reachable."""
    try:
        if provider == "huggingface":
            # A minimal probe: POST with empty input just to check auth/connectivity
            r = httpx.post(
                hf_url,
                headers={"Authorization": f"Bearer {hf_token}"},
                json={"model": "", "input": ["test"]},
                timeout=5.0,
            )
            return r.status_code in (200, 400, 422)  # 400/422 = reachable but bad model
        else:
            r = httpx.get(f"{endpoint}/api/tags", timeout=5.0)
            return r.status_code == 200
    except Exception:
        return False
