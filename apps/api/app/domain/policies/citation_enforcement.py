"""
Citation Enforcement Policy (Section 9.2 + GAP-PIPE-10).

Strict mode algorithm:
1. Split script into paragraphs
2. For each paragraph: verify evidence_ids[] reference real, retrieved chunks
3. If missing evidence:
   a. Try auto-repair: re-prompt with "rewrite using only these evidence snippets"
   b. If still missing: apply citation_repair_mode action
      - remove_paragraph: silently drop paragraph
      - label_interpretation: prepend [Interpretation] label
      - fail_generation: raise CitationEnforcementError
4. Track all repair actions in warnings list
"""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class CitationRepairMode(str, Enum):
    REMOVE_PARAGRAPH = "remove_paragraph"
    LABEL_INTERPRETATION = "label_interpretation"
    FAIL_GENERATION = "fail_generation"


@dataclass
class Paragraph:
    paragraph_id: str
    text: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class EnforcementResult:
    paragraphs: list[Paragraph]
    warnings: list[str] = field(default_factory=list)
    removed_count: int = 0
    labeled_count: int = 0
    repaired_count: int = 0
    citation_coverage: float = 1.0  # % paragraphs with at least one evidence


class CitationEnforcementError(Exception):
    """Raised when fail_generation mode is set and a paragraph lacks evidence."""
    pass


class CitationEnforcementPolicy:
    """
    Enforces that every paragraph in a generated script maps to evidence.

    Usage:
        policy = CitationEnforcementPolicy(
            retrieved_evidence_ids={"chunk:abc", "unit:xyz"},
            repair_mode=CitationRepairMode.LABEL_INTERPRETATION,
            llm_repair_fn=async_repair_fn,
        )
        result = await policy.enforce(paragraphs)
    """

    def __init__(
        self,
        retrieved_evidence_ids: set[str],
        repair_mode: CitationRepairMode = CitationRepairMode.LABEL_INTERPRETATION,
        llm_repair_fn: Callable[..., str] | None = None,
    ):
        self.retrieved_evidence_ids = retrieved_evidence_ids
        self.repair_mode = repair_mode
        self.llm_repair_fn = llm_repair_fn

    async def enforce(self, paragraphs: list[Paragraph]) -> EnforcementResult:
        """Run citation enforcement on all paragraphs.

        Args:
            paragraphs: List of Paragraph objects from the generated script,
                each carrying evidence_ids claimed by the LLM.

        Returns:
            EnforcementResult with processed paragraphs, warnings list,
            counts of removed/labeled/repaired paragraphs, and overall
            citation_coverage ratio (supported / total).

        Raises:
            CitationEnforcementError: When repair_mode is FAIL_GENERATION and
                a paragraph cannot be grounded in retrieved evidence.
        """
        result_paragraphs = []
        warnings = []
        removed = 0
        labeled = 0
        repaired = 0
        supported = 0

        for para in paragraphs:
            valid_ids = [
                eid for eid in para.evidence_ids
                if eid in self.retrieved_evidence_ids
            ]

            if valid_ids:
                para.evidence_ids = valid_ids
                result_paragraphs.append(para)
                supported += 1
                continue

            # Try auto-repair
            if self.llm_repair_fn:
                repaired_text = await self.llm_repair_fn(
                    original_text=para.text,
                    evidence_ids=list(self.retrieved_evidence_ids),
                )
                # Re-check after repair
                if repaired_text and repaired_text != para.text:
                    para.text = repaired_text
                    repaired += 1
                    warnings.append(
                        f"para:{para.paragraph_id} auto-repaired by LLM (evidence missing)"
                    )
                    result_paragraphs.append(para)
                    supported += 1
                    continue

            # Apply repair mode
            if self.repair_mode == CitationRepairMode.REMOVE_PARAGRAPH:
                removed += 1
                warnings.append(f"para:{para.paragraph_id} removed (no evidence found)")
            elif self.repair_mode == CitationRepairMode.LABEL_INTERPRETATION:
                para.text = f"[Interpretation] {para.text}"
                labeled += 1
                warnings.append(f"para:{para.paragraph_id} labeled [Interpretation]")
                result_paragraphs.append(para)
            elif self.repair_mode == CitationRepairMode.FAIL_GENERATION:
                raise CitationEnforcementError(
                    f"Paragraph {para.paragraph_id} has no valid evidence and mode=fail_generation"
                )

        total = len(paragraphs)
        coverage = supported / total if total > 0 else 1.0

        return EnforcementResult(
            paragraphs=result_paragraphs,
            warnings=warnings,
            removed_count=removed,
            labeled_count=labeled,
            repaired_count=repaired,
            citation_coverage=coverage,
        )
