# Creator GraphRAG

**Multilingual Book Knowledge Base → GraphRAG Video Content Generator**

Ingest books in Marathi, Hindi, and English — extract structured knowledge, build a Neo4j concept graph, and generate citation-enforced video scripts with a single API call.

---

## Table of Contents

- [Architecture](#architecture)
- [Implementation Status](#implementation-status)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Running Services](#running-services)
- [Book Ingestion](#book-ingestion)
- [Testing](#testing)
- [API Reference](#api-reference)
- [Scripts Reference](#scripts-reference)
- [Tech Stack](#tech-stack)
- [Data Layout](#data-layout)
- [Documentation](#documentation)

---

## Architecture

```
apps/
  api/        FastAPI service — REST API, auth, book management, knowledge units, search
  worker/     Celery workers — ingestion pipeline (chunk → embed → KU extract → graph build)
  web/        React 19 + TypeScript creator studio (Vite, port 3000)

alembic/      PostgreSQL migrations (0000–0008, all tables created)
scripts/      Utility scripts — extraction, import, seeding, evaluation
data/         Gitignored: source PDFs, extracted text, Qdrant storage
docs/         TDD, API docs, user stories, gap analysis
tests/        Fixtures and golden queries
```

### Infrastructure Services

| Service | Port | Purpose |
|---------|------|---------|
| api | 8000 | FastAPI REST — Swagger UI at `/docs` |
| worker | — | Celery ingestion worker |
| postgres | 5432 | Primary datastore (SQLAlchemy 2.0 async) |
| redis | 6379 | Task queue (DB 1), JTI revocation + rate limits (DB 0) |
| qdrant | 6333/6334 | Vector search — `chunks_multilingual` (4096-dim Cosine) |
| neo4j | 7474/7687 | Knowledge graph — `http://localhost:7474` for browser |
| minio | 9000/9001 | S3-compatible object storage — console at `:9001` |

**Ollama** runs on the host (not in Docker) and serves `qwen3-embedding:8b` at `http://localhost:11434`.

---

## Implementation Status

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 0 | Auth (JWT RS256 + API keys), health endpoints, rate limiting | ✅ Complete |
| Phase 1 | Book ingestion, S3 upload, Celery pipeline, Qwen3 embeddings, vector search | ✅ Complete |
| Phase 2 | Knowledge unit extraction (LLM), Neo4j graph build, GraphRAG retrieval | ✅ Complete |
| Phase 3 | Creator Studio UI, video package generation, analytics, SSE events, ZIP export | ✅ Complete |

**113 integration tests passing** — auth (14), books (17), search (10), knowledge units (22), graph (13), video packages (26), analytics (11).

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker + Docker Compose | latest | All infrastructure services |
| Python | 3.11+ | API, worker, scripts |
| Node.js | 18+ (LTS) | Frontend only |
| Ollama | latest | Embedding model host |
| `qwen3-embedding:8b` | — | 4.7 GB — pull once |

---

## Quick Start

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env — set at minimum:
#   POSTGRES_PASSWORD, JWT key paths, OPENAI_API_KEY (Zenmux key)
```

### 2. Generate JWT RS256 key pair

```bash
mkdir -p certs
openssl genrsa -out certs/private.pem 2048
openssl rsa -in certs/private.pem -pubout -out certs/public.pem
```

### 3. Install Ollama and pull the embedding model

```bash
# Install from https://ollama.com
ollama pull qwen3-embedding:8b    # 4.7 GB, run once
```

### 4. Start infrastructure

```bash
docker-compose up -d
```

### 5. Run migrations and initialise stores

```bash
make migrate       # Alembic: head (creates all tables)
make init-stores   # Qdrant collection (4096-dim, Cosine) + Neo4j constraints
```

### 6. Seed system templates

```bash
python scripts/seed_templates.py
# Inserts 4 built-in templates: shorts_60s, explainer_5min, myth_buster, step_by_step
```

### 7. Start the API and worker

```bash
# Terminal 1 — API
cd apps/api && pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Worker (Ollama must be running)
ollama serve &
cd apps/worker && pip install -e ".[dev]"
celery -A app.worker worker --loglevel=info -Q default,ocr,embed,graph -c 4

# Terminal 3 — Frontend
cd apps/web && npm install && npm run dev
# → http://localhost:3000
```

---

## Running Services

### API (FastAPI)

```bash
cd apps/api
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs
- Liveness: http://localhost:8000/health/live
- Readiness: http://localhost:8000/health/ready

### Celery Worker

```bash
cd apps/worker
pip install -e ".[dev]"
celery -A app.worker worker --loglevel=info -Q default,ocr,embed,graph -c 4
```

The worker processes `run_ingestion` tasks through six stages:
`ocr → structure_extract → chunk → embed → unit_extract → graph_build`

Transient failures (ConnectionError, TimeoutError, OSError) retry up to 3× with exponential back-off (60 s → 120 s → 240 s).

### Frontend (Creator Studio)

```bash
cd apps/web
npm install
npm run dev        # → http://localhost:3000 (HMR enabled)
npm run build      # Production bundle → apps/web/dist/
npm run lint       # ESLint
```

All `/v1/*` requests are proxied to `http://localhost:8000` by Vite — no CORS configuration needed.

---

## Book Ingestion

### Path A — Upload via UI or REST API (Celery pipeline)

```bash
# 1. Register and log in
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"secret123","full_name":"Dev"}'

TOKEN=$(curl -s -X POST http://localhost:8000/v1/auth/login \
  -d 'username=you@example.com&password=secret123' \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)

# 2. Create book + get presigned S3 upload URL
BOOK=$(curl -s -X POST http://localhost:8000/v1/books \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"My Book","language_primary":"en","source_format":"pdf"}')

BOOK_ID=$(echo $BOOK | jq -r .book_id)
UPLOAD_URL=$(echo $BOOK | jq -r .upload_url)

# 3. Upload directly to MinIO/S3
curl -X PUT "$UPLOAD_URL" --upload-file mybook.pdf

# 4. Confirm upload
curl -X POST "http://localhost:8000/v1/books/$BOOK_ID/upload-complete" \
  -H "Authorization: Bearer $TOKEN"

# 5. Start ingestion job
JOB_ID=$(curl -s -X POST "http://localhost:8000/v1/books/$BOOK_ID/ingest" \
  -H "Authorization: Bearer $TOKEN" | jq -r .job_id)

# 6. Stream real-time progress via SSE
curl -N "http://localhost:8000/v1/jobs/$JOB_ID/events" \
  -H "Authorization: Bearer $TOKEN"
# → data: {"status":"running","stage":"embed","progress":0.77}
# → data: {"status":"completed","stage":"done","progress":1.0}
```

### Path B — Extract from PDF with Sarvam AI (requires `SARVAM_API_KEY`)

```bash
# Single book (auto-detects language)
PYTHONIOENCODING=utf-8 python scripts/sarvam_extract.py \
  --pdf "data/books/MyBook.pdf"

# Force language (for scanned/image PDFs where detection fails)
PYTHONIOENCODING=utf-8 python scripts/sarvam_extract.py \
  --pdf "data/books/MarathiBook.pdf" --lang mr-IN

# Batch all PDFs in a folder
PYTHONIOENCODING=utf-8 python scripts/sarvam_extract.py \
  --books-dir data/books/
```

Output: `data/extracted/<Book Name>/document.md`, `extraction_info.json`, `metadata/page_NNN.json`

Supported language codes (all require `-IN` suffix): `en-IN`, `mr-IN`, `hi-IN`, `bn-IN`, `gu-IN`, `kn-IN`, `ml-IN`, `ta-IN`, `te-IN`, `ur-IN`.

### Path C — Import pre-extracted books directly to Qdrant

```bash
# Requires Ollama running
python scripts/import_sarvam.py \
  --extracted-dir "data/extracted/Introduction to Natural Farming"

# Import all books
python scripts/import_sarvam.py --all --extracted-dir data/extracted/

# Dry run (shows chunk count, no upsert)
python scripts/import_sarvam.py \
  --extracted-dir "data/extracted/Introduction to Natural Farming" --dry-run
```

After script-based import, run the ownership fix to make books visible in the UI:

```bash
python scripts/fix_book_ownership.py your@email.com
```

---

## Testing

All tests use **real services** — no mocks for DB, Redis, Qdrant, or Ollama.
Only S3 HEAD calls are mocked inline.

### Prerequisites

PostgreSQL, Redis, Qdrant, and Ollama (`qwen3-embedding:8b`) must be running.

### Run tests

```bash
# All integration tests (113 tests)
pytest apps/api/tests/integration/ -v

# Individual suites
pytest apps/api/tests/integration/test_auth.py -v            # 14 tests
pytest apps/api/tests/integration/test_books.py -v           # 17 tests
pytest apps/api/tests/integration/test_search.py -v          # 10 tests
pytest apps/api/tests/integration/test_knowledge_units.py -v # 22 tests
pytest apps/api/tests/integration/test_graph.py -v           # 13 tests (5 skip if Neo4j down)
pytest apps/api/tests/integration/test_video_packages.py -v  # 26 tests (1 skip — live LLM)

# Golden query evaluation (measures recall against expected chunk IDs)
python scripts/eval_run.py --in-process --baseline
```

---

## API Reference

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/register` | Create account |
| POST | `/v1/auth/login` | OAuth2 password flow — returns JWT pair |
| POST | `/v1/auth/refresh` | Rotate refresh token (JTI revocation) |
| POST | `/v1/auth/logout` | Revoke current JTI |
| GET | `/v1/auth/me` | Current user profile |
| POST | `/v1/api-keys` | Create permanent API key (`cgr_…`) |
| GET | `/v1/api-keys` | List active API keys |
| DELETE | `/v1/api-keys/{key_id}` | Revoke API key |

### Books

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/books` | Create book record + presigned S3 upload URL |
| GET | `/v1/books` | List books (keyset cursor pagination) |
| GET | `/v1/books/{id}` | Book detail + latest job status + chunk count |
| POST | `/v1/books/{id}/upload-complete` | Confirm S3 upload (HEAD check) |
| POST | `/v1/books/{id}/ingest` | Enqueue Celery ingestion job |
| DELETE | `/v1/books/{id}` | Soft delete |

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/jobs/{job_id}` | Job status, stage, progress, metrics |
| GET | `/v1/jobs/{job_id}/events` | **SSE stream** — real-time job progress events |
| POST | `/v1/jobs/{job_id}/cancel` | Cancel a queued job |
| POST | `/v1/jobs/{job_id}/retry` | Retry a failed job |

SSE events are published to Redis channel `job:{id}:events`; the API falls back to DB polling every 3 s if Redis is slow. Streams auto-close on terminal states (`completed`, `failed`, `cancelled`).

### Search

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/search` | Semantic search — embed → Qdrant ANN → ranked results |

```json
{
  "query": "मातीची सुपीकता",
  "top_k": 10,
  "filters": {
    "book_ids": ["uuid..."],
    "languages": ["mr", "en"],
    "chunk_types": ["paragraph", "heading"],
    "page_min": 1,
    "page_max": 100
  },
  "graph_options": { "enable": true }
}
```

Returns `results[]` with `score`, `text_preview` (300 chars), citation metadata, and optionally `graph_plan.beats[]` for graph-augmented context.
Returns HTTP 503 if Ollama is unreachable.

### Knowledge Units

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/knowledge-units` | List KUs (filter by book, status, type, language) |
| GET | `/v1/knowledge-units/{unit_id}` | KU detail + edit history |
| PATCH | `/v1/knowledge-units/{unit_id}` | Update status/fields (approve, reject, edit) |
| POST | `/v1/knowledge-units/bulk-update` | Bulk status update with audit trail |

### Knowledge Graph

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/graph/concepts?q=soil` | Search concept nodes |
| GET | `/v1/graph/concepts/{key}` | Concept detail + 1-hop edges + Mermaid diagram |
| GET | `/v1/graph/concepts/{key}/neighbors` | N-hop neighborhood traversal |

### Video Packages

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/video-packages` | Generate video package from search query + template |
| GET | `/v1/video-packages` | List packages (paginated) |
| GET | `/v1/video-packages/{id}` | Package detail |
| GET | `/v1/video-packages/{id}/export?format=json` | Export as JSON |
| GET | `/v1/video-packages/{id}/export?format=zip` | **Export as ZIP** — `package.json`, `outline.md`, `script.md`, `storyboard.json`, `visual_spec.json`, `citations.json`, `evidence_map.json` |
| GET | `/v1/video-packages/{id}/versions` | Version history |

### Templates

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/templates` | List available templates (includes 4 built-in system templates) |
| POST | `/v1/templates` | Create custom template |
| GET | `/v1/templates/{id}` | Template detail |

Built-in templates: `shorts_60s` (5–7 scenes, 60 s), `explainer_5min` (8–14 scenes), `myth_buster`, `step_by_step`.

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/analytics/books/{book_id}/coverage` | Chunk count, KU counts by status, citation coverage rate |
| GET | `/v1/analytics/llm-usage` | Token usage + USD cost (admin only) — `group_by=operation\|book\|user`, date range filters |

### Evidence

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/evidence/{chunk_id}` | Full chunk text + chapter + book metadata |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health/live` | Liveness probe (always 200) |
| GET | `/health/ready` | Readiness — checks DB + Redis + Qdrant |

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/sarvam_extract.py` | PDF → Markdown via Sarvam AI OCR (Indic-optimised) |
| `scripts/import_sarvam.py` | Pre-extracted books → Qdrant (chunk → embed → upsert) |
| `scripts/seed_templates.py` | Insert 4 built-in system templates into PostgreSQL |
| `scripts/fix_book_ownership.py` | Reassign script-imported books to a real user account |
| `scripts/eval_run.py` | Golden query recall evaluation (`--baseline` for regression) |
| `scripts/init_qdrant.py` | Create/recreate Qdrant collection with correct dimensions |
| `scripts/init_neo4j.py` | Create Neo4j constraints and indexes |
| `scripts/dev_seed.py` | Create dev user, org, and sample data |
| `scripts/backup_dbs.py` | Timestamped dumps of PostgreSQL and Neo4j to `data/backups/` |

---

## Development Commands

```bash
make help             # list all targets
make docker-up        # start all Docker services
make docker-down      # stop all Docker services
make migrate          # alembic upgrade head
make migrate-gen msg="add col"  # generate migration
make init-stores      # Qdrant collection + Neo4j indexes
make seed             # dev user + sample data
make lint             # ruff check + mypy
make format           # ruff format (auto-fix)
make test             # all tests
make test-integration # integration tests (needs live services)
make eval             # golden query evaluation
make clean            # remove __pycache__ / .pyc
```

---

## Tech Stack

| Concern | Choice |
|---------|--------|
| API | FastAPI + Pydantic v2 |
| Auth | JWT RS256 (access 15 min, refresh 7 days) + API keys (`cgr_…`) |
| Task queue | Celery + Redis |
| Embeddings | **Qwen3-Embedding-8B** (4096-dim) via Ollama — #1 MTEB Multilingual |
| Vector DB | Qdrant (payload indexes: book_id, chunk_type, language_detected) |
| OCR / Document AI | Sarvam AI — best-in-class for Indic scripts |
| Graph DB | Neo4j 5 — Cypher, atomic MERGE, concept deduplication |
| LLM / Generation | OpenAI-compatible API via Zenmux (`openai/gpt-4.1`, `anthropic/claude-sonnet-4-6`) |
| Real-time | SSE over Redis pub/sub + DB poll fallback |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Pagination | Keyset cursor (consistent perf on time-ordered data) |
| Frontend | React 19 + TypeScript + Vite + React Router 6 + TanStack Query 5 |
| Observability | structlog (JSON) + OpenTelemetry + W3C traceparent |

---

## Data Layout

```
data/                        # gitignored
  books/                     # source PDFs
  extracted/                 # Sarvam AI output (one folder per book)
    Introduction to Natural Farming/
      document.md
      extraction_info.json
      metadata/page_NNN.json
    आपले हात जगन्नाथ/
      document.md
      extraction_info.json
      metadata/page_NNN.json
  qdrant_storage/            # local Qdrant data (when not using Docker volume)
  stopwords/                 # hi.txt, mr.txt (committed)
  backups/                   # auto-generated DB dumps

certs/                       # JWT key pair (gitignored)
  private.pem
  public.pem
```

### Current indexed data

**Qdrant** (`chunks_multilingual`, 4096-dim Cosine):

| Book | Language | Points | Book ID |
|------|----------|--------|---------|
| An agricultural testament | en | 466 | `00272ec0-ce39-5d32-a12a-fbb87b3c5591` |
| Introduction to Natural Farming | en | 391 | `5d3f6232-ce05-57b8-ac89-9ecff1df68ce` |
| आपले हात जगन्नाथ | mr | 116 | `2dedee82-7755-5b3b-a695-6fe32c28acc2` |
| **Total** | | **973** | |

**Knowledge Graph:**
- **3,420** extracted knowledge units in PostgreSQL
- **5,654** Concept nodes interconnected in Neo4j

---

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/TDD.md](docs/TDD.md) | Full Technical Design Document |
| [docs/USER_STORIES.md](docs/USER_STORIES.md) | 53 user stories across 14 epics |
| [docs/API.md](docs/API.md) | API reference with error codes |
| [docs/GAPS.md](docs/GAPS.md) | Gap analysis and design decisions |
| [docs/SCHEMAS.md](docs/SCHEMAS.md) | JSON schema reference |
| [docs/MIGRATIONS.md](docs/MIGRATIONS.md) | Zero-downtime migration guide |

---

## License

Proprietary. All rights reserved.
