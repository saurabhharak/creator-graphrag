"""
PageIndex Evaluation Script
============================
Compares PageIndex tree-based retrieval against the current naive chunker,
using golden facts as ground truth.

Tests
-----
  1. Tree Coverage   — does every golden fact's verbatim_snippet appear in a tree node?
  2. Chunker Coverage — same test against current chunker output (baseline)
  3. LLM Retrieval   — does LLM tree-search return the node that contains the snippet?
                        (optional, pass --llm-eval; costs API calls)

Books under test (English, 20 golden facts each)
--------------------------------------------------
  • Introduction to Natural Farming  (introduction-to-natural-farming)
  • Agriculture and Agriculturists   (agriculture-ancient-india)
  • Vriksha Ayurveda (EN translation)(vriksha-ayurveda)

Usage
-----
  # Install PageIndex first (one-time):
  #   pip install git+https://github.com/VectifyAI/PageIndex.git

  python scripts/test_pageindex.py               # Tests 1 & 2 only (free, no LLM)
  python scripts/test_pageindex.py --llm-eval    # All 3 tests (uses Zenmux API)
  python scripts/test_pageindex.py --book vriksha-ayurveda  # Single book
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

# ── Project paths ───────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "worker"))      # for chunker import
sys.path.insert(0, str(ROOT / "vendor" / "pageindex")) # for pageindex package

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# PageIndex reads CHATGPT_API_KEY at import time.
# Map our OPENAI_API_KEY → CHATGPT_API_KEY so it works with Zenmux.
os.environ.setdefault("CHATGPT_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

# ── Book registry ────────────────────────────────────────────────────────────
BOOKS = [
    {
        "slug": "introduction-to-natural-farming",
        "dir": "Introduction to Natural Farming",
        "golden": "introduction-to-natural-farming.jsonl",
        "total_pages": 120,
    },
    {
        "slug": "agriculture-ancient-india",
        "dir": "Agriculture and Agriculturists in Ancient India",
        "golden": "agriculture-ancient-india.jsonl",
        "total_pages": 156,
    },
    {
        "slug": "vriksha-ayurveda",
        "dir": "Vriksha Ayurveda of Surapala Nalini Sadhale 1996",
        "golden": "vriksha-ayurveda.jsonl",
        "total_pages": 101,
    },
]

# ── Text helpers ─────────────────────────────────────────────────────────────

def clean_doc(path: Path) -> str:
    """Read document.md and strip base64 images to get clean text."""
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]{50,}", "", content)
    content = re.sub(r"!\[Image\]\([^)]{10,}\)", "", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def _walk_nodes(nodes: list) -> list:
    """Flatten tree nodes recursively."""
    flat = []
    for node in nodes:
        flat.append(node)
        flat.extend(_walk_nodes(node.get("nodes", [])))
    return flat


def _strip_text(tree: dict) -> dict:
    """Return a compact tree copy without 'text' fields (for LLM prompt)."""
    import copy
    t = copy.deepcopy(tree)
    for node in _walk_nodes(t.get("structure", [])):
        node.pop("text", None)
    return t


def _norm(text: str) -> str:
    """Collapse all whitespace runs (newlines, tabs, multiple spaces) into a single space.

    Sarvam AI OCR inserts hard line-breaks at ~80 chars mid-sentence.
    Golden-fact verbatim_snippets are clean single-line strings from the LLM,
    so raw substring matching fails on those line breaks.  Normalizing both
    sides before comparison recovers ~25 percentage points of coverage.
    """
    return re.sub(r"\s+", " ", text).strip()


def snippet_in_nodes(snippet: str, nodes: list, key_len: int = 40) -> str | None:
    """Return the node_id of the first node whose normalized text contains
    the normalized first ``key_len`` characters of ``snippet``."""
    key = _norm(snippet[:key_len])
    if not key:
        return None
    for node in nodes:
        if key in _norm(node.get("text", "")):
            return node.get("node_id", "?")
    return None


# ── Current chunker (baseline) ───────────────────────────────────────────────

def run_chunker(md_text: str) -> tuple[list, list]:
    """Run both chunker variants and return (char_chunk_texts, header_chunk_texts)."""
    from app.pipelines.chunker import chunk_document, chunk_document_by_headers
    char_chunks = chunk_document(md_text, max_chars=2000, overlap_chars=250)
    hdr_chunks = chunk_document_by_headers(md_text, max_chars=6000, overlap_chars=200)
    return [c.text for c in char_chunks], [c.text for c in hdr_chunks]


def snippet_in_chunks(snippet: str, chunks: list, key_len: int = 40) -> bool:
    """Return True if the normalized snippet key appears in any normalized chunk."""
    key = _norm(snippet[:key_len])
    if not key:
        return False
    return any(key in _norm(c) for c in chunks)


# ── Tree builder ─────────────────────────────────────────────────────────────

async def build_tree(md_path: Path) -> dict:
    """Build a PageIndex tree from a cleaned markdown file.

    Notes:
    - if_add_node_summary="no" → NO LLM API calls (purely markdown parsing)
    - if_thinning=False        → skip tiktoken token counting (avoids model
                                  name compatibility issues with 'openai/gpt-4.1')
    - if_add_node_text="yes"   → keep raw section text for snippet lookup
    """
    from pageindex.page_index_md import md_to_tree  # type: ignore

    tree = await md_to_tree(
        md_path=str(md_path),
        if_thinning=False,           # skip thinning (avoids tiktoken model lookup)
        if_add_node_summary="no",    # NO LLM calls — pure header parsing
        if_add_doc_description="no",
        if_add_node_text="yes",      # keep text in nodes for eval
        if_add_node_id="yes",
        model="gpt-4o",              # only used by tiktoken if thinning is on
    )
    return tree


# ── LLM retrieval test ───────────────────────────────────────────────────────

def llm_tree_search(query: str, compact_tree: dict, client) -> tuple[list[str], str]:
    """Ask the LLM to pick relevant node_ids from the tree."""
    prompt = f"""You are given a query about traditional/ancient Indian agriculture and the structure of a knowledge document.
Find all tree nodes that are most likely to contain the answer.

Query: {query}

Document tree:
{json.dumps(compact_tree, ensure_ascii=False, indent=2)}

Reply ONLY in valid JSON (no markdown fences):
{{
  "thinking": "<brief reasoning>",
  "node_list": ["0001", "0005"]
}}"""

    resp = client.chat.completions.create(
        model="openai/gpt-4.1",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=512,
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    result = json.loads(raw)
    return result.get("node_list", []), result.get("thinking", "")


# ── Report helpers ───────────────────────────────────────────────────────────

def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100*n//d if d else 0}%)"


def print_header(title: str) -> None:
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


def print_subheader(title: str) -> None:
    print(f"\n  {'─'*55}")
    print(f"  {title}")
    print(f"  {'─'*55}")


# ── Main evaluation ──────────────────────────────────────────────────────────

async def evaluate_book(book: dict, do_llm: bool, client=None) -> dict:
    slug = book["slug"]
    md_path = ROOT / "data" / "extracted" / book["dir"] / "document.md"
    golden_path = ROOT / "tests" / "golden_facts" / book["golden"]

    print_subheader(f"Book: {slug}")

    # ── Load golden facts ──
    if not golden_path.exists():
        print(f"  [SKIP] No golden facts file: {golden_path.name}")
        return {}

    facts = []
    with open(golden_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                facts.append(json.loads(line))
    print(f"  Golden facts: {len(facts)}")

    # ── Check document.md ──
    if not md_path.exists():
        print(f"  [SKIP] No document.md at {md_path}")
        return {}

    # ── Clean text ──
    print("  Cleaning document.md (stripping base64)...")
    t0 = time.time()
    clean_text = clean_doc(md_path)
    print(f"  Clean text: {len(clean_text):,} chars ({time.time()-t0:.1f}s)")

    # Write cleaned text to a temp file for PageIndex
    tmp_md = ROOT / "data" / "extracted" / book["dir"] / "_clean_for_pageindex.md"
    tmp_md.write_text(clean_text, encoding="utf-8")

    # ── Build PageIndex tree ──
    print("  Building PageIndex tree (markdown header parsing)...")
    t0 = time.time()
    try:
        tree = await build_tree(tmp_md)
        nodes = _walk_nodes(tree.get("structure", []))
        elapsed = time.time() - t0
        print(f"  Tree: {len(nodes)} nodes in {elapsed:.1f}s")

        # Count nodes that have meaningful text
        nodes_with_text = [n for n in nodes if len(n.get("text", "")) > 50]
        print(f"  Nodes with text (>50 chars): {len(nodes_with_text)}")

        # Show node titles (top-level only)
        top_nodes = tree.get("structure", [])
        print(f"  Top-level sections ({len(top_nodes)}):")
        for n in top_nodes[:10]:
            title = n.get("title", "?")[:60]
            nchildren = len(n.get("nodes", []))
            print(f"    [{n.get('node_id','?')}] {title} ({nchildren} children)")
        if len(top_nodes) > 10:
            print(f"    ... +{len(top_nodes)-10} more")

    except Exception as e:
        print(f"  [ERROR] Tree build failed: {e}")
        tmp_md.unlink(missing_ok=True)
        return {}

    # ── Test 1: Tree Coverage ──
    print("\n  [Test 1] Tree Coverage — snippet in tree node?")
    tree_hits = 0
    tree_misses = []
    for fact in facts:
        snippet = fact.get("verbatim_snippet", "")
        hit_node = snippet_in_nodes(snippet, nodes)
        if hit_node:
            tree_hits += 1
        else:
            tree_misses.append(fact)

    print(f"  Coverage: {pct(tree_hits, len(facts))}")
    if tree_misses:
        print(f"  Missed facts ({len(tree_misses)}):")
        for f in tree_misses[:5]:
            snip = f.get("verbatim_snippet", "")[:60]
            print(f"    [{f['fact_id']}] \"{snip}...\"")
        if len(tree_misses) > 5:
            print(f"    ... +{len(tree_misses)-5} more")

    # ── Test 2: Chunker Coverage (Baseline vs Header-based) ──
    print("\n  [Test 2] Chunker Coverage — char-based (old) vs header-based (new)")
    t0 = time.time()
    char_chunks, hdr_chunks = [], []
    char_hits = hdr_hits = 0
    try:
        char_chunks, hdr_chunks = run_chunker(clean_text)
        chunk_elapsed = time.time() - t0

        char_hits = sum(1 for f in facts if snippet_in_chunks(f.get("verbatim_snippet", ""), char_chunks))
        hdr_hits  = sum(1 for f in facts if snippet_in_chunks(f.get("verbatim_snippet", ""), hdr_chunks))

        avg_char = sum(len(c) for c in char_chunks) // len(char_chunks) if char_chunks else 0
        avg_hdr  = sum(len(c) for c in hdr_chunks)  // len(hdr_chunks)  if hdr_chunks  else 0

        print(f"  Char-based  : {len(char_chunks)} chunks (avg {avg_char:,} chars)  → coverage {pct(char_hits, len(facts))}")
        print(f"  Header-based: {len(hdr_chunks)} chunks (avg {avg_hdr:,} chars)  → coverage {pct(hdr_hits, len(facts))}")
        print(f"  Built in {chunk_elapsed:.2f}s")
        chunk_hits = hdr_hits  # use header-based as the primary metric
    except Exception as e:
        print(f"  [ERROR] Chunker failed: {e}")
        chunk_hits = 0

    # ── Test 3: LLM Retrieval (optional) ──
    llm_hits_at1 = 0
    llm_hits_at3 = 0
    llm_results = []

    if do_llm and client:
        print("\n  [Test 3] LLM Tree Retrieval — does LLM find the right node?")
        compact = _strip_text(tree)
        node_map = {n.get("node_id", ""): n for n in nodes}

        for i, fact in enumerate(facts):
            query = f"{fact.get('subject','')} {fact.get('predicate','')} {fact.get('object','')}"
            query = query.strip()
            snippet = fact.get("verbatim_snippet", "")
            key = snippet[:40].strip()

            try:
                node_ids, thinking = llm_tree_search(query, compact, client)
                # Check hits
                hit_at1 = False
                hit_at3 = False
                for rank, nid in enumerate(node_ids[:3]):
                    text = node_map.get(nid, {}).get("text", "")
                    if key in text:
                        if rank == 0:
                            hit_at1 = True
                        hit_at3 = True
                        break

                if hit_at1:
                    llm_hits_at1 += 1
                if hit_at3:
                    llm_hits_at3 += 1

                status = "HIT@1" if hit_at1 else ("HIT@3" if hit_at3 else "MISS")
                print(f"    [{i+1:02d}] {status:6s} | q: {query[:50]}")
                llm_results.append({
                    "fact_id": fact["fact_id"],
                    "query": query,
                    "returned_nodes": node_ids,
                    "hit_at1": hit_at1,
                    "hit_at3": hit_at3,
                    "thinking": thinking,
                })

                time.sleep(0.5)  # avoid rate limits

            except Exception as e:
                print(f"    [{i+1:02d}] ERROR: {e}")
                llm_results.append({
                    "fact_id": fact["fact_id"],
                    "error": str(e),
                    "hit_at1": False,
                    "hit_at3": False,
                })

        print(f"\n  LLM Retrieval Summary:")
        print(f"  Hit@1 (top node correct): {pct(llm_hits_at1, len(facts))}")
        print(f"  Hit@3 (top-3 nodes correct): {pct(llm_hits_at3, len(facts))}")

    # ── Cleanup temp file ──
    tmp_md.unlink(missing_ok=True)

    return {
        "slug": slug,
        "facts_total": len(facts),
        "tree_nodes": len(nodes),
        "tree_nodes_with_text": len(nodes_with_text),
        "tree_coverage": tree_hits,
        "tree_coverage_pct": round(100 * tree_hits / len(facts), 1) if facts else 0,
        "char_chunk_count": len(char_chunks),
        "hdr_chunk_count": len(hdr_chunks),
        "char_coverage": char_hits,
        "char_coverage_pct": round(100 * char_hits / len(facts), 1) if facts else 0,
        "hdr_coverage": hdr_hits,
        "hdr_coverage_pct": round(100 * hdr_hits / len(facts), 1) if facts else 0,
        "chunk_coverage": chunk_hits,
        "chunk_coverage_pct": round(100 * chunk_hits / len(facts), 1) if facts else 0,
        "llm_hit_at1": llm_hits_at1,
        "llm_hit_at3": llm_hits_at3,
        "llm_hit_at1_pct": round(100 * llm_hits_at1 / len(facts), 1) if facts and do_llm else None,
        "llm_hit_at3_pct": round(100 * llm_hits_at3 / len(facts), 1) if facts and do_llm else None,
    }


# ── Summary table ─────────────────────────────────────────────────────────────

def print_summary(results: list[dict], do_llm: bool) -> None:
    print_header("SUMMARY — PageIndex vs Chunkers")

    # Header
    if do_llm:
        print(f"  {'Book':<30} {'Facts':>5} {'Nodes':>5} {'TreeCov':>8} {'CharCov':>8} {'HdrCov':>7} {'LLM@1':>6} {'LLM@3':>6}")
        print(f"  {'─'*30} {'─'*5} {'─'*5} {'─'*8} {'─'*8} {'─'*7} {'─'*6} {'─'*6}")
    else:
        print(f"  {'Book':<30} {'Facts':>5} {'Nodes':>5} {'TreeCov':>8} {'CharCov':>8} {'HdrCov':>7}")
        print(f"  {'─'*30} {'─'*5} {'─'*5} {'─'*8} {'─'*8} {'─'*7}")

    for r in results:
        if not r:
            continue
        slug = r["slug"][:30]
        char_pct = r.get("char_coverage_pct", r.get("chunk_coverage_pct", 0))
        hdr_pct  = r.get("hdr_coverage_pct",  r.get("chunk_coverage_pct", 0))
        if do_llm:
            print(
                f"  {slug:<30} {r['facts_total']:>5} {r['tree_nodes']:>5} "
                f"{r['tree_coverage_pct']:>7.1f}% {char_pct:>7.1f}% {hdr_pct:>6.1f}% "
                f"{r.get('llm_hit_at1_pct') or 0:>5.1f}% {r.get('llm_hit_at3_pct') or 0:>5.1f}%"
            )
        else:
            print(
                f"  {slug:<30} {r['facts_total']:>5} {r['tree_nodes']:>5} "
                f"{r['tree_coverage_pct']:>7.1f}% {char_pct:>7.1f}% {hdr_pct:>6.1f}%"
            )

    # Totals
    total_facts     = sum(r.get("facts_total", 0)     for r in results if r)
    total_tree_hits = sum(r.get("tree_coverage", 0)   for r in results if r)
    total_char_hits = sum(r.get("char_coverage", r.get("chunk_coverage", 0)) for r in results if r)
    total_hdr_hits  = sum(r.get("hdr_coverage",  r.get("chunk_coverage", 0)) for r in results if r)
    print(f"\n  {'TOTAL':<30} {total_facts:>5}")
    print(f"  PageIndex tree coverage:   {pct(total_tree_hits, total_facts)}")
    print(f"  Char-based chunk coverage: {pct(total_char_hits, total_facts)}")
    print(f"  Header-based chunk cov:    {pct(total_hdr_hits,  total_facts)}")

    print("\n  Legend:")
    print("  TreeCov — % of facts whose snippet is in a PageIndex node (ws-normalized)")
    print("  CharCov — % of facts in a 2000-char sliding-window chunk (old default)")
    print("  HdrCov  — % of facts in a ## header-bounded chunk (new default)")
    if do_llm:
        print("  LLM@1   — % correct when LLM's top choice node contains the snippet")
        print("  LLM@3   — % correct within LLM's top-3 node choices")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="PageIndex evaluation against golden facts")
    parser.add_argument("--llm-eval", action="store_true", help="Run LLM retrieval test (uses API)")
    parser.add_argument("--book", type=str, default=None, help="Test a single book by slug")
    args = parser.parse_args()

    print_header("PageIndex Evaluation — Creator GraphRAG")
    print(f"  Root: {ROOT}")
    print(f"  LLM retrieval test: {'YES' if args.llm_eval else 'NO (pass --llm-eval to enable)'}")

    # Check PageIndex import
    try:
        from pageindex.page_index_md import md_to_tree  # type: ignore  # noqa
        print("  PageIndex: INSTALLED ✓")
    except ImportError:
        print("\n  ERROR: PageIndex is not installed.")
        print("  Install it with:")
        print("    pip install git+https://github.com/VectifyAI/PageIndex.git")
        sys.exit(1)

    # LLM client (only if needed)
    client = None
    if args.llm_eval:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ["OPENAI_BASE_URL"],
        )
        print("  LLM client: Zenmux ✓")

    # Filter books
    books = BOOKS
    if args.book:
        books = [b for b in BOOKS if args.book in b["slug"]]
        if not books:
            print(f"\n  ERROR: No book matching '{args.book}'")
            print(f"  Available: {', '.join(b['slug'] for b in BOOKS)}")
            sys.exit(1)

    # Run evaluations
    results = []
    for book in books:
        result = await evaluate_book(book, do_llm=args.llm_eval, client=client)
        results.append(result)

    # Save raw results
    out_path = ROOT / "tests" / "pageindex_eval_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Raw results saved → {out_path.relative_to(ROOT)}")

    # Print summary table
    print_summary(results, do_llm=args.llm_eval)

    print("\n  Done.\n")


if __name__ == "__main__":
    asyncio.run(main())
