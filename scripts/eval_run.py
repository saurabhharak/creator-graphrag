"""Golden Query Evaluation Runner (US-TEST-01).

Runs every golden query against the hybrid search endpoint and reports:
  - recall@k    : fraction of expected chunk_ids found in top-k results
  - precision@k : fraction of top-k results that are expected
  - book_recall : fraction of expected books present in results
  - score_p50   : median Qdrant cosine score across results (quality signal)
  - citation_ok : whether citation_coverage ≥ min_citation_coverage

Two execution modes
───────────────────
  HTTP mode (default)
    Calls a live API server.  Requires DEV_API_TOKEN env var OR --email/--password
    to auto-login before running queries.

      python scripts/eval_run.py \\
          --queries tests/golden_queries/golden_queries.jsonl \\
          --api-base http://localhost:8000

  In-process mode  (--in-process, recommended for CI)
    Boots the FastAPI ASGI app inline via httpx.AsyncClient — no live server needed.
    Registers a throwaway user automatically.

      python scripts/eval_run.py \\
          --queries tests/golden_queries/golden_queries.jsonl \\
          --in-process

Discover mode  (--discover)
───────────────────────────
  Run once to populate expected_chunk_ids / expected_book_ids in the JSONL.
  Saves a new file with `_discovered` suffix you can review and promote to canonical.

      python scripts/eval_run.py \\
          --queries tests/golden_queries/golden_queries.jsonl \\
          --in-process --discover --top-k 10

Baseline / regression
─────────────────────
  Save a run as baseline:
      python scripts/eval_run.py ... --output eval_results/baseline.json

  Compare future runs:
      python scripts/eval_run.py ... --baseline eval_results/baseline.json
  → exits with code 1 if avg recall@k drops > --fail-threshold (default 5%).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure Unicode output works on Windows (Devanagari / emoji in queries)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_queries(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _compute_metrics(query: dict, results: list[dict]) -> dict:
    expected_chunks = set(query.get("expected_chunk_ids") or [])
    expected_books  = set(query.get("expected_book_ids") or [])
    result_chunks   = {r["chunk_id"] for r in results}
    result_books    = {r["book_id"] for r in results}

    recall_k = (
        len(expected_chunks & result_chunks) / len(expected_chunks)
        if expected_chunks else 1.0           # no ground truth → not penalised
    )
    precision_k = (
        len(expected_chunks & result_chunks) / len(result_chunks)
        if result_chunks else 0.0
    )
    book_recall = (
        len(expected_books & result_books) / len(expected_books)
        if expected_books else 1.0
    )
    scores = [r.get("score", 0.0) for r in results]
    score_p50 = statistics.median(scores) if scores else 0.0
    covered   = sum(1 for r in results if r.get("citations"))
    cov_frac  = covered / len(results) if results else 0.0
    min_cov   = query.get("min_citation_coverage", 0.9)

    return {
        "query":                query["query"],
        "language":             query.get("language", "?"),
        "recall_at_k":          round(recall_k, 3),
        "precision_at_k":       round(precision_k, 3),
        "book_recall":          round(book_recall, 3),
        "score_p50":            round(score_p50, 4),
        "citation_coverage":    round(cov_frac, 3),
        "min_citation_coverage":min_cov,
        "citation_ok":          cov_frac >= min_cov,
        "result_count":         len(results),
        "result_chunk_ids":     [r["chunk_id"] for r in results],
        "result_book_ids":      list({r["book_id"] for r in results}),
    }


def _print_table(rows: list[dict], top_k: int) -> None:
    cols = [
        ("language",  8, "lang"),
        ("recall_at_k", 9, f"rec@{top_k}"),
        ("precision_at_k", 9, f"pre@{top_k}"),
        ("book_recall", 9, "book_rec"),
        ("score_p50", 9, "score_p50"),
        ("citation_ok", 6, "cit_ok"),
        ("result_count", 5, "n"),
    ]
    header = "  ".join(f"{label:>{width}}" for _, width, label in cols)
    sep    = "  ".join("-" * w for _, w, _ in cols)
    print(f"\n  {'query':<42}  " + header)
    print(f"  {'-'*42}  " + sep)
    for r in rows:
        q_disp = r.get("query", "")[:41]
        vals = []
        for key, width, _ in cols:
            v = r.get(key, "")
            if isinstance(v, bool):
                v = "✓" if v else "✗"
            elif isinstance(v, float):
                v = f"{v:.3f}"
            vals.append(f"{str(v):>{width}}")
        status = "PASS" if r.get("recall_at_k", 0) >= 0.8 else "FAIL"
        print(f"  [{status}] {q_disp:<41}  " + "  ".join(vals))


# ── HTTP client factories ──────────────────────────────────────────────────────

async def _make_http_client(api_base: str, email: str | None, password: str | None):
    """Return (client, token) for HTTP mode."""
    import httpx
    token = None

    if email and password:
        # Try login first, then register
        for route in ["/v1/auth/login", None]:
            if route:
                r = httpx.post(f"{api_base}{route}",
                               json={"email": email, "password": password}, timeout=10)
                if r.status_code == 200:
                    token = r.json().get("access_token")
                    break
        if not token:
            r = httpx.post(f"{api_base}/v1/auth/register",
                           json={"email": email, "password": password,
                                 "display_name": "EvalRunner"}, timeout=10)
            r.raise_for_status()
            token = r.json().get("access_token")

    token = token or ""
    client = httpx.AsyncClient(base_url=api_base, timeout=30)
    return client, token


async def _make_inprocess_client():
    """Boot the FastAPI app inline and return (client, token)."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

    import httpx
    from httpx import ASGITransport

    # Silence structlog box-drawing chars on Windows
    import structlog
    structlog.configure(
        processors=[structlog.processors.add_log_level,
                    structlog.dev.ConsoleRenderer(colors=False)],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )

    from app.main import app  # type: ignore

    client = httpx.AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test", timeout=60)

    # Register a throwaway user
    email    = f"eval_{uuid.uuid4().hex[:8]}@example.com"
    password = "EvalPass123!"
    r = await client.post("/v1/auth/register",
                          json={"email": email, "password": password,
                                "display_name": "EvalRunner"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Register failed {r.status_code}: {r.text}")
    r.raise_for_status()
    token = r.json()["access_token"]
    return client, token


# ── Core eval loop ────────────────────────────────────────────────────────────

async def _run_eval(
    queries: list[dict],
    client,
    token: str,
    top_k: int,
    search_prefix: str = "",
) -> list[dict]:
    """Run all queries; return list of metric dicts."""
    headers = {"Authorization": f"Bearer {token}"}
    results = []

    for i, q in enumerate(queries, 1):
        print(f"  [{i:>2}/{len(queries)}] {q['query'][:60]}", end=" … ", flush=True)
        try:
            resp = await client.post(
                f"{search_prefix}/v1/search",
                json={
                    "query": q["query"],
                    "query_language": q.get("language"),
                    "top_k": top_k,
                    "graph": {"enable": False},   # keep fast; graph is optional
                },
                headers=headers,
            )
            resp.raise_for_status()
            hits = resp.json().get("results", [])
            m = _compute_metrics(q, hits)
            results.append(m)
            print(f"recall={m['recall_at_k']:.2f}  score_p50={m['score_p50']:.3f}  n={m['result_count']}")
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append({"query": q["query"], "language": q.get("language", "?"),
                             "error": str(exc), "recall_at_k": 0.0})

    return results


# ── main ──────────────────────────────────────────────────────────────────────

async def _async_main(args: argparse.Namespace) -> int:
    queries = _load_queries(args.queries)
    print(f"\nLoaded {len(queries)} golden queries from {args.queries}")
    print(f"Mode: {'in-process' if args.in_process else 'HTTP → ' + args.api_base}")
    print(f"top_k={args.top_k}  discover={args.discover}\n")

    if args.in_process:
        client, token = await _make_inprocess_client()
        search_prefix = ""
    else:
        env_token = __import__("os").environ.get("DEV_API_TOKEN", "")
        client, token = await _make_http_client(
            args.api_base,
            args.email or None,
            args.password or None,
        )
        token = token or env_token
        search_prefix = ""

    try:
        metrics = await _run_eval(queries, client, token, args.top_k, search_prefix)
    finally:
        await client.aclose()

    # ── Summary table ──
    _print_table(metrics, args.top_k)

    valid = [m for m in metrics if "error" not in m]
    if not valid:
        print("\nAll queries errored — aborting.")
        return 1

    avg_recall    = sum(m["recall_at_k"]       for m in valid) / len(valid)
    avg_precision = sum(m["precision_at_k"]    for m in valid) / len(valid)
    avg_score_p50 = sum(m["score_p50"]         for m in valid) / len(valid)
    avg_coverage  = sum(m["citation_coverage"] for m in valid) / len(valid)
    citation_pass = sum(1 for m in valid if m.get("citation_ok")) / len(valid)

    lang_groups: dict[str, list[float]] = {}
    for m in valid:
        lang_groups.setdefault(m["language"], []).append(m["recall_at_k"])

    print(f"\n{'─'*70}")
    print(f"  avg recall@{args.top_k}      : {avg_recall:.3f}")
    print(f"  avg precision@{args.top_k}   : {avg_precision:.3f}")
    print(f"  avg score p50       : {avg_score_p50:.4f}")
    print(f"  avg citation cov    : {avg_coverage:.3f}")
    print(f"  citation pass rate  : {citation_pass:.0%}")
    print(f"  queries with results: {sum(1 for m in valid if m['result_count'] > 0)}/{len(valid)}")
    print(f"\n  Recall by language:")
    for lang, recalls in sorted(lang_groups.items()):
        print(f"    {lang:>4} : {statistics.mean(recalls):.3f}  (n={len(recalls)})")

    # ── Discover: update JSONL with found chunk/book IDs ──
    if args.discover:
        updated = []
        for q, m in zip(queries, metrics):
            entry = dict(q)
            if "error" not in m and m["result_count"] > 0:
                entry["expected_chunk_ids"] = m["result_chunk_ids"]
                entry["expected_book_ids"]  = m["result_book_ids"]
            updated.append(entry)
        disc_path = Path(args.queries).with_suffix("").as_posix() + "_discovered.jsonl"
        with open(disc_path, "w", encoding="utf-8") as f:
            for entry in updated:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"\n  Discovered IDs written to: {disc_path}")
        print(f"  Review and rename to golden_queries.jsonl to use as baseline.")

    # ── Save results JSON ──
    output_path = args.output or (
        f"eval_results/eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp":             datetime.now(timezone.utc).isoformat(),
        "top_k":                 args.top_k,
        "query_count":           len(queries),
        "valid_count":           len(valid),
        "avg_recall":            round(avg_recall, 4),
        "avg_precision":         round(avg_precision, 4),
        "avg_score_p50":         round(avg_score_p50, 4),
        "avg_citation_coverage": round(avg_coverage, 4),
        "citation_pass_rate":    round(citation_pass, 4),
        "recall_by_language":    {k: round(statistics.mean(v), 4)
                                  for k, v in lang_groups.items()},
        "results":               metrics,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved → {output_path}")

    # ── Regression check ──
    exit_code = 0
    if args.baseline:
        with open(args.baseline) as f:
            baseline = json.load(f)
        base_recall = baseline.get("avg_recall", 0.0)
        regression  = base_recall - avg_recall
        if regression > args.fail_threshold:
            print(f"\n  REGRESSION: recall dropped {regression:.3f} "
                  f"(baseline={base_recall:.3f} → current={avg_recall:.3f})")
            exit_code = 1
        else:
            print(f"\n  No regression  (baseline={base_recall:.3f} → current={avg_recall:.3f})")

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Golden query evaluation runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--queries",  required=True,
                        help="Path to golden_queries.jsonl")
    parser.add_argument("--top-k",   type=int, default=10,
                        help="Number of results to retrieve per query (default: 10)")
    parser.add_argument("--in-process", action="store_true",
                        help="Run against inline ASGI app (no live server needed)")
    parser.add_argument("--api-base",  default="http://localhost:8000",
                        help="API base URL for HTTP mode (default: http://localhost:8000)")
    parser.add_argument("--email",    help="User email for auto-login (HTTP mode)")
    parser.add_argument("--password", help="User password for auto-login (HTTP mode)")
    parser.add_argument("--discover", action="store_true",
                        help="Capture top-k chunk IDs as expected_chunk_ids (bootstrap mode)")
    parser.add_argument("--baseline", help="Baseline JSON for regression comparison")
    parser.add_argument("--output",   help="Output path for results JSON")
    parser.add_argument("--fail-threshold", type=float, default=0.05,
                        help="Recall drop that triggers CI failure (default: 0.05)")
    args = parser.parse_args()

    code = asyncio.run(_async_main(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
