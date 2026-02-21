"""Embedding client — Qwen3-Embedding-8B via Ollama.

Ollama native endpoint: POST /api/embeddings
  Request:  {"model": "qwen3:embedding", "prompt": "<text>"}
  Response: {"embedding": [float, ...]}

For batch embedding, we call the endpoint in a tight loop and log progress
every 25 texts to stay observable without spam.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-embedding:8b"


@dataclass
class EmbedResult:
    vector: list[float]
    model_id: str


def embed_batch(
    texts: list[str],
    endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
    model: str = DEFAULT_MODEL,
    timeout: float = 120.0,
    log_every: int = 25,
) -> list[EmbedResult]:
    """Embed a list of texts using Ollama.

    Skips empty strings silently and raises on HTTP errors.

    Args:
        texts: Strings to embed (order preserved).
        endpoint: Ollama base URL (default http://localhost:11434).
        model: Ollama model name (default qwen3:embedding).
        timeout: Per-request HTTP timeout in seconds.
        log_every: Log progress every N texts.

    Returns:
        List of EmbedResult, same length and order as non-empty inputs.

    Raises:
        httpx.HTTPStatusError: On non-2xx response from Ollama.
        httpx.ConnectError: If Ollama is not reachable.
    """
    results: list[EmbedResult] = []

    with httpx.Client(timeout=timeout) as client:
        for i, text in enumerate(texts):
            if not text.strip():
                continue

            response = client.post(
                f"{endpoint}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            response.raise_for_status()
            vector: list[float] = response.json()["embedding"]
            results.append(EmbedResult(vector=vector, model_id=model))

            if (i + 1) % log_every == 0:
                logger.info("embedding_progress", done=i + 1, total=len(texts))

    logger.info("embedding_complete", total=len(results), model=model)
    return results


def check_ollama(endpoint: str = DEFAULT_OLLAMA_ENDPOINT) -> bool:
    """Return True if Ollama is reachable at the given endpoint."""
    try:
        r = httpx.get(f"{endpoint}/api/tags", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False
