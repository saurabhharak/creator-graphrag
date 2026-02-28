# Book Priority & Annotation Tips
## Creator GraphRAG — Golden Facts Dataset

---

## Phase 1 — Start Here (Tier 1: Already Extracted)

These 3 books have Sarvam AI Markdown output ready. **Annotate from the `.md` file** — it is
easier to search and copy from than a PDF.

---

### Book 1: Introduction to Natural Farming
**File**: `data/extracted/Introduction to Natural Farming/document.md`
**Output**: `tests/golden_facts/introduction-to-natural-farming.jsonl`
**Language**: English | **Book Code**: `NF`
**Difficulty**: Easy

**What to look for:**
- Jeevamrit recipe (ingredients + quantities + fermentation time) → `practice`
- Beejamrit seed treatment protocol → `practice`
- Panchagavya composition and application → `practice`
- Comparisons: natural farming vs chemical farming yield/cost → `comparison`
- Soil health claims: "improves water retention", "reduces compaction" → `claim`
- Seasonal planting rules (monsoon, rabi, kharif) → `observation`
- Cost reduction percentages or yield improvement numbers → `prescription` or `claim`

**Search tips** (in the .md file):
```
Search for: "litres", "kg", "hours" — finds recipe facts with quantities
Search for: "compared to", "vs", "than" — finds comparison facts
Search for: "should be applied", "must be", "recommended" — finds prescriptions
Search for: "improves", "increases", "reduces", "enhances" — finds claim facts
```

**Aim for spread**: early chapters (philosophy/principles), middle chapters (practices/recipes),
late chapters (case studies/results).

---

### Book 2: An Agricultural Testament (Sir Albert Howard)
**File**: `data/extracted/An agricultural testament/document.md`
**Output**: `tests/golden_facts/an-agricultural-testament.jsonl`
**Language**: English | **Book Code**: `AT`
**Difficulty**: Medium (1940s academic English, some archaic phrasing)

**What to look for:**
- Indore Process compost making (Howard's core contribution) → `process`
- Humus formation and soil structure claims → `claim`
- Observations about crop disease resistance with well-composted soil → `observation`
- Comparisons: Indore method vs artificial fertilizers → `comparison`
- Mycorrhizal fungi role in plant nutrition → `claim`
- Crop rotation principles → `principle` or `observation`
- Animal husbandry and soil connection → `claim`

**Search tips**:
```
Search for: "Indore" — the compost process is described in detail
Search for: "humus" — multiple claim facts about water retention and fertility
Search for: "artificial manure" or "chemical fertiliser" — comparison facts
Search for: "mycorrhiza" — unique biological claim facts
Search for: "disease" — observations about crop health
```

**Note**: Howard writes in British English ("fertiliser" not "fertilizer"). Copy verbatim.

---

### Book 3: आपले हात जगन्नाथ
**File**: `data/extracted/आपले हात जगन्नाथ/document.md`
**Output**: `tests/golden_facts/aapale-haat-jagannath.jsonl`
**Language**: Marathi | **Book Code**: `AH`
**Difficulty**: Medium (requires Marathi reading ability)

**IMPORTANT**: All `subject` and `object` values must be in Devanagari script.

**What to look for:**
- जीवामृत बनवणे (making Jeevamrit) → `practice` (subject: जीवामृत)
- बीजामृत उपचार (Beejamrit seed treatment) → `practice` (subject: बीजामृत)
- गोमूत्र as pesticide — dilution ratios → `prescription`
- माती परीक्षण (soil testing) recommendations → `practice`
- पीक फेरपालट (crop rotation) rules → `principle` or `observation`
- Seasonal planting in Marathi month names (श्रावण, कार्तिक) → `observation`

**Search tips** (in the .md file):
```
Search for: "लिटर" or "किलो" — recipe facts with quantities
Search for: "जेव्हा" (when) — conditional prescriptions
Search for: "म्हणजे" (means) — definitions
Search for: "वापरावे" (should use) — prescriptions
Search for: "सुधारते", "वाढते", "कमी होते" — claim verbs
```

**Page number note**: The Markdown file has `## Page N` headers — use the number after the
nearest `## Page` header above the snippet as the page number.

---

## Phase 2 — After Tier 1 Complete (Needs PDF annotation)

For these books, annotate directly from the PDF. Note: page numbers may differ between
PDF viewer page count and the printed page number in the book — use the **printed page number**.

### Priority order within Tier 2:

#### High Priority

**Inventory of ITK Vol 1** (`inventory-itk-vol1.jsonl`, code `ITK1`)
- Dense factual content: tables of indigenous practices by crop and region
- Each table row is potentially a `practice` or `prescription` fact
- Strong multilingual — some Marathi/Hindi terms mixed in English text
- Annotate 1 table row as a fact (subject = crop/practice name, object = outcome)

**Inventory of ITK Vol 2** (`inventory-itk-vol2.jsonl`, code `ITK2`)
- Same structure as Vol 1
- Look for facts not already covered in Vol 1

**IITKA Traditional Knowledge Book** (`iitka-traditional-knowledge.jsonl`, code `IITK`)
- Academic format with references — may have numerical data (yield comparisons)
- Good source of `comparison` and `claim` facts with quantitative evidence

#### Medium Priority

**Technical Manual on Natural Farming** (`technical-manual-natural-farming.jsonl`, code `TM`)
- Government extension document — very practical, clear prescriptions
- Dosage tables, application schedules — easy `prescription` facts

**Natural Farming for Sustainable Agriculture** (`natural-farming-sustainable-agriculture.jsonl`, code `NFSA`)
- Similar to Introduction to Natural Farming but may have regional variations

#### Low Priority (Historical/Classical)

**Agriculture and Agriculturists in Ancient India** (`agriculture-ancient-india.jsonl`, code `AHI`)
**Handbook of Indian Agriculture** (`handbook-indian-agriculture.jsonl`, code `HI`)
**AgriHistory Vol 1/2/3** (`agrihistory-vol1.jsonl`, `agrihistory-vol2.jsonl`, `agrihistory-vol3.jsonl`)

These are historical reference books. Annotate only if you find clear, specific factual claims.
Avoid annotating general historical descriptions.

**Krishi Parashar** (`krishi-parashar.jsonl`, code `KP`)
**Vriksha Ayurveda** (`vriksha-ayurveda.jsonl`, code `VA`)

Sanskrit texts — annotate only if the book has English commentary alongside.
Use `language: "sa"` and keep Sanskrit terms in the subject field. Set `confidence_floor: 0.65`.

---

## Output File Checklist

Before submitting, verify each file:

```bash
# Validate JSON syntax (run in project root, requires Python):
python -c "
import json, sys
with open('tests/golden_facts/introduction-to-natural-farming.jsonl') as f:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if not line: continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            print(f'Line {i}: JSON ERROR: {e}')
            sys.exit(1)
        # Check required fields
        for field in ['fact_id','book_slug','language','type','domain_type','subject','predicate','verbatim_snippet','page_numbers','confidence_floor']:
            if field not in obj:
                print(f'Line {i}: MISSING field: {field}')
                sys.exit(1)
print('All lines valid!')
"
```

```bash
# Count unique fact_ids (should equal line count):
python -c "
import json
ids = []
with open('tests/golden_facts/introduction-to-natural-farming.jsonl') as f:
    for line in f:
        if line.strip():
            ids.append(json.loads(line)['fact_id'])
print(f'Total: {len(ids)} facts, {len(set(ids))} unique IDs')
if len(ids) != len(set(ids)):
    print('ERROR: Duplicate fact_ids found!')
"
```

---

## Summary Table

| File to create | Book | Language | Tier | Target |
|----------------|------|----------|------|--------|
| `introduction-to-natural-farming.jsonl` | Introduction to Natural Farming | en | 1 | 20 |
| `an-agricultural-testament.jsonl` | An Agricultural Testament | en | 1 | 20 |
| `aapale-haat-jagannath.jsonl` | आपले हात जगन्नाथ | mr | 1 | 20 |
| `inventory-itk-vol1.jsonl` | Inventory ITK Vol 1 | en | 2 | 20 |
| `inventory-itk-vol2.jsonl` | Inventory ITK Vol 2 | en | 2 | 20 |
| `iitka-traditional-knowledge.jsonl` | IITKA TK Book | en | 2 | 20 |
| `technical-manual-natural-farming.jsonl` | Technical Manual NF | en | 2 | 15 |
| `natural-farming-sustainable-agriculture.jsonl` | NF Sustainable Agri | en | 2 | 15 |
| `agriculture-ancient-india.jsonl` | Agriculture Ancient India | en | 2 | 10 |
| `handbook-indian-agriculture.jsonl` | Handbook Indian Agri | en | 2 | 10 |
| `krishi-parashar.jsonl` | Krishi Parashar | sa | 2 | 10 |
| `vriksha-ayurveda.jsonl` | Vriksha Ayurveda | sa/en | 2 | 10 |
| `agrihistory-vol1.jsonl` | AgriHistory Vol 1 | en | 2 | 10 |
| `agrihistory-vol2.jsonl` | AgriHistory Vol 2 | en | 2 | 10 |
| `agrihistory-vol3.jsonl` | AgriHistory Vol 3 | en | 2 | 10 |
