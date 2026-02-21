# Test Fixtures

## Purpose
Multilingual test fixtures for integration tests and golden query evaluation.

## Required Fixture Files

| File | Language | Content | Source | License |
|------|----------|---------|--------|---------|
| `sample_marathi_agri.pdf` | Marathi | Agricultural content, ~10 pages | Public domain | TODO |
| `sample_hindi_text.pdf` | Hindi | General content, ~10 pages | Public domain | TODO |
| `sample_mixed_script.pdf` | Marathi + English | Mixed script (code-switching) | Synthetic | TODO |
| `sample_epub.epub` | English | Simple EPUB test file | Synthetic | MIT |

## Status
Fixture files not yet added. To generate synthetic test fixtures:

```bash
# Install reportlab for PDF generation
pip install reportlab

# Generate Marathi sample (requires Devanagari font)
python tests/fixtures/generate_fixtures.py
```

## Fixture Factory (for Unit Tests)
For unit tests that don't need real OCR, use the `FixtureBook` factory in `tests/conftest.py`:

```python
from tests.conftest import fixture_book_with_chunks

@pytest.fixture
def book(fixture_book_with_chunks):
    return fixture_book_with_chunks(
        language="mr",
        chunk_count=10,
        include_ocr=False,
    )
```

## Notes
- Never commit copyrighted book content
- All fixtures must be either public domain, CC0, or synthetically generated
- Devanagari content: use text from publicly available agricultural publications
