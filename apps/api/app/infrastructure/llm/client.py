"""Async OpenAI-compatible LLM client for the API service.

Used by the video package generation usecase.  Raises openai.APIError
subclasses on failure — the caller catches these and returns appropriate
HTTP error responses.
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


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = "openai/gpt-4.1",
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 6000,
    json_mode: bool = True,
) -> LlmResponse:
    """Call an OpenAI-compatible chat completion endpoint.

    Args:
        system_prompt: System message (always English per STANDARDS.md).
        user_prompt: User message containing the generation request.
        model: Model ID (with provider prefix for proxies, e.g. openai/gpt-4.1).
        api_key: API key; falls back to OPENAI_API_KEY env var.
        base_url: Optional custom endpoint (e.g. https://zenmux.ai/api/v1).
        temperature: Sampling temperature.
        max_tokens: Max output tokens.
        json_mode: Request JSON object response format.

    Returns:
        LlmResponse with content, token counts, and model_id.

    Raises:
        openai.AuthenticationError: Invalid API key.
        openai.RateLimitError: Rate limit exceeded.
        openai.APIError: Other API errors.
    """
    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    kwargs: dict = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content or ""
    usage = response.usage

    logger.debug(
        "llm_call_done",
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
