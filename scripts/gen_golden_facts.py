"""Generate golden facts for two missing books using LLM bootstrap (Phase 1)."""
import re, os, json
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_BASE_URL"],
)


def clean_doc(path):
    content = Path(path).read_text(encoding="utf-8")
    content = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]{50,}", "", content)
    content = re.sub(r"!\[Image\]\([^)]{10,}\)", "", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def sample_text(text, max_chars=28000):
    n = len(text)
    if n <= max_chars:
        return text
    chunk = max_chars // 3
    start = text[:chunk]
    mid = text[n // 2 - chunk // 2 : n // 2 + chunk // 2]
    end = text[-chunk:]
    return start + "\n\n[...]\n\n" + mid + "\n\n[...]\n\n" + end


PROMPT = """\
You are an expert annotator for agricultural knowledge extraction evaluation.

Extract exactly 20 golden facts from this agricultural text. Each fact must:
1. Use a verbatim_snippet that is an EXACT quote from the text (max 300 chars)
2. Be specific and verifiable — not vague or general
3. Cover diverse types spread across the full book (not clustered on the first pages)
4. Follow the schema exactly

Schema per fact (JSON object):
{{
  "fact_id": "{prefix}001",
  "book_slug": "{slug}",
  "language": "en",
  "type": "<one of: claim, definition, process, comparison, observation, practice, principle, prescription>",
  "domain_type": "<one of: crop, practice, input_material, season, region, pest, soil, water, general>",
  "subject": "main entity as named in the text",
  "predicate": "verb phrase max 50 chars",
  "object": "object entity or null",
  "conditions": "qualifier/context or null",
  "verbatim_snippet": "exact quote from text max 300 chars",
  "page_numbers": [estimated_page_number],
  "confidence_floor": 0.80,
  "notes": "brief annotation note"
}}

Target mix across 20 facts: 5 practice/process, 4 claim, 3 prescription, 3 definition, 2 observation, 2 comparison, 1 principle.

Return ONLY a valid JSON array of exactly 20 objects. No markdown fences, no explanation.

TEXT:
{text}
"""


BOOKS = [
    {
        "doc": ROOT / "data/extracted/Agriculture and Agriculturists in Ancient India/document.md",
        "out": ROOT / "tests/golden_facts/agriculture-ancient-india.jsonl",
        "slug": "agriculture-ancient-india",
        "prefix": "en-AHI-",
        "total_pages": 156,
    },
    {
        "doc": ROOT / "data/extracted/Vriksha Ayurveda of Surapala Nalini Sadhale 1996/document.md",
        "out": ROOT / "tests/golden_facts/vriksha-ayurveda.jsonl",
        "slug": "vriksha-ayurveda",
        "prefix": "en-VA-",
        "total_pages": 101,
    },
]


def run():
    for book in BOOKS:
        print(f"\n{'='*60}")
        print(f"Book: {book['slug']}")
        print(f"{'='*60}")

        text = clean_doc(book["doc"])
        sample = sample_text(text)
        print(f"  Text: {len(text):,} chars → sampled {len(sample):,} chars")

        prompt = PROMPT.format(
            prefix=book["prefix"],
            slug=book["slug"],
            text=sample,
        )

        print("  Calling LLM (gpt-4.1)...")
        resp = client.chat.completions.create(
            model="openai/gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        facts = json.loads(raw)
        print(f"  Received {len(facts)} facts from LLM")

        # Fix IDs and estimate page numbers from text position
        for i, fact in enumerate(facts, 1):
            fact["fact_id"] = book["prefix"] + f"{i:03d}"
            fact["book_slug"] = book["slug"]
            snippet = fact.get("verbatim_snippet", "")
            pos = text.find(snippet[:40]) if snippet else -1
            if pos >= 0:
                estimated_page = max(1, round(pos / len(text) * book["total_pages"]))
                fact["page_numbers"] = [estimated_page]

        # Write JSONL
        with open(book["out"], "w", encoding="utf-8") as f:
            for fact in facts:
                f.write(json.dumps(fact, ensure_ascii=False) + "\n")

        print(f"  Written {len(facts)} facts → {book['out']}")

        # Quick validation
        errors = []
        for fact in facts:
            if not fact.get("verbatim_snippet"):
                errors.append(f"{fact['fact_id']}: missing verbatim_snippet")
            if fact.get("type") not in ["claim","definition","process","comparison","observation","practice","principle","prescription"]:
                errors.append(f"{fact['fact_id']}: bad type '{fact.get('type')}'")
        if errors:
            print(f"  WARNINGS: {errors}")
        else:
            print(f"  All {len(facts)} facts validated OK")

    print("\nAll done.")


if __name__ == "__main__":
    run()
