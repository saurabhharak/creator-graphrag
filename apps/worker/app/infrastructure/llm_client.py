"""Async OpenAI LLM client for knowledge unit extraction.

Thin wrapper around the OpenAI async client. Returns a structured
LlmResponse so callers can log token usage without parsing raw API objects.

Raises openai.APIError subclasses on failure — the caller (unit_extractor.py)
catches these and gracefully skips the chunk.
"""
from __future__ import annotations

from dataclasses import dataclass

import openai
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LlmResponse:
    """Parsed response from an OpenAI chat completion call."""

    content: str
    input_tokens: int
    output_tokens: int
    model_id: str


async def call_openai(
    system_prompt: str,
    user_prompt: str,
    model: str = "openai/gpt-4.1",
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 16000,
) -> LlmResponse:
    """Call OpenAI-compatible chat completion and return a structured response.

    Args:
        system_prompt: The system message (always English per STANDARDS.md).
        user_prompt: The user message containing the text chunk.
        model: Model ID (default gpt-4.1).
        api_key: API key. If None, uses OPENAI_API_KEY env var.
        base_url: Optional custom endpoint (e.g. https://zenmux.ai/api/v1).
        temperature: Sampling temperature (0.1 → deterministic, good for extraction).
        max_tokens: Max output tokens.

    Returns:
        LlmResponse with content, token counts, and model_id.

    Raises:
        openai.AuthenticationError: Invalid API key.
        openai.RateLimitError: Rate limit exceeded.
        openai.APIError: Other API errors.
    """
    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    usage = response.usage

    logger.debug(
        "openai_call_done",
        model=model,
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
    )

    return LlmResponse(
        content=content,
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
        model_id=model,
    )
