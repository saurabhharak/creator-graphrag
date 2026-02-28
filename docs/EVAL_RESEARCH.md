# Evaluation Research — Creator GraphRAG
## Industry Standards for RAG + Knowledge Graph Pipeline Evaluation

This document captures the research session on best practices for evaluating:
1. RAG (Retrieval-Augmented Generation) pipelines
2. Knowledge Graph extraction quality
3. Chunking quality
4. Multilingual embedding performance

---

## Table of Contents
1. [What We Are Evaluating](#what-we-are-evaluating)
2. [RAGAS — The RAG Evaluation Framework](#ragas)
3. [G-Eval — LLM-as-Judge](#g-eval)
4. [Information Extraction Metrics](#information-extraction-metrics)
5. [Knowledge Graph Quality Metrics](#knowledge-graph-quality-metrics)
6. [Chunking Quality Evaluation](#chunking-quality-evaluation)
7. [Multilingual Embedding Evaluation](#multilingual-embedding-evaluation)
8. [Ground Truth Construction Strategy](#ground-truth-construction-strategy)
9. [What We Decided to Build](#what-we-decided-to-build)
10. [Production Thresholds](#production-thresholds)
11. [Common Mistakes to Avoid](#common-mistakes-to-avoid)

---

## What We Are Evaluating

Our pipeline has three distinct stages, each needing separate eval strategies:

```
PDF/Book → Chunking → Embedding → Qdrant (retrieval)
                                       ↓
                              LLM Extraction → Knowledge Units (Postgres)
                                       ↓
                              Graph Builder → Neo4j (graph)
                                       ↓
                              Video Package Generator → Script + Citations
```

| Stage | What can go wrong | Eval type needed |
|-------|-------------------|-----------------|
| Chunking | Chunks cut mid-sentence, lose context | Coherence eval (no ground truth needed) |
| Embedding / Retrieval | Wrong chunks returned for a query | Retrieval eval (golden queries) |
| KU Extraction | Facts missed, wrong type/domain, hallucinated | Extraction eval (golden facts) |
| Graph building | Missing edges, wrong labels, broken SAME_AS | Graph completeness eval |
| Video generation | Unfaithful script, missing citations | Faithfulness eval (RAGAS-style) |

---

## RAGAS

**RAGAS** (Retrieval-Augmented Generation Assessment Score) is the dominant open-source
framework for evaluating RAG pipelines end-to-end.

**GitHub**: `explodinggradients/ragas`

### The 5 Core RAGAS Metrics

| Metric | Measures | Formula approach |
|--------|----------|-----------------|
| **Faithfulness** | Does the answer contain only claims supported by the retrieved context? | LLM extracts claims → checks each against context |
| **Context Precision** | Are the retrieved chunks relevant (no noise)? | % of retrieved chunks that were actually useful |
| **Context Recall** | Did we retrieve all the chunks needed to answer? | % of ground-truth statements covered by retrieved chunks |
| **Answer Relevancy** | Is the answer on-topic to the question? | Cosine similarity of reverse-generated questions |
| **Noise Sensitivity** | Does the answer break when irrelevant context is added? | Introduce adversarial chunks, measure answer degradation |

### Production Thresholds (Industry Consensus)

```
Faithfulness        > 0.85  (critical — never hallucinate farming advice)
Context Precision   > 0.85  (clean retrieval = better generations)
Context Recall      > 0.80  (don't miss critical passages)
Answer Relevancy    > 0.80  (keep answers on-topic)
```

### Why These Matter for Our Domain

Farming and traditional knowledge advice must be **faithful** — a hallucinated dosage or wrong
recipe could harm crops or waste inputs. Faithfulness is our most critical metric.

### RAGAS Input Format

```python
from ragas import evaluate
from ragas.metrics import faithfulness, context_precision, context_recall

dataset = {
    "question": ["How is Jeevamrit prepared?"],
    "answer": ["Jeevamrit is prepared by fermenting cow dung..."],
    "contexts": [["page text chunk 1", "page text chunk 2"]],
    "ground_truth": ["Jeevamrit requires cow dung, cow urine, jaggery..."]
}
result = evaluate(dataset, metrics=[faithfulness, context_precision, context_recall])
```

---

## G-Eval

**G-Eval** is the LLM-as-judge paradigm from the paper "G-Eval: NLG Evaluation using GPT-4
with Better Human Alignment" (Liu et al., 2023).

### Core Idea

Instead of computing a metric algorithmically, ask a powerful LLM to score the output on a
structured rubric (1–5 scale per dimension), then normalize.

### Why Use It

- Works without ground truth for qualitative dimensions (fluency, coherence, relevance)
- Correlates better with human judgements than BLEU/ROUGE for open-ended text
- Customizable rubrics for domain-specific evaluation (e.g., "Is this farming advice safe?")

### Critical Warning

**Never use the same model as both generator and judge.** Score inflation of **0.15–0.20** is
consistently observed when the model evaluates its own outputs. Use a different model family.

Our setup:
- Generator: `openai/gpt-4.1-mini` (via Zenmux)
- Judge: `anthropic/claude-sonnet-4.6` (via Zenmux)

### G-Eval Rubric Example for Our Video Scripts

```
Evaluate the following farming script on a scale of 1-5 for each dimension:

FAITHFULNESS (1-5): Is every claim in the script supported by the cited source passages?
  5 = All claims directly traceable to sources
  3 = Most claims supported, 1-2 minor additions
  1 = Multiple unsupported or contradictory claims

SAFETY (1-5): Is the advice safe for a farmer to follow?
  5 = All quantities and methods are standard / well-evidenced
  3 = Minor ambiguity in dosage or timing
  1 = Potentially harmful or contradictory advice

COMPLETENESS (1-5): Does the script cover the key steps a farmer needs?
  5 = All critical steps present in logical order
  3 = Core steps present, minor omissions
  1 = Major steps missing
```

---

## Information Extraction Metrics

These measure how well the LLM extracts **structured knowledge units** from text.

### Standard Metrics

| Metric | Definition | Formula |
|--------|-----------|---------|
| **Precision** | Of all extracted units, how many are correct? | TP / (TP + FP) |
| **Recall** | Of all ground-truth facts, how many were extracted? | TP / (TP + FN) |
| **F1** | Harmonic mean of Precision and Recall | 2 × P × R / (P + R) |

### Matching Strategy

Exact string matching is too strict for extracted knowledge. Use:

1. **Exact match**: fact_id + subject + predicate + object all match literally
2. **Fuzzy match**: cosine similarity of `(subject + " " + predicate + " " + object)` embedding > 0.85
3. **Partial credit**: subject matches + predicate matches (object missing or slightly wrong)

### What TP/FP/FN Mean for Us

| Symbol | Meaning | Example |
|--------|---------|---------|
| TP (True Positive) | Golden fact was extracted correctly | "Jeevamrit fermented 48h" → found in DB ✓ |
| FP (False Positive) | Extracted unit not in golden set (may be hallucinated) | Made-up quantity not in book |
| FN (False Negative) | Golden fact not extracted | Pipeline missed the recipe entirely |

### Targets

```
Recall  ≥ 0.80  (we must not miss important facts)
F1      ≥ 0.75  (balanced precision and recall)
```

---

## Knowledge Graph Quality Metrics

KG evaluation is less standardized than RAG eval. Industry uses a combination of:

### Structural Metrics (no ground truth needed)

| Metric | What it checks | How |
|--------|---------------|-----|
| **Triple completeness** | Are all extracted entities linked? | Count nodes with 0 relationships |
| **Label coverage** | Do all nodes have an English label? | % nodes where `label_en` is not null |
| **Type coverage** | Do all concept nodes have a `domain_type`? | Count un-typed `:Concept` nodes |
| **SAME_AS coverage** | How many cross-lingual synonyms were linked? | Count `SAME_AS` edges |
| **Orphan rate** | Nodes with no connections | Should be < 5% |

### Semantic Metrics (require golden triples)

| Metric | What it checks |
|--------|---------------|
| **Triple Recall** | Did we create the expected (subject, predicate, object) triple? |
| **Entity Resolution Rate** | Did we correctly merge "Jeevamrit" / "जीवामृत" / "Jeevamrut"? |
| **Relation type accuracy** | Did we use the right relationship type (RELATED_TO vs PART_OF)? |

### Graph Eval Tooling

- **HELM** (Stanford): holistic evaluation, includes KG tasks
- **KGEval**: newer library specifically for KG quality
- Manual spot-check: run Cypher queries and human-review sample

---

## Chunking Quality Evaluation

Chunking is often the **least evaluated** stage but has large downstream impact.

### No Ground Truth Needed — Two Signal-Only Metrics

#### 1. Intra-chunk Semantic Coherence
All sentences within a chunk should be about the same topic.

```python
# For each chunk: embed all sentences, compute mean pairwise cosine sim
# Target: > 0.75 (chunks are internally coherent)
intra_chunk_coherence = mean(pairwise_cosine_sim(sentence_embeddings))
```

#### 2. Cross-Boundary Semantic Drop
The last sentence of chunk N and the first sentence of chunk N+1 should have **low** similarity
(they belong to different chunks for a reason).

```python
# For each boundary: cosine_sim(last_sentence_of_chunk_N, first_sentence_of_chunk_N+1)
# Target: < 0.50 (boundaries are at natural breaks, not mid-thought)
cross_boundary_drop = mean(boundary_cosine_similarities)
```

#### Interpretation

| Signal | Value | Meaning |
|--------|-------|---------|
| Intra-chunk coherence | > 0.75 | Good chunking — each chunk is focused |
| Intra-chunk coherence | < 0.60 | Bad chunking — mixed topics in one chunk |
| Cross-boundary drop | < 0.50 | Good chunking — breaks at natural boundaries |
| Cross-boundary drop | > 0.70 | Bad chunking — split mid-thought |

### Why This Matters for Us

Our books contain:
- Recipe blocks (highly coherent — must not split mid-recipe)
- Verse + commentary pairs (Sanskrit text then English explanation)
- Tables (must stay within one chunk or be reconstructed)

---

## Multilingual Embedding Evaluation

We use `qwen3-embedding:8b` for all languages (English, Marathi, Hindi, Sanskrit).

### What We Know

| Language | Status |
|----------|--------|
| English | Full MTEB coverage — well validated |
| Hindi | Good MTEB coverage |
| Marathi | Limited MTEB tasks — sparse evaluation data |
| Sanskrit | **Zero** dedicated MTEB tasks — no public benchmark |

**qwen3-embedding:8b** is #1 on MTEB multilingual leaderboard (score 70.58), but this does
NOT guarantee good Marathi or Sanskrit performance specifically.

### How to Validate Our Multilingual Retrieval

**Cross-lingual retrieval test**: Submit a Marathi query, expect an English chunk as the result.

Example:
```json
{
  "query": "जीवामृत कसे बनवायचे",
  "expected_lang_of_result": "en",
  "expected_book": "Introduction to Natural Farming"
}
```

If cross-lingual retrieval works, the embedding model is aligning languages in the same space.
This is exactly what our golden_queries.jsonl already tests (see existing Marathi queries).

### Hybrid Search Recommendation

For rare terminology (Sanskrit terms, Marathi crop names), BM25 + dense fusion via
**Reciprocal Rank Fusion (RRF)** gives +5–15% Recall@10 over dense-only.

Qdrant supports this natively with sparse + dense collections. This is a future improvement.

---

## Ground Truth Construction Strategy

### Three-Phase Approach (Industry Best Practice)

```
Phase 1: LLM Bootstrap (Silver Labels)
  → Ask GPT-4.1 to extract facts from each book chapter
  → Fast, cheap, but ~15% error rate
  → Creates candidate set for human review

Phase 2: Human Expert Review (Gold Labels)
  → Domain expert annotator reviews and corrects LLM output
  → Adds facts the LLM missed
  → Removes hallucinated or wrong facts
  → Target: Cohen's Kappa > 0.70 (inter-annotator agreement)
  → This is what we are building now (see tests/golden_facts/)

Phase 3: Active Learning Expansion
  → Run pipeline, find low-confidence extractions
  → Send borderline cases to human for labelling
  → Expand dataset with hard examples
```

### Why Human Review Matters

A silver-only dataset (LLM-generated ground truth evaluated by LLM) has known biases:
- LLM judges what it knows how to extract — misses culturally specific terms
- Self-consistency bias: the same model that generates also rates
- Sanskrit and rare Marathi terms often silently skipped in LLM output

**Our strategy**: Human annotates 20 golden facts per book (Phase 2). Phase 1 and 3 are future.

### Minimum Viable Ground Truth

| Eval type | Minimum for reliable metrics |
|-----------|------------------------------|
| Retrieval (chunk) | 50 queries → we have 20, need 30 more |
| Extraction (KU) | 100–200 fact-question pairs → target 20/book × 14 books = 280 |
| KG triples | 50 golden triples for graph eval → future |
| End-to-end (video scripts) | 20–30 QA pairs with model-graded answers → future |

---

## What We Decided to Build

### Now (Session 13 / current)

1. **Golden Facts dataset** (`tests/golden_facts/`)
   - 20 facts × 14 books = 280 ground-truth knowledge units
   - Human annotator writes JSONL using `ANNOTATION_GUIDE.md`
   - Tests KU extraction (type, domain_type, subject, predicate, object)

2. **Existing retrieval eval** (`tests/golden_queries/golden_queries.jsonl`)
   - 20 multilingual queries already written
   - Tests Qdrant retrieval (which chunks come back for a query)
   - Script: `scripts/eval_run.py` → `eval_results/baseline.json`

### Future (not yet built)

3. **Extraction eval script** (`scripts/eval_golden_facts.py`)
   - Queries DB for knowledge units matching golden facts
   - Reports Recall, Precision, F1 per book
   - Integrates with CI

4. **KG completeness eval** — Cypher queries checking structural KG properties

5. **Faithfulness eval** — G-Eval judge on generated video scripts

---

## Production Thresholds

Summary of targets across all eval dimensions:

| Metric | Target | Framework | Priority |
|--------|--------|-----------|----------|
| Faithfulness (video scripts) | > 0.85 | RAGAS / G-Eval | Critical |
| Context Recall (retrieval) | > 0.80 | RAGAS | High |
| Context Precision (retrieval) | > 0.85 | RAGAS | High |
| Extraction Recall (golden facts) | > 0.80 | Custom | High |
| Extraction F1 (golden facts) | > 0.75 | Custom | High |
| Intra-chunk Coherence | > 0.75 | Signal-only | Medium |
| Cross-boundary Drop | < 0.50 | Signal-only | Medium |
| Cross-lingual SAME_AS coverage | > 60% of synonyms linked | Neo4j query | Medium |
| Graph orphan rate | < 5% | Neo4j query | Low |
| Inter-annotator agreement (Cohen's κ) | > 0.70 | Annotation QA | High |

---

## Common Mistakes to Avoid

These are well-documented failure modes in the RAG/KG eval literature:

### 1. Evaluating With the Same Model That Generated
Using GPT-4.1 to both generate video scripts AND judge faithfulness inflates scores by 0.15–0.20.
**Fix**: use Claude as judge when GPT generates, and vice versa.

### 2. Ground Truth Contamination
If the LLM that extracts facts was trained on your books, it may "know" facts without actually
reading the retrieved context. **Fix**: use books published after the model's training cutoff,
or deliberately test with facts the model is unlikely to know (regional Marathi practices).

### 3. Retrieval Metric Gaming
Precision@K looks great if K is small but you retrieved 1 good chunk out of 100. **Fix**:
report both Precision@K and Recall@K. We use `min_citation_coverage` in golden_queries.jsonl
as a proxy for recall.

### 4. Ignoring Chunk Boundaries in Faithfulness
A script might be "faithful" to the retrieved chunks, but the retrieved chunks might be wrong.
**Fix**: eval retrieval and faithfulness independently. A script can score high on faithfulness
but low on context recall (retrieved wrong chunks, but was faithful to whatever it got).

### 5. Silver Labels Only
Using LLM-generated ground truth for LLM evaluation creates a self-referential loop.
**Fix**: Phase 2 human review is non-negotiable for the core evaluation set.

### 6. Ignoring Multilingual Recall Separately
Overall Recall@10 can look fine even when Marathi retrieval fails, if the English portions
of the dataset pull the average up. **Fix**: report Recall@10 broken down by query language.

### 7. Over-fitting to Eval Set
If 20 golden queries are used to tune chunking parameters, those same 20 queries cannot
measure generalization. **Fix**: hold out 20% of golden queries for final validation only.

---

## References

- RAGAS paper: "RAGAS: Automated Evaluation of Retrieval Augmented Generation" (Es et al., 2023)
- G-Eval paper: "G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment" (Liu et al., 2023)
- MTEB Leaderboard: https://huggingface.co/spaces/mteb/leaderboard
- qwen3-embedding:8b MTEB score: 70.58 (multilingual, #1 as of research date)
- HELM (Stanford): https://crfm.stanford.edu/helm/
- Cohen's Kappa for annotation agreement: target κ > 0.70 (substantial agreement)
