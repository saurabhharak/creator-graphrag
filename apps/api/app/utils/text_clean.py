"""
Text sanitization utilities.

Includes LLM prompt injection protection (GAP-SEC-05).
"""
from __future__ import annotations
import re
from app.core.errors import PromptInjectionError

# Patterns that indicate prompt injection attempts
INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|prior)\s+instructions",
    r"forget\s+(everything|all|previous)",
    r"you\s+are\s+now\s+",
    r"act\s+as\s+(if|a|an)\s+",
    r"jailbreak",
    r"disregard\s+(your|the|all)",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"###\s*(instruction|system|assistant)",
]

_compiled_patterns = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def sanitize_for_llm(text: str, max_length: int = 2000, field_name: str = "input") -> str:
    """
    Sanitize user-supplied text before passing to LLM.

    - Strips prompt injection patterns
    - Enforces max length
    - Raises PromptInjectionError if injection detected (caller may log to audit_log)
    """
    if not text:
        return text

    # Enforce max length first
    if len(text) > max_length:
        text = text[:max_length]

    # Check for injection patterns
    for pattern in _compiled_patterns:
        if pattern.search(text):
            raise PromptInjectionError(
                f"Input field '{field_name}' contains disallowed patterns",
                details={"field": field_name, "pattern": pattern.pattern},
            )

    return text.strip()


def normalize_whitespace(text: str) -> str:
    """Normalize multiple spaces, tabs, and newlines."""
    return re.sub(r"\s+", " ", text).strip()


def truncate_snippet(text: str, max_len: int = 600) -> str:
    """Truncate text to snippet max length with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"
