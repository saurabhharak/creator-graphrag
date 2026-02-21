"""Import pre-extracted Sarvam AI books into Qdrant.

Reads books from 'data/ready to use books data/' (output of sarvam_extract.py),
chunks document.md, embeds each chunk via Ollama (Qwen3-Embedding-8B), and
upserts to the Qdrant vector store.

Does NOT require Celery, Redis, or PostgreSQL — runs standalone.

Prerequisites:
  1. Qdrant running:  docker run -p 6333:6333 qdrant/qdrant
  2. Ollama running with model pulled:
       ollama pull qwen3:embedding
       ollama serve  (auto-starts on Windows)

Usage — import all books:
    python scripts/import_sarvam.py

Usage — import a single book folder:
    python scripts/import_sarvam.py --book "Introduction to Natural Farming"

Usage — re-import (overwrite existing points):
    python scripts/import_sarvam.py --force

Options:
    --books-base DIR     Base folder (default: data/ready to use books data)
    --book NAME          Single book folder name inside books-base
    --qdrant-host HOST   Qdrant host (default: localhost)
    --qdrant-port PORT   Qdrant port (default: 6333)
    --collection NAME    Qdrant collection name (default: chunks_multilingual)
    --ollama HOST        Ollama base URL (default: http://localhost:11434)
    --model MODEL        Ollama model name (default: qwen3:embedding)
    --dim N              Embedding dimension (default: 1024)
    --max-chars N        Max chars per chunk (default: 2000)
    --overlap N          Overlap chars between chunks (default: 250)
    --force              Re-import even if book points already exist
    --dry-run            Chunk and count without embedding or upserting
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path


# ── Inline chunker (no dependency on apps/worker) ─────────────────────────────

_DEVANAGARI_START = 0x0900
_DEVANAGARI_END   = 0x097F

_MR_STOPWORDS = {
    "आणि", "आहे", "हे", "की", "ते", "त्या", "च", "या", "व", "नाही",
    "तो", "ती", "हा", "ही", "मी", "तू", "आम्ही", "आपण", "त्यांना",
    "होते", "होता", "होती", "असे", "म्हणजे", "पण", "तर", "जर",
}
_HI_STOPWORDS = {
    "और", "है", "में", "से", "को", "के", "का", "की", "एक", "यह",
    "इस", "वह", "नहीं", "हैं", "पर", "भी", "तो", "कि", "जो", "इन",
    "वे", "था", "थी", "थे", "हो", "आप", "हम", "मैं", "तुम", "वो",
}

_PROCESS_RE = re.compile(
    r"\b(step|steps|method|procedure|process|how to|instruction|guideline)\b",
    re.IGNORECASE,
)
_EVIDENCE_RE = re.compile(
    r"\b(according to|research|study|studies|evidence|found that|shows?|"
    r"demonstrated?|percent|%|result[s]?|data)\b",
    re.IGNORECASE,
)
_CONCEPT_RE = re.compile(
    r"\b(is defined as|refers? to|means?|concept of|definition|known as)\b",
    re.IGNORECASE,
)

# base64 alphabet never contains ')' so [^)]+ safely matches any length URI.
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:[^)]+\)", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_HEADING_EXTRACT_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


@dataclass
class _Chunk:
    text: str
    text_hash: str
    page_start: int
    page_end: int
    section_title: str | None
    chunk_type: str
    language_detected: str
    language_confidence: float


def _deva_ratio(text: str) -> float:
    alpha = [c for c in text if unicodedata.category(c).startswith("L")]
    if not alpha:
        return 0.0
    return sum(1 for c in alpha if _DEVANAGARI_START <= ord(c) <= _DEVANAGARI_END) / len(alpha)


def _detect_lang(text: str) -> tuple[str, float]:
    r = _deva_ratio(text)
    if r > 0.6:
        words = set(text.split())
        mr = len(words & _MR_STOPWORDS)
        hi = len(words & _HI_STOPWORDS)
        if mr == 0 and hi == 0:
            return "mr", 0.70
        total = mr + hi
        return ("mr", round(0.5 + 0.5 * mr / total, 2)) if mr >= hi else ("hi", round(0.5 + 0.5 * hi / total, 2))
    if r > 0.15:
        return "mixed", round(r, 2)
    if r < 0.05:
        return "en", round(1.0 - r * 4, 2)
    return "unknown", 0.5


def _chunk_type(text: str) -> str:
    if _PROCESS_RE.search(text):
        return "process"
    if _EVIDENCE_RE.search(text):
        return "evidence"
    if _CONCEPT_RE.search(text):
        return "concept"
    return "general"


def _clean(raw: str) -> str:
    t = _IMAGE_RE.sub("", raw)
    t = _HTML_TAG_RE.sub("", t)
    t = _HEADING_RE.sub("", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _last_heading(raw: str) -> str | None:
    h = _HEADING_EXTRACT_RE.findall(raw)
    return h[-1].strip() if h else None


def _emit(out: list[_Chunk], text: str, ps: int, pe: int, sec: str | None) -> None:
    clean = text.strip()
    if len(clean) < 50:
        return
    lang, conf = _detect_lang(clean)
    out.append(_Chunk(
        text=clean,
        text_hash=hashlib.sha256(clean.encode()).hexdigest(),
        page_start=ps,
        page_end=pe,
        section_title=sec,
        chunk_type=_chunk_type(clean),
        language_detected=lang,
        language_confidence=conf,
    ))


def _chunk_document(doc_md: str, max_chars: int, overlap: int) -> list[_Chunk]:
    raw_pages = doc_md.split("\n---\n")
    chunks: list[_Chunk] = []
    buf, buf_ps, buf_pe, sec = "", 1, 1, None

    for i, raw in enumerate(raw_pages):
        pnum = i + 1
        h = _last_heading(raw)
        if h:
            sec = h
        pt = _clean(raw)
        if not pt:
            continue
        buf = (buf + "\n\n" + pt) if buf else pt
        if not buf or buf == pt:
            buf_ps = pnum
        buf_pe = pnum

        while len(buf) >= max_chars:
            ct = buf[:max_chars]
            si = ct.rfind("\n\n")
            if si > max_chars // 2:
                ct = buf[:si]
            _emit(chunks, ct, buf_ps, buf_pe, sec)
            slide = max(0, len(ct) - overlap)
            buf = buf[slide:]
            buf_ps = buf_pe

    if buf.strip():
        _emit(chunks, buf, buf_ps, buf_pe, sec)
    return chunks


# ── Book ID derivation ─────────────────────────────────────────────────────────

def _book_uuid(book_name: str) -> str:
    """Stable UUID5 from book folder name (DNS namespace)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"creator-graphrag.book.{book_name}"))


def _point_uuid(book_id: str, text_hash: str) -> str:
    """Deterministic point ID from book_id + text_hash."""
    return str(uuid.uuid5(uuid.UUID(book_id), text_hash))


# ── Embedding helpers ─────────────────────────────────────────────────────────

def _embed(
    texts: list[str],
    endpoint: str,
    model: str,
    embed_url: str | None = None,
    hf_token: str | None = None,
    hf_model: str = "Qwen/Qwen3-Embedding-8B",
    baseten_api_key: str | None = None,
    baseten_base_url: str = "https://model-q84l5lgw.api.baseten.co/environments/production/predict",
) -> list[list[float]]:
    """Embed texts via Baseten, Ollama, a custom HTTP endpoint, or HuggingFace.

    Priority:
      0. baseten_api_key set → Baseten /predict endpoint
      1. hf_token set        → HuggingFace InferenceClient (provider=scaleway)
      2. embed_url set       → custom HTTP endpoint: POST {text:...} → {embeddings:[...]}
      3. default             → Ollama /api/embeddings
    """
    vectors: list[list[float]] = []

    # ── Baseten /predict endpoint ──────────────────────────────────────────
    if baseten_api_key:
        import httpx as _httpx
        bt_headers = {"Authorization": f"Api-Key {baseten_api_key}"}
        batch_size = 16
        with _httpx.Client(timeout=300.0) as bt_client:
            for start in range(0, len(texts), batch_size):
                batch = [t for t in texts[start:start + batch_size] if t.strip()]
                if not batch:
                    continue
                resp = bt_client.post(
                    baseten_base_url,
                    headers=bt_headers,
                    json={"input": batch, "model": "model", "encoding_format": "float"},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data["data"]:
                    vectors.append(item["embedding"])
                done = min(start + batch_size, len(texts))
                if done % 25 == 0 or done == len(texts):
                    print(f"    embedded {done}/{len(texts)} (Baseten)...")
        return vectors

    if hf_token:
        from huggingface_hub import InferenceClient
        hf_client = InferenceClient(api_key=hf_token)  # HF's own free inference servers
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            result = hf_client.feature_extraction(text, model=hf_model)
            # returns numpy array shape (1, dim) or (dim,)
            import numpy as np
            arr = np.array(result).flatten().tolist()
            vectors.append(arr)
            if (i + 1) % 25 == 0:
                print(f"    embedded {i + 1}/{len(texts)}...")
        return vectors

    import httpx
    _tunnel_headers = {"Bypass-Tunnel-Reminder": "true", "User-Agent": "import-sarvam/1.0"}
    with httpx.Client(timeout=300.0) as client:
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            if embed_url:
                resp = client.post(embed_url, json={"text": text}, headers=_tunnel_headers)
                resp.raise_for_status()
                vectors.append(resp.json()["embeddings"])
            else:
                resp = client.post(f"{endpoint}/api/embeddings", json={"model": model, "prompt": text})
                resp.raise_for_status()
                vectors.append(resp.json()["embedding"])
            if (i + 1) % 25 == 0:
                print(f"    embedded {i + 1}/{len(texts)}...")
    return vectors


# ── Qdrant helpers ─────────────────────────────────────────────────────────────

def _setup_qdrant(host: str, port: int, collection: str, dim: int):
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, HnswConfigDiff, PayloadSchemaType, VectorParams

    client = QdrantClient(host=host, port=port, check_compatibility=False)
    existing = {c.name for c in client.get_collections().collections}
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            hnsw_config=HnswConfigDiff(m=16, ef_construct=200),
        )
        for field_name, schema in [
            ("book_id",           PayloadSchemaType.KEYWORD),
            ("chunk_type",        PayloadSchemaType.KEYWORD),
            ("language_detected", PayloadSchemaType.KEYWORD),
        ]:
            client.create_payload_index(
                collection_name=collection,
                field_name=field_name,
                field_schema=schema,
            )
        print(f"  Created Qdrant collection '{collection}' (dim={dim})")
    else:
        print(f"  Qdrant collection '{collection}' exists")
    return client


def _book_exists_in_qdrant(client, collection: str, book_id: str) -> bool:
    """Return True if the collection already has points for this book_id."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    result = client.scroll(
        collection_name=collection,
        scroll_filter=Filter(must=[FieldCondition(key="book_id", match=MatchValue(value=book_id))]),
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    return len(result[0]) > 0


def _upsert(client, collection: str, points: list) -> None:
    from qdrant_client.models import PointStruct
    batch_size = 64
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=collection, points=points[i : i + batch_size])


# ── Per-book import ────────────────────────────────────────────────────────────

def import_book(
    book_dir: Path,
    *,
    qdrant_client,
    collection: str,
    ollama_endpoint: str,
    model: str,
    dim: int,
    max_chars: int,
    overlap: int,
    force: bool,
    dry_run: bool,
    embed_url: str | None = None,
    hf_token: str | None = None,
    hf_model: str = "Qwen/Qwen3-Embedding-8B",
    baseten_api_key: str | None = None,
    baseten_base_url: str = "https://model-q84l5lgw.api.baseten.co/environments/production/predict",
) -> dict:
    """Import one extracted book folder. Returns a result dict."""
    doc_md = book_dir / "document.md"
    info_json = book_dir / "extraction_info.json"

    if not doc_md.exists():
        return {"status": "error", "reason": "document.md missing"}

    # Load extraction metadata
    info = {}
    if info_json.exists():
        info = json.loads(info_json.read_text(encoding="utf-8"))

    book_name = book_dir.name
    book_id = _book_uuid(book_name)
    language = info.get("language", "unknown")
    pages_processed = info.get("pages_processed", "?")

    # Fallback: detect language from document.md content if extraction_info.json absent
    if language == "unknown":
        # Strip base64 images first — they're pure ASCII and mislead Devanagari detection
        raw_doc = doc_md.read_text(encoding="utf-8")
        clean_doc = _IMAGE_RE.sub("", raw_doc)[:8000]
        lang_code, _ = _detect_lang(clean_doc)
        if lang_code in ("mr", "hi"):
            language = f"{lang_code}-IN"
        elif lang_code == "en":
            language = "en-IN"

    # Fallback: count metadata page JSON files if pages_processed unknown
    if pages_processed == "?":
        meta_dir = book_dir / "metadata"
        if meta_dir.is_dir():
            pages_processed = len(list(meta_dir.glob("page_*.json")))

    print(f"  BOOK  {book_name}")
    print(f"  ID    {book_id}")
    print(f"  LANG  {language}  |  pages: {pages_processed}")

    # Skip if already imported (unless --force)
    if not force and not dry_run and _book_exists_in_qdrant(qdrant_client, collection, book_id):
        print("  SKIP  already in Qdrant (use --force to re-import)")
        return {"status": "skipped", "book_id": book_id}

    # Chunk
    document_md = doc_md.read_text(encoding="utf-8")
    chunks = _chunk_document(document_md, max_chars, overlap)
    print(f"  CHUNK {len(chunks)} chunks (max={max_chars}, overlap={overlap})")

    if dry_run:
        lang_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        for c in chunks:
            lang_counts[c.language_detected] = lang_counts.get(c.language_detected, 0) + 1
            type_counts[c.chunk_type] = type_counts.get(c.chunk_type, 0) + 1
        print(f"  LANG  distribution: {lang_counts}")
        print(f"  TYPE  distribution: {type_counts}")
        return {"status": "dry_run", "chunks": len(chunks), "book_id": book_id}

    # Embed
    if baseten_api_key:
        src = f"Baseten ({baseten_base_url})"
    elif hf_token:
        src = f"HuggingFace/Scaleway ({hf_model})"
    elif embed_url:
        src = embed_url
    else:
        src = f"{ollama_endpoint}/api/embeddings"
    print(f"  EMBED sending {len(chunks)} texts ({src})...")
    texts = [c.text for c in chunks]
    try:
        vectors = _embed(texts, ollama_endpoint, model, embed_url=embed_url,
                         hf_token=hf_token, hf_model=hf_model,
                         baseten_api_key=baseten_api_key,
                         baseten_base_url=baseten_base_url)
    except Exception as exc:
        return {"status": "error", "reason": f"embedding failed: {exc}"}

    if len(vectors) != len(chunks):
        return {"status": "error", "reason": f"vector count mismatch: {len(vectors)} vs {len(chunks)} chunks"}

    # Build points
    from qdrant_client.models import PointStruct
    points = []
    for chunk, vec in zip(chunks, vectors):
        point_id = _point_uuid(book_id, chunk.text_hash)
        points.append(PointStruct(
            id=point_id,
            vector=vec,
            payload={
                "book_id": book_id,
                "book_name": book_name,
                "language": language,
                "chunk_type": chunk.chunk_type,
                "language_detected": chunk.language_detected,
                "language_confidence": chunk.language_confidence,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "section_title": chunk.section_title,
                "text_hash": chunk.text_hash,
                "text": chunk.text,             # store text in payload for retrieval
                "embedding_model_id": model,
            },
        ))

    # Upsert
    _upsert(qdrant_client, collection, points)
    print(f"  DONE  {len(points)} points upserted to Qdrant")
    return {"status": "done", "chunks": len(chunks), "book_id": book_id}


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Import pre-extracted Sarvam AI books into Qdrant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--books-base", metavar="DIR",
                   default=None,
                   help="Base folder with extracted books (default: data/ready to use books data)")
    p.add_argument("--book", metavar="NAME", default=None,
                   help="Single book folder name to import (inside books-base)")
    p.add_argument("--qdrant-host", default="localhost")
    p.add_argument("--qdrant-port", type=int, default=6333)
    p.add_argument("--collection", default="chunks_multilingual")
    p.add_argument("--ollama", metavar="URL", default="http://localhost:11434",
                   help="Ollama base URL (default: http://localhost:11434)")
    p.add_argument("--model", default="qwen3-embedding:8b",
                   help="Ollama model name (default: qwen3-embedding:8b)")
    p.add_argument("--dim", type=int, default=4096,
                   help="Embedding dimension (default: 4096 for qwen3-embedding:8b)")
    p.add_argument("--max-chars", type=int, default=2000)
    p.add_argument("--overlap", type=int, default=250)
    p.add_argument("--embed-url", metavar="URL", default=None,
                   help="Custom embedding endpoint (overrides Ollama). "
                        "Expects POST {text:...} → {embeddings:[...]}. "
                        "Example: https://host/generate_embeddings")
    p.add_argument("--hf-token", metavar="TOKEN", default=None,
                   help="HuggingFace token for Scaleway inference (overrides Ollama + embed-url). "
                        "Can also be set via HF_TOKEN env var.")
    p.add_argument("--hf-model", metavar="MODEL", default="Qwen/Qwen3-Embedding-8B",
                   help="HuggingFace model ID for Scaleway (default: Qwen/Qwen3-Embedding-8B)")
    p.add_argument("--baseten-api-key", metavar="KEY", default=None,
                   help="Baseten API key for OpenAI-compatible endpoint (highest priority). "
                        "Can also be set via BASETEN_API_KEY env var.")
    p.add_argument("--baseten-base-url", metavar="URL",
                   default="https://model-q84l5lgw.api.baseten.co/environments/production/predict",
                   help="Baseten base URL (default: model-q84l5lgw production endpoint)")
    p.add_argument("--force", action="store_true",
                   help="Re-import even if book already exists in Qdrant")
    p.add_argument("--dry-run", action="store_true",
                   help="Chunk and count without embedding or upserting")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    books_base = Path(args.books_base) if args.books_base else (
        repo_root / "data" / "extracted"
    )
    if not books_base.is_dir():
        print(f"ERROR: books base directory not found: {books_base}", file=sys.stderr)
        sys.exit(1)

    # Collect book directories
    if args.book:
        book_dirs = [books_base / args.book]
        if not book_dirs[0].is_dir():
            print(f"ERROR: book directory not found: {book_dirs[0]}", file=sys.stderr)
            sys.exit(1)
    else:
        book_dirs = sorted(
            d for d in books_base.iterdir()
            if d.is_dir() and (d / "document.md").exists()
        )

    if not book_dirs:
        print("No extracted book directories found (expected document.md inside each).")
        sys.exit(0)

    print(f"\nImporting {len(book_dirs)} book(s) from: {books_base}")

    embed_url: str | None = getattr(args, "embed_url", None)
    hf_token: str | None = getattr(args, "hf_token", None) or os.environ.get("HF_TOKEN")
    hf_model: str = getattr(args, "hf_model", "Qwen/Qwen3-Embedding-8B")
    baseten_api_key: str | None = getattr(args, "baseten_api_key", None) or os.environ.get("BASETEN_API_KEY")
    baseten_base_url: str = getattr(args, "baseten_base_url",
                                    "https://model-q84l5lgw.api.baseten.co/environments/production/predict")

    # Verify embedding source is reachable (unless dry-run)
    if not args.dry_run:
        import httpx
        if baseten_api_key:
            # Quick probe to verify Baseten connectivity
            try:
                r = httpx.post(
                    baseten_base_url,
                    headers={"Authorization": f"Api-Key {baseten_api_key}"},
                    json={"input": ["ping"], "model": "model", "encoding_format": "float"},
                    timeout=30.0,
                )
                r.raise_for_status()
                print(f"Baseten reachable at {baseten_base_url}")
            except Exception as exc:
                print(f"\nERROR: Baseten not reachable at {baseten_base_url}\n"
                      f"  {exc}", file=sys.stderr)
                sys.exit(1)
        elif hf_token:
            # Quick probe to verify HF/Scaleway connectivity
            try:
                from huggingface_hub import InferenceClient
                hf_client = InferenceClient(provider="scaleway", api_key=hf_token)
                result = hf_client.feature_extraction("ping", model=hf_model)
                import numpy as np
                dim = np.array(result).flatten().shape[0]
                print(f"HuggingFace/Scaleway reachable. Model: {hf_model}, dim={dim}")
            except Exception as exc:
                print(f"\nERROR: HuggingFace/Scaleway not reachable: {exc}", file=sys.stderr)
                sys.exit(1)
        elif embed_url:
            # Verify custom endpoint with a short probe
            _tunnel_headers = {"Bypass-Tunnel-Reminder": "true", "User-Agent": "import-sarvam/1.0"}
            try:
                r = httpx.post(embed_url, json={"text": "ping"}, timeout=30.0,
                               headers=_tunnel_headers)
                r.raise_for_status()
                print(f"Custom embed endpoint reachable at {embed_url}")
            except Exception as exc:
                print(
                    f"\nERROR: Custom embed endpoint not reachable at {embed_url}\n"
                    f"  {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            # Verify Ollama
            try:
                r = httpx.get(f"{args.ollama}/api/tags", timeout=5.0)
                if r.status_code != 200:
                    raise ConnectionError()
                print(f"Ollama reachable at {args.ollama}")
            except Exception:
                print(
                    f"\nERROR: Ollama not reachable at {args.ollama}\n"
                    "  Make sure Ollama is running and the model is pulled:\n"
                    f"    ollama pull {args.model}\n"
                    "  Then retry, or use --dry-run to test chunking without embedding.",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Setup Qdrant
        try:
            qdrant = _setup_qdrant(args.qdrant_host, args.qdrant_port, args.collection, args.dim)
        except Exception as exc:
            print(f"\nERROR: Cannot connect to Qdrant at {args.qdrant_host}:{args.qdrant_port}\n"
                  f"  {exc}\n"
                  "  Start Qdrant: docker run -p 6333:6333 qdrant/qdrant",
                  file=sys.stderr)
            sys.exit(1)
    else:
        qdrant = None
        embed_url = None
        hf_token = None
        print("[DRY RUN] Chunking only — no embedding or Qdrant writes\n")

    results = []
    for book_dir in book_dirs:
        print(f"\n{'-' * 60}")
        result = import_book(
            book_dir,
            qdrant_client=qdrant,
            collection=args.collection,
            ollama_endpoint=args.ollama,
            model=args.model,
            dim=args.dim,
            max_chars=args.max_chars,
            overlap=args.overlap,
            force=args.force,
            dry_run=args.dry_run,
            embed_url=embed_url,
            hf_token=hf_token,
            hf_model=hf_model,
            baseten_api_key=baseten_api_key,
            baseten_base_url=baseten_base_url,
        )
        result["name"] = book_dir.name
        results.append(result)

    # Summary
    print(f"\n{'=' * 60}")
    print("Summary:")
    for r in results:
        icon = "v" if r["status"] in ("done", "skipped", "dry_run") else "x"
        if r["status"] == "error":
            detail = r.get("reason", "unknown error")
        elif r["status"] == "skipped":
            detail = "already in Qdrant"
        else:
            detail = f"chunks={r.get('chunks', '-')}"
        print(f"  {icon}  {r['name']}  [{r['status']}]  {detail}")
    print("=" * 60)


if __name__ == "__main__":
    main()
