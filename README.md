# Creator GraphRAG
## Multilingual Book Knowledge Base → GraphRAG Video Content Generator

A production-grade system that ingests multilingual books (Marathi, Hindi, English),
builds a Knowledge Graph + Vector Store, and generates citation-enforced video packages.

---

## Implementation Status

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 0 | Auth (JWT RS256 + API keys), health endpoints, config | **Complete** |
| Phase 1 | Book ingestion (Sarvam AI OCR / pre-extracted), chunking, Qwen3 embeddings, vector search | **Complete** |
| Phase 2 | Knowledge unit extraction (LLM), Neo4j graph build, GraphRAG retrieval | **Complete** |
| Phase 3 | Creator Studio UI, versioning, analytics | Not started |

**71/71 integration tests passing** (auth, books, search, graph_rag).

---

## Architecture

```
apps/
  api/          FastAPI service — REST API, auth, book management, search
  worker/       Celery workers — ingestion pipeline (chunk → embed → upsert)
  web/          React + TypeScript UI (Creator Studio) — Vite + React 19, port 3000

libs/
  shared/       Contracts, types, validators

alembic/        PostgreSQL migrations (0000–0007, all tables created)
scripts/        Sarvam AI extraction, book import, DB/store init, eval
data/           Gitignored: source PDFs, extracted text, Qdrant local storage
docs/           TDD, API docs, user stories, gap analysis
tests/          Fixtures and golden queries
```

### Services (docker-compose)

| Service | Port | Purpose |
|---------|------|---------|
| api | 8000 | FastAPI REST — `/docs` for Swagger UI |
| worker | — | Celery ingestion worker |
| celery-beat | — | Scheduled tasks |
| postgres | 5432 | Primary datastore (SQLAlchemy async) |
| redis | 6379 | Task queue (DB 1) + rate limits + JTI revocation (DB 0) |
| qdrant | 6333/6334 | Vector search — collection `chunks_multilingual` (4096-dim Cosine) |
| neo4j | 7474/7687 | Knowledge graph (Phase 2) |
| minio | 9000/9001 | Object storage — S3-compatible, console at :9001 |
| otel-collector | 4317/4318 | OpenTelemetry traces (optional `--profile observability`) |

**Ollama** runs on the host machine (not in Docker) and serves Qwen3-Embedding-8B at `http://localhost:11434`.

---

## One-Time Setup

### 1. Copy and configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set POSTGRES_PASSWORD, JWT key paths, SARVAM_API_KEY
```

### 2. Generate JWT RS256 key pair

```bash
mkdir -p certs
openssl genrsa -out certs/private.pem 2048
openssl rsa -in certs/private.pem -pubout -out certs/public.pem
```

### 3. Install Ollama and pull the embedding model

```bash
# Install Ollama from https://ollama.com
ollama pull qwen3-embedding:8b    # 4.7 GB — multilingual, #1 MTEB Multilingual
```

### 4. Start infrastructure services

```bash
make docker-up
# or: docker-compose up -d
```

### 5. Run database migrations

```bash
make migrate
# or: cd apps/api && alembic upgrade head
```

### 6. Initialise Qdrant collection and Neo4j indexes

```bash
make init-stores
# Creates chunks_multilingual collection (4096-dim Cosine, HNSW m=16/ef=200)
# Creates Neo4j indexes and constraints
```

### 7. (Optional) Seed a dev user

```bash
make seed
# or: python scripts/dev_seed.py
```

---

## Ingesting Books

### Path A — Extract a new PDF with Sarvam AI (requires `SARVAM_API_KEY`)

```bash
# Single book (auto-detects language):
PYTHONIOENCODING=utf-8 python scripts/sarvam_extract.py \
    --pdf "data/books/Introduction to Natural Farming.pdf"

# Batch all PDFs in a folder:
PYTHONIOENCODING=utf-8 python scripts/sarvam_extract.py \
    --books-dir data/books/

# Force language (use for scanned/image PDFs where detection fails):
PYTHONIOENCODING=utf-8 python scripts/sarvam_extract.py \
    --pdf "data/books/MyMarathiBook.pdf" --lang mr-IN
```

Output lands in `data/extracted/<Book Name>/`:
```
document.md          # full text as Markdown
extraction_info.json # book metadata, language, page count
metadata/
  page_001.json      # per-page confidence, bounding boxes
  page_002.json
  ...
```

Supported language codes (all require `-IN` suffix): `en-IN`, `mr-IN`, `hi-IN`, `bn-IN`,
`gu-IN`, `kn-IN`, `ml-IN`, `or-IN`, `pa-IN`, `ta-IN`, `te-IN`, `ur-IN`.

### Path B — Import pre-extracted books into Qdrant

```bash
# Import a single book directory (chunks → embed via Ollama → upsert to Qdrant):
python scripts/import_sarvam.py \
    --extracted-dir "data/extracted/Introduction to Natural Farming"

# Import all books in the extracted folder:
python scripts/import_sarvam.py --all --extracted-dir data/extracted/

# Dry run (shows chunk count, no upsert):
python scripts/import_sarvam.py \
    --extracted-dir "data/extracted/Introduction to Natural Farming" --dry-run
```

> Ollama must be running (`ollama serve`) before import. Each chunk is ~1–5 seconds to embed.

### Path C — REST API ingestion (via the running API + Celery worker)

```bash
# 1. Register / login
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"dev@example.com","password":"secret123","full_name":"Dev"}'

curl -X POST http://localhost:8000/v1/auth/login \
  -d 'username=dev@example.com&password=secret123' \
  -H "Content-Type: application/x-www-form-urlencoded"
# → { "access_token": "eyJ..." }

TOKEN=eyJ...

# 2. Create book record + get presigned S3 upload URL
curl -X POST http://localhost:8000/v1/books \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"My Book","language_primary":"en","source_format":"pdf"}'
# → { "book_id": "...", "upload_url": "http://localhost:9000/..." }

# 3. Upload file directly to MinIO/S3
curl -X PUT "<upload_url>" --upload-file mybook.pdf

# 4. Confirm upload
curl -X POST http://localhost:8000/v1/books/<book_id>/upload-complete \
  -H "Authorization: Bearer $TOKEN"

# 5. Start ingestion job (queued in Celery)
curl -X POST http://localhost:8000/v1/books/<book_id>/ingest \
  -H "Authorization: Bearer $TOKEN"
# → { "job_id": "...", "status": "queued" }

# 6. Poll job status
curl http://localhost:8000/v1/jobs/<job_id> \
  -H "Authorization: Bearer $TOKEN"
# → { "status": "running", "stage": "embed", "progress": 0.77, ... }
```

### Path D — Extract Knowledge Units + Build Graph (Phase 2)

```bash
# 1. Extract Knowledge Units from imported Qdrant chunks (LLM operation)
# Uses GPT-4.1 via Zenmux (configured in .env)
python scripts/extract_knowledge_units.py --book "Introduction to Natural Farming"

# 2. Build / Update the Neo4j Knowledge Graph from PostgreSQL units
# MERGEs Concept nodes and relationships locally
python scripts/build_knowledge_graph.py
```

### DB Backups

```bash
# Safely snapshot PostgreSQL (pg_dump) and Neo4j (offline dump) to data/backups/
python scripts/backup_dbs.py
```

---

## Running the API (local dev, no Docker)

```bash
cd apps/api
pip install -e ".[dev]"                      # install deps
uvicorn app.main:app --reload --port 8000    # hot-reload dev server
```

Swagger UI: http://localhost:8000/docs
Health check: http://localhost:8000/health/live

---

## Running the Celery Worker (local dev)

```bash
# Ollama must be running first
ollama serve &

cd apps/worker
pip install -e ".[dev]"
celery -A app.worker worker --loglevel=info -Q default,ocr,embed,graph -c 4
```

Worker processes `run_ingestion` tasks: marks job `running` → chunks → embeds → upserts → marks `completed`.
Transient failures (ConnectionError, TimeoutError, OSError) are retried up to 3× with exponential back-off (60s, 120s, 240s).

---

## Running the Frontend (local dev)

The Creator Studio UI is a **React 19 + TypeScript** single-page app built with **Vite**.

### Prerequisites

- **Node.js ≥ 18** (LTS recommended) — [nodejs.org](https://nodejs.org)
- The **backend API** must be running on `http://localhost:8000` (see *Running the API* above).
  Vite proxies all `/v1/*` requests to the API automatically, so no CORS setup is needed.

### Install dependencies

```bash
cd apps/web
npm install
```

### Start the dev server

```bash
npm run dev
# → Local: http://localhost:3000
```

The app hot-reloads on every file save. API calls to `/v1/...` are transparently
proxied to `http://localhost:8000` via the Vite config.

### Other frontend commands

| Command | Purpose |
|---------|--------|
| `npm run dev` | Start Vite dev server (port 3000, HMR enabled) |
| `npm run build` | Production bundle → `apps/web/dist/` |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | ESLint check |

### Frontend tech

| Library | Version | Purpose |
|---------|---------|--------|
| React | 19 | UI framework |
| React Router | 6 | Client-side routing |
| TanStack Query | 5 | Server-state / data fetching |
| Zustand | 5 | Client-side state management |
| Lucide React | latest | Icon set |
| React Hot Toast | 2 | Notifications |

---

## Search API

```bash
# Semantic search (cross-lingual: query in any language, results from all)
curl -X POST http://localhost:8000/v1/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "natural farming soil health",
    "top_k": 5,
    "filters": {
      "languages": ["en", "mr"],
      "chunk_types": ["concept", "process"]
    }
  }'
```

Returns ranked chunks with `score`, `text_preview` (300 chars), and citation metadata.
The embedding model is `qwen3-embedding:8b` (4096-dim), running via Ollama.
Returns HTTP 503 if Ollama is not reachable.

---

## Running Tests

> **Prerequisites:** PostgreSQL, Redis, Qdrant, and Ollama (`qwen3-embedding:8b`) must all be running.

```bash
# All integration tests
make test-integration
# or:
pytest apps/api/tests/integration/ -v

# Specific suites
pytest apps/api/tests/integration/test_auth.py -v     # 14 tests
pytest apps/api/tests/integration/test_books.py -v    # 17 tests
pytest apps/api/tests/integration/test_search.py -v   # 10 tests
```

Tests use real services — no mocks for DB, Redis, Qdrant, or Ollama.
Only S3 HEAD calls (`object_exists`, `get_object_size`) are mocked inline.

---

## Development Commands

### Backend (Make)

```bash
make help             # list all commands
make docker-up        # start all docker services
make docker-down      # stop all docker services
make migrate          # run alembic migrations (head)
make migrate-down     # roll back one migration
make migrate-gen msg="add foo"  # generate new migration
make seed             # create dev user + sample data
make init-stores      # create Qdrant collection + Neo4j indexes
make lint             # ruff check + mypy
make format           # ruff format (auto-fix)
make test             # all tests
make test-unit        # unit tests only
make test-integration # integration tests (needs live services)
make eval             # golden query evaluation
make clean            # remove __pycache__ and .pyc files
```

### Frontend (npm)

```bash
cd apps/web
npm install      # install dependencies (first time)
npm run dev      # start dev server → http://localhost:3000
npm run build    # production bundle → apps/web/dist/
npm run preview  # preview production build
npm run lint     # ESLint
```

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/sarvam_extract.py` | Extract text from PDFs using Sarvam AI (OCR + layout) |
| `scripts/import_sarvam.py` | Import pre-extracted books into Qdrant (chunk → embed → upsert) |
| `scripts/extract_knowledge_units.py` | Read Qdrant chunks → extract SPO units with LLM → save to PostgreSQL |
| `scripts/build_knowledge_graph.py` | Read PostgreSQL units → MERGE nodes/relationships into Neo4j |
| `scripts/backup_dbs.py` | Create timestamped local dumps of PostgreSQL and Neo4j |
| `scripts/dev_seed.py` | Seed dev user, org, and sample books |
| `scripts/init_qdrant.py` | Create Qdrant collection with correct dimensions and indexes |
| `scripts/init_neo4j.py` | Create Neo4j constraints and indexes (Phase 2) |
| `scripts/eval_run.py` | Run golden query evaluation against `tests/golden_queries/` |

---

## Tech Stack

| Concern | Choice | Reason |
|---------|--------|--------|
| API | FastAPI + Pydantic v2 | Type safety, auto OpenAPI docs |
| Auth | JWT RS256 + API keys | Key rotation without invalidating all tokens |
| Task queue | Celery + Redis | Reliable async ingestion with retries |
| Embeddings | **Qwen3-Embedding-8B** (4096-dim) via Ollama | #1 MTEB Multilingual (score 70.58), 100+ languages, 32K context, self-hosted |
| Vector DB | Qdrant | Fast ANN, payload filtering, easy Docker setup |
| OCR / Document AI | **Sarvam AI** | Best-in-class for Indic scripts (Marathi, Hindi, Bengali…) |
| Graph DB | Neo4j 5 + APOC | Cypher, atomic MERGE, alias deduplication |
| Real-time | SSE (not WebSocket) | Simpler, HTTP/2 friendly, read-only progress streams |
| ORM | SQLAlchemy 2.0 async | `mapped_column` style, asyncpg pool |
| Pagination | Keyset cursor | Consistent perf with time-ordered data |
| Observability | structlog + OpenTelemetry | JSON logs, distributed traces |

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
  backups/                   # Auto-generated DB dumps (PostgreSQL .dump, Neo4j .dump)

certs/                       # JWT key pair (gitignored)
  private.pem
  public.pem
```

Currently indexed in Qdrant (`chunks_multilingual`):
- **973 points total**: 466 English ("An agricultural testament") + 391 English ("Introduction to Natural Farming") + 116 Marathi ("आपले हात जगन्नाथ")

Currently in Knowledge Graph:
- **3,420** extracted knowledge units in PostgreSQL
- **5,654** interconnected Concept nodes in Neo4j

---

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/register` | Create account |
| POST | `/v1/auth/login` | Get JWT tokens |
| POST | `/v1/auth/refresh` | Rotate refresh token |
| POST | `/v1/auth/logout` | Revoke JTI |
| GET | `/v1/auth/me` | Current user info |
| POST | `/v1/api-keys` | Create API key |
| GET | `/v1/api-keys` | List API keys |
| DELETE | `/v1/api-keys/{key_id}` | Revoke API key |
| POST | `/v1/books` | Create book + presigned upload URL |
| GET | `/v1/books` | List books (keyset paginated) |
| GET | `/v1/books/{id}` | Book detail + latest job status |
| POST | `/v1/books/{id}/upload-complete` | Confirm S3 upload |
| POST | `/v1/books/{id}/ingest` | Start ingestion job |
| DELETE | `/v1/books/{id}` | Soft delete |
| GET | `/v1/jobs/{job_id}` | Job status + progress |
| POST | `/v1/search` | Semantic search |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe (checks DB + Redis + Qdrant) |

---

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/TDD.md](docs/TDD.md) | Full Technical Design Document |
| [docs/GAPS.md](docs/GAPS.md) | Gap analysis + design decisions |
| [docs/USER_STORIES.md](docs/USER_STORIES.md) | 53 user stories across 14 epics |
| [docs/API.md](docs/API.md) | API reference with error codes |
| [docs/SCHEMAS.md](docs/SCHEMAS.md) | JSON schema reference |
| [docs/MIGRATIONS.md](docs/MIGRATIONS.md) | Zero-downtime migration guide |

---

## License

Proprietary. All rights reserved.
