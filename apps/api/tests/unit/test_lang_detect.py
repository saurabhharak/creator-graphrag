"""Unit tests for language detection and Marathi/Hindi disambiguation."""
import pytest
from app.utils.lang_detect import detect_script, disambiguate_devanagari, detect_language


class TestDetectScript:
    def test_devanagari_text(self):
        assert detect_script("जीवामृत तयार करण्याची पद्धत") == "devanagari"

    def test_latin_text(self):
        assert detect_script("Humus improves soil water retention") == "latin"

    def test_mixed_text(self):
        result = detect_script("जीवामृत jeevamrut preparation")
        assert result == "mixed"

    def test_empty_text(self):
        assert detect_script("") == "unknown"

    def test_numbers_only(self):
        assert detect_script("12345 6789") == "unknown"


class TestTransliteration:
    def test_canonical_key_marathi(self):
        from app.utils.transliteration import to_canonical_key
        key = to_canonical_key("जीवामृत")
        assert isinstance(key, str)
        assert len(key) > 0
        # Should not contain Devanagari characters
        import re
        assert not re.search(r"[\u0900-\u097F]", key)

    def test_canonical_key_english(self):
        from app.utils.transliteration import to_canonical_key
        assert to_canonical_key("Humus Soil") == "humus_soil"
        assert to_canonical_key("Water Retention") == "water_retention"

    def test_canonical_key_empty(self):
        from app.utils.transliteration import to_canonical_key
        assert to_canonical_key("") == ""

    def test_canonical_key_punctuation_removed(self):
        from app.utils.transliteration import to_canonical_key
        assert to_canonical_key("Soil (organic matter)") == "soil_organic_matter"


class TestCitationEnforcement:
    @pytest.mark.asyncio
    async def test_paragraph_with_evidence_passes(self):
        from app.domain.policies.citation_enforcement import (
            CitationEnforcementPolicy, CitationRepairMode, Paragraph
        )
        policy = CitationEnforcementPolicy(
            retrieved_evidence_ids={"chunk:abc123", "unit:def456"},
            repair_mode=CitationRepairMode.LABEL_INTERPRETATION,
        )
        paragraphs = [
            Paragraph("p1", "Humus improves water retention.", ["chunk:abc123"]),
        ]
        result = await policy.enforce(paragraphs)
        assert len(result.paragraphs) == 1
        assert result.citation_coverage == 1.0
        assert result.removed_count == 0

    @pytest.mark.asyncio
    async def test_paragraph_without_evidence_labeled(self):
        from app.domain.policies.citation_enforcement import (
            CitationEnforcementPolicy, CitationRepairMode, Paragraph
        )
        policy = CitationEnforcementPolicy(
            retrieved_evidence_ids={"chunk:abc123"},
            repair_mode=CitationRepairMode.LABEL_INTERPRETATION,
        )
        paragraphs = [
            Paragraph("p1", "Some unsupported claim.", []),
        ]
        result = await policy.enforce(paragraphs)
        assert len(result.paragraphs) == 1
        assert result.paragraphs[0].text.startswith("[Interpretation]")
        assert result.labeled_count == 1

    @pytest.mark.asyncio
    async def test_paragraph_without_evidence_removed(self):
        from app.domain.policies.citation_enforcement import (
            CitationEnforcementPolicy, CitationRepairMode, Paragraph
        )
        policy = CitationEnforcementPolicy(
            retrieved_evidence_ids={"chunk:abc123"},
            repair_mode=CitationRepairMode.REMOVE_PARAGRAPH,
        )
        paragraphs = [
            Paragraph("p1", "Some unsupported claim.", []),
        ]
        result = await policy.enforce(paragraphs)
        assert len(result.paragraphs) == 0
        assert result.removed_count == 1

    @pytest.mark.asyncio
    async def test_fail_generation_raises(self):
        from app.domain.policies.citation_enforcement import (
            CitationEnforcementPolicy, CitationRepairMode, CitationEnforcementError, Paragraph
        )
        policy = CitationEnforcementPolicy(
            retrieved_evidence_ids={"chunk:abc123"},
            repair_mode=CitationRepairMode.FAIL_GENERATION,
        )
        paragraphs = [Paragraph("p1", "Unsupported claim.", [])]
        with pytest.raises(CitationEnforcementError):
            await policy.enforce(paragraphs)
