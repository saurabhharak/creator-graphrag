# Golden Facts Annotation Guide
## Creator GraphRAG — Knowledge Unit Extraction Evaluation

**Purpose**: Create a ground-truth dataset of factual statements that the extraction pipeline
MUST correctly identify in each book. These "golden facts" are used to measure whether the LLM
(via Zenmux) correctly extracts, classifies, and stores structured knowledge from the text.

**Target**: ~20 facts per book (start with Tier 1 books, which are already extracted).

---

## What Is a "Golden Fact"?

A golden fact is a specific, verifiable claim from the book text that:

1. A reader can confirm by looking at the page — it is **directly stated**, not inferred
2. Has a clear **subject**, a **relationship**, and (usually) an **object**
3. Would be useful to a farmer, researcher, or student of traditional agriculture
4. Is specific enough that only one or two text passages could support it

**Good examples:**
- "Jeevamrit is prepared by fermenting cow dung, cow urine, jaggery, and pulse flour in water for 48 hours"
- "SRI (System of Rice Intensification) reduces water usage by 30–50% compared to conventional flooding"
- "जीवामृत जमिनीतील सूक्ष्मजीवांना सक्रिय करते" (Jeevamrit activates soil microorganisms)

**Bad examples (do NOT annotate these):**
- "Agriculture is important" — too vague, no testable object
- "The author argues that..." — author opinion, not extractable fact
- "It may help crops grow better" — too uncertain ("may")
- A fact you remembered from outside the book — must come from this book's text

---

## The JSONL Schema

Each line in a `.jsonl` file is one golden fact as a JSON object.

```json
{
  "fact_id": "en-NF-001",
  "book_slug": "introduction-to-natural-farming",
  "language": "en",
  "type": "practice",
  "domain_type": "practice",
  "subject": "Jeevamrit",
  "predicate": "prepared by fermenting",
  "object": "cow dung, cow urine, jaggery, pulse flour in water",
  "conditions": "fermented for 48 hours",
  "verbatim_snippet": "a mixture of 10 kg cow dung, 10 litres cow urine, 2 kg jaggery, and 1 kg pulse flour in 200 litres of water, fermented for 48 hours",
  "page_numbers": [45],
  "confidence_floor": 0.80,
  "notes": "Exact recipe with quantities. Should be extracted as type=practice with payload.steps."
}
```

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fact_id` | string | YES | Unique ID. Format: `{lang}-{book-code}-{NNN}`. E.g. `en-NF-001`, `mr-AH-003` |
| `book_slug` | string | YES | Kebab-case book name (see Book Codes below) |
| `language` | string | YES | `en`, `mr`, `hi`, `sa` |
| `type` | string | YES | One of: `claim`, `definition`, `process`, `comparison`, `observation`, `practice`, `principle`, `prescription` |
| `domain_type` | string | YES | One of: `crop`, `practice`, `input_material`, `season`, `region`, `pest`, `soil`, `water`, `general` |
| `subject` | string | YES | The main entity — exactly as it appears (or would appear) in the knowledge unit |
| `predicate` | string | YES | The relationship verb phrase (max 50 chars) |
| `object` | string or null | sometimes | Required for `claim`, `comparison`, `prescription`. Null for `definition`, `observation`, `principle` |
| `conditions` | string or null | NO | Optional context (season, quantity qualifier, etc.) |
| `verbatim_snippet` | string | YES | Word-for-word quote from the book that PROVES this fact. Max 300 chars |
| `page_numbers` | list[int] | YES | Page(s) in the PDF where this fact appears |
| `confidence_floor` | float | YES | Minimum confidence score you expect the extractor to assign. Use 0.80 for clear facts, 0.65 for ambiguous ones |
| `notes` | string | NO | Your annotation notes — why you chose it, what makes it tricky |

---

## Unit Types — Quick Reference

| Type | Use when | Needs object? |
|------|----------|---------------|
| `claim` | Cause-effect or property assertion ("X improves Y") | YES |
| `definition` | What something is ("Jeevamrit is a biofertilizer") | NO |
| `process` | Multi-step procedure with ordered steps | NO (steps in payload) |
| `comparison` | Direct comparison of two practices or materials | YES |
| `observation` | Seasonal, weather, or astronomical pattern | NO |
| `practice` | A named farming operation with method/inputs | NO |
| `principle` | An overarching guideline or belief | NO |
| `prescription` | If-then recommendation with quantity/timing | YES |

## Domain Types — Quick Reference

| domain_type | Use when subject is… |
|-------------|----------------------|
| `crop` | A plant species, seed variety, grain, vegetable, or fruit |
| `practice` | A named farming technique (Jeevamrit, SRI, biodynamic) |
| `input_material` | Cow dung, urine, jaggery, neem, fertilizer, amendment |
| `season` | A season, month, nakshatra, lunar phase, planting window |
| `region` | A state, agro-climatic zone, geographic area |
| `pest` | An insect pest, plant disease, weed, or pathogen |
| `soil` | A soil type, texture, horizon, or soil property |
| `water` | A water source, rainfall pattern, or irrigation method |
| `general` | Abstract outcomes, relationships, general statements |

---

## Step-by-Step Annotation Process

### Step 1 — Read the book section first

Open the PDF. Read a chapter or a 10-page section completely before annotating.
Do NOT annotate while skimming. Understanding context prevents wrong labels.

### Step 2 — Identify candidate facts

Look for sentences that state:
- A procedure or recipe (ingredients + method + output) → `process` or `practice`
- A cause-effect claim ("X leads to Y", "X improves Y") → `claim`
- A definition ("X is defined as Y", "X means Y") → `definition`
- A conditional recommendation ("When X happens, apply Y at rate Z") → `prescription`
- A seasonal rule ("In summer / during Kharif, X should be done") → `observation`

### Step 3 — Find the verbatim snippet

Copy the exact words from the page. Do not paraphrase. The snippet must be ≤ 300 chars.
If the fact spans multiple sentences, pick the most informative continuous phrase.

### Step 4 — Fill the JSON fields

Use the schema above. When unsure about `type`, pick the closest match and note it in `notes`.

### Step 5 — Set confidence_floor

- **0.80** — fact is crystal-clear, single sentence, specific quantities
- **0.70** — fact is clear but spans multiple sentences or needs context
- **0.65** — fact uses ambiguous language or archaic terminology

### Step 6 — Self-check before saving

- [ ] Is the verbatim_snippet actually in the book (word-for-word)?
- [ ] Does the subject appear in the snippet?
- [ ] Is the page number correct?
- [ ] For `claim` / `comparison` / `prescription`: is `object` filled in?
- [ ] Is `fact_id` unique across all your facts for this book?
- [ ] Does the JSON parse without errors? (Use jsonlint.com or Python `json.loads()`)

---

## Book Codes and Tier Priority

### Tier 1 — Ready Now (Sarvam extraction already done)

These books have extracted Markdown text. You can read the `.md` files instead of PDFs.

| Book Code | Book Name | Language | Text Location | Facts Target |
|-----------|-----------|----------|---------------|--------------|
| `NF` | Introduction to Natural Farming | English | `data/extracted/Introduction to Natural Farming/document.md` | 20 |
| `AT` | An Agricultural Testament (Howard) | English | `data/extracted/An agricultural testament/document.md` | 20 |
| `AH` | आपले हात जगन्नाथ | Marathi | `data/extracted/आपले हात जगन्नाथ/document.md` | 20 |

**For Tier 1**: Read from the `.md` file (easier than PDF). Page numbers come from the `## Page N` headers in the extracted Markdown.

### Tier 2 — Needs Extraction (PDF only for now)

These books require running Sarvam AI extraction before annotating from the Markdown.
You CAN still annotate from the PDF — just note the PDF page numbers directly.

| Book Code | Book Name | Language | Priority |
|-----------|-----------|----------|----------|
| `ITK1` | Inventory of ITK in Agriculture — Vol 1 | English | High |
| `ITK2` | Inventory of ITK in Agriculture — Vol 2 | English | High |
| `IITK` | Traditional Knowledge in Agriculture (IITK) | English | High |
| `NFSA` | Natural Farming for Sustainable Agriculture | English | Medium |
| `TM` | Technical Manual on Natural Farming | English | Medium |
| `AHI` | Agriculture and Agriculturists in Ancient India | English | Low |
| `HI` | Handbook of Indian Agriculture | English | Low |
| `KP` | Krishi Parashar (Sanskrit) | Sanskrit | Low |
| `VA` | Vriksha Ayurveda of Surapala | Sanskrit/English | Low |
| `AGH1` | AgriHistory Vol 1 | English | Low |
| `AGH2` | AgriHistory Vol 2 | English | Low |
| `AGH3` | AgriHistory Vol 3 | English | Low |

**Start with Tier 1. Do all 3 books (60 facts total) before moving to Tier 2.**

---

## Language-Specific Instructions

### English Books

- Subject and object should be in English as they appear in the text
- Technical terms (Jeevamrit, Beejamrit, SRI) keep their original spelling
- Quantities are golden: "200 litres", "48 hours", "30–50%"
- If a table in the book states data (e.g., yield comparison), that is a `comparison` fact

### Marathi Book (आपले हात जगन्नाथ)

- Subject and object MUST be in Devanagari script — do NOT translate to English
- Example: subject = "जीवामृत", NOT "Jeevamrit"
- `predicate` can be short Marathi verb phrase: "बनवले जाते", "वापरले जाते", "सुधारते"
- The extracted Markdown has Devanagari text — copy directly from there
- Use `language: "mr"` in every fact from this book

### Sanskrit Books (Krishi Parashar, Vriksha Ayurveda) — Tier 2 only

- Verse numbers are important context — include in `notes`: "Shloka 42"
- Sanskrit terms (balīvarda, gomaya, jalī) stay in the subject field AS-IS
- `confidence_floor` should be 0.65 for archaic terms, 0.70 for clear verses with commentary
- If the book has an English commentary, prefer annotating from the commentary's meaning

---

## Output File Naming

Create one `.jsonl` file per book:

```
tests/golden_facts/
  introduction-to-natural-farming.jsonl     ← 20 facts
  an-agricultural-testament.jsonl           ← 20 facts
  aapale-haat-jagannath.jsonl               ← 20 facts
  inventory-itk-vol1.jsonl                  ← future
  ...
```

---

## How to Write 20 Varied Facts (Diversity Checklist)

For each book, aim to cover ALL of the following categories:

| Category | How many | Description |
|----------|----------|-------------|
| Practice/Process | 5–6 | Named operations with steps or ingredients |
| Claim (cause-effect) | 4–5 | "X improves Y", "X reduces Z" |
| Prescription | 3–4 | Conditional recommendations with quantities |
| Definition | 2–3 | What something is |
| Observation | 2–3 | Seasonal or ecological patterns |
| Comparison | 1–2 | Two practices or inputs compared directly |
| Principle | 1 | Overarching philosophy or guideline |

Also vary by:
- **domain_type**: don't put all 20 facts about the same domain (all `practice` is too narrow)
- **Page spread**: don't cluster all 20 facts on pages 1–20; spread across the whole book
- **Confidence**: include a few hard/ambiguous ones (confidence_floor 0.65) alongside the easy ones

---

## Validation After Annotation

Once you submit your `.jsonl` file, the team will run:

```bash
python scripts/eval_golden_facts.py --file tests/golden_facts/introduction-to-natural-farming.jsonl
```

This script:
1. Queries the database for knowledge units matching your `subject` + `book_slug`
2. Checks if a matching unit exists with `confidence >= confidence_floor`
3. Checks if the `type` and `domain_type` match
4. Reports: Recall (how many of your 20 facts were found), Precision (no hallucinated units), F1

**Target scores**: Recall ≥ 0.80, F1 ≥ 0.75

If a fact scores 0 (not found), the team will investigate whether it is:
- An extraction failure (LLM missed it — pipeline bug)
- An annotation error (the fact was mis-labelled — annotation guide updated)

---

## Frequently Asked Questions

**Q: What if the same fact appears in two books?**
Annotate it in both books with different `fact_id`s. Cross-book fact presence is valuable signal.

**Q: What if I can't find a verbatim snippet because the fact is spread across a paragraph?**
Pick the most informative single sentence. If truly inseparable, pick up to 2 consecutive sentences
(combined ≤ 300 chars). Note it in `notes`.

**Q: What if a page has a table with many facts?**
You can annotate multiple rows from the same table as separate facts (different `fact_id`).
Each fact should cite the same page number.

**Q: For Marathi, do I write the predicate in Marathi?**
Yes, but keep it short (≤ 50 chars). A short English predicate is also acceptable if the Marathi
verb is too long, as long as the subject and object remain in Devanagari.

**Q: How do I handle facts about dosage/rate that use numbers?**
Include the numbers in the `object` or `conditions` field.
Example: object = "100 litres per acre", conditions = "applied at sowing time"

**Q: The book says 'may' or 'can' — is it still a fact?**
If the hedging is weak ("the practice can improve..."), annotate it with `confidence_floor: 0.65`.
If it is strongly hedged ("might possibly..."), skip it.

---

## Example Annotated Facts

### English — Introduction to Natural Farming (NF)

```jsonl
{"fact_id":"en-NF-001","book_slug":"introduction-to-natural-farming","language":"en","type":"practice","domain_type":"practice","subject":"Jeevamrit","predicate":"prepared by fermenting","object":"cow dung, cow urine, jaggery, pulse flour","conditions":"fermented 48 hours in 200L water","verbatim_snippet":"10 kg cow dung, 10 litres cow urine, 2 kg jaggery, and 1 kg pulse flour in 200 litres of water, fermented for 48 hours","page_numbers":[45],"confidence_floor":0.85,"notes":"Clear recipe with exact quantities. Should produce type=practice with payload.steps."}
{"fact_id":"en-NF-002","book_slug":"introduction-to-natural-farming","language":"en","type":"claim","domain_type":"practice","subject":"Jeevamrit","predicate":"increases","object":"soil microbial activity","conditions":null,"verbatim_snippet":"Jeevamrit increases the microbial activity in the soil significantly within a few weeks of application","page_numbers":[46],"confidence_floor":0.80,"notes":"Causal claim, easy to extract."}
{"fact_id":"en-NF-003","book_slug":"introduction-to-natural-farming","language":"en","type":"prescription","domain_type":"practice","subject":"Beejamrit","predicate":"applied to seeds before","object":"sowing","conditions":"50ml per kg of seeds","verbatim_snippet":"seeds should be soaked in Beejamrit solution at 50ml per kg before sowing","page_numbers":[52],"confidence_floor":0.80,"notes":"Prescription with quantity and timing."}
```

### Marathi — आपले हात जगन्नाथ (AH)

```jsonl
{"fact_id":"mr-AH-001","book_slug":"aapale-haat-jagannath","language":"mr","type":"definition","domain_type":"practice","subject":"जीवामृत","predicate":"आहे","object":null,"conditions":null,"verbatim_snippet":"जीवामृत हे एक जैविक खत आहे जे जमिनीतील सूक्ष्मजीवांना सक्रिय करते","page_numbers":[12],"confidence_floor":0.85,"notes":"Clear definition in Marathi. Subject must remain in Devanagari."}
{"fact_id":"mr-AH-002","book_slug":"aapale-haat-jagannath","language":"mr","type":"claim","domain_type":"practice","subject":"जीवामृत","predicate":"सुधारते","object":"पिकांची वाढ","conditions":null,"verbatim_snippet":"जीवामृत पिकांची वाढ सुधारते आणि उत्पादन वाढवते","page_numbers":[12],"confidence_floor":0.80,"notes":"Causal claim in Marathi."}
{"fact_id":"mr-AH-003","book_slug":"aapale-haat-jagannath","language":"mr","type":"prescription","domain_type":"input_material","subject":"गोमूत्र","predicate":"वापरावे","object":"कीडनाशक म्हणून","conditions":"१० पट पाण्यात पातळ करून","verbatim_snippet":"गोमूत्र १० पट पाण्यात पातळ करून कीडनाशक म्हणून वापरावे","page_numbers":[28],"confidence_floor":0.75,"notes":"Prescription with dilution ratio."}
```

---

## Contact

If you are unsure about a fact, write it as your best guess and add a question in the `notes`
field starting with "QUESTION:". The team will review and clarify.

For structural questions (file format errors, unclear instructions), contact the project team.
