"""Shared embedding service — vector embedding via Ollama or HuggingFace.

Provides a single `embed_query` entry point that dispatches to the configured
EMBEDDING_PROVIDER (ollama | huggingface).  Both the search router and the
video-package usecase import from here instead of duplicating the logic.
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from app.core.config import settings


async def embed_query(query: str) -> list[float]:
    """Embed a query string. Uses EMBEDDING_PROVIDER setting (ollama or huggingface)."""
    if settings.EMBEDDING_PROVIDER == "huggingface":
        return await _embed_via_huggingface(query)
    return await _embed_via_ollama(query)


async def _embed_via_ollama(query: str) -> list[float]:
    """Embed via local Ollama instance."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                f"{settings.OLLAMA_ENDPOINT}/api/embeddings",
                json={"model": settings.EMBEDDING_MODEL, "prompt": query},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Ollama timed out (model may be loading): {exc}",
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Ollama unavailable. "
                    f"Ensure Ollama is running at {settings.OLLAMA_ENDPOINT} "
                    f"with model '{settings.EMBEDDING_MODEL}' pulled."
                ),
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ollama error: {exc.response.text[:200]}",
            )


async def _embed_via_huggingface(query: str) -> list[float]:
    """Embed via HuggingFace Inference API (Scaleway endpoint)."""
    if not settings.HF_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HF_TOKEN not configured. Set HF_TOKEN in .env to use HuggingFace embeddings.",
        )
    # Convert Ollama model name to HF format: "qwen3-embedding:8b" -> "qwen3-embedding-8b"
    hf_model = settings.EMBEDDING_MODEL.replace(":", "-")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                settings.HF_EMBEDDING_URL,
                headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
                json={"input": query, "model": hf_model},
            )
            resp.raise_for_status()
            data = resp.json()
            # HF returns {"data": [{"embedding": [...]}]} (OpenAI-compatible)
            if isinstance(data, dict) and "data" in data:
                return data["data"][0]["embedding"]
            # Or direct list format
            if isinstance(data, list) and len(data) > 0:
                return data[0] if isinstance(data[0], list) else data
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Unexpected HF embedding response format: {str(data)[:200]}",
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"HuggingFace embedding timed out: {exc}",
            )
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"HuggingFace embedding error: {exc}",
            )
