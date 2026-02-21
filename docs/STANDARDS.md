# Engineering Standards
## Creator GraphRAG — Multilingual Book Knowledge Base → GraphRAG Video Content Generator

> **Purpose:** This document is the single source of truth for how we write, structure,
> review, and ship code. Every engineer — human or AI — must follow these standards.
> Low tech debt is not an accident; it is the result of enforced discipline at every step.

---

## Table of Contents

1. [Core Principles](#1-core-principles)
2. [Project Structure Rules](#2-project-structure-rules)
3. [Python & FastAPI Standards](#3-python--fastapi-standards)
4. [Database Standards](#4-database-standards)
5. [Worker & Pipeline Standards](#5-worker--pipeline-standards)
6. [AI / LLM Standards](#6-ai--llm-standards)
7. [Testing Standards](#7-testing-standards)
8. [Security Standards](#8-security-standards)
9. [Observability Standards](#9-observability-standards)
10. [Git & Review Standards](#10-git--review-standards)
11. [Frontend Standards](#11-frontend-standards)
12. [Tech Debt Prevention Rules](#12-tech-debt-prevention-rules)
13. [CI/CD & Deployment Standards](#13-cicd--deployment-standards)
14. [Documentation Standards](#14-documentation-standards)
15. [Definition of Done](#15-definition-of-done)

---

## 1. Core Principles

These four principles override every specific rule below. When in doubt, apply these.

### P1 — Make the Wrong Thing Hard
Structure code so that incorrect usage fails at compile/import time, not at runtime.
Use types, enums, Pydantic models, and database constraints to make invalid states unrepresentable.

### P2 — One Reason to Change
Every module, class, and function has exactly one reason to change. A router should not contain
business logic. A service should not know about HTTP. A migration should not contain application code.

### P3 — Explicit Over Implicit
No magic. No global state. No side effects at import time. Every dependency is injected.
Every configuration is validated at startup. Every decision is visible in code.

### P4 — Delete Over Abstract
Do not create abstractions for hypothetical future requirements. Write the simplest thing
that works today. When the same pattern appears three times in three real use cases, then
and only then extract it. Premature abstraction is tech debt with a smiling face.

---

## 2. Project Structure Rules

### 2.1 Layer Separation (Enforced)

The architecture has five layers. Traffic flows downward only. No layer may import from a layer above it.

```
HTTP Request
     ↓
[Router]          — HTTP concerns only: parse request, call usecase, return response
     ↓
[Usecase]         — Orchestrates domain services. No DB calls, no HTTP concerns
     ↓
[Domain Service]  — Pure business logic. No infrastructure knowledge
     ↓
[Repository]      — DB access only. Returns domain models, not ORM objects
     ↓
[Infrastructure]  — SQLAlchemy, Qdrant, Neo4j, S3, OpenAI clients
```

**Violations that will be rejected in review:**
- A router calling a repository directly → must go through usecase
- A domain service importing `sqlalchemy` → use repository interface
- A usecase importing `fastapi` → domain has no HTTP knowledge
- A repository containing business logic → move to domain service

### 2.2 File Naming Conventions

| Layer | Convention | Example |
|-------|-----------|---------|
| Router | `{resource}.py` | `books.py`, `jobs.py` |
| Usecase | `{verb}_{noun}.py` | `ingest_book.py`, `generate_video.py` |
| Service | `{noun}_service.py` | `book_service.py`, `citation_service.py` |
| Repository | `{noun}_repository.py` | `book_repository.py` |
| ORM model | `{noun}.py` in `db/models/` | `book.py`, `chunk.py` |
| Schema | `{noun}_schema.py` | `book_schema.py` |
| Test | `test_{module}.py` | `test_book_service.py` |
| Migration | `{NNNN}_{description}.py` | `0002_books_files.py` |

### 2.3 Module Size Limits

| File type | Max lines | Action if exceeded |
|-----------|----------|-------------------|
| Router | 150 lines | Split by resource sub-type |
| Usecase | 100 lines | Split into smaller usecases |
| Service | 200 lines | Split by responsibility |
| Repository | 150 lines | Split by query group |
| Test file | 300 lines | Split by feature group |
| Migration | 100 lines | Split into multiple migrations |

### 2.4 Dependency Direction

```
libs/shared/          ← imported by both api and worker (never the reverse)
apps/api/             ← never imports from apps/worker/
apps/worker/          ← never imports from apps/api/
```

Shared contracts (Pydantic schemas, enums, constants) go in `libs/shared/`.
If both api and worker need the same schema, it lives in shared.

### 2.5 Code Ownership

A `CODEOWNERS` file at the repo root maps every critical path to a responsible engineer.
Pull requests touching owned paths require the owner's approval in addition to a peer review.

```
# CODEOWNERS
/alembic/                          @data-lead
/apps/api/app/domain/              @backend-lead
/apps/api/app/infrastructure/llm/  @ml-lead
/apps/worker/app/pipelines/        @ml-lead
/apps/web/                         @frontend-lead
/docs/STANDARDS.md                 @tech-lead
/.github/                          @tech-lead
```

**Rules:**
- Every directory containing business-critical logic must have an owner entry
- Owner is accountable for review quality in their domain — not the only reviewer
- If a path has no owner, the tech lead is the implicit owner
- Update `CODEOWNERS` as part of the PR that creates a new domain directory

---

## 3. Python & FastAPI Standards

### 3.1 Type Annotations (Mandatory)

Every function must be fully type-annotated. No `Any` except at true system boundaries
(e.g., parsing raw JSON from external API).

```python
# WRONG — no types, mypy cannot help you
def process_chunk(chunk, book_id):
    return chunk.text.lower()

# CORRECT — fully typed
def process_chunk(chunk: Chunk, book_id: UUID) -> str:
    return chunk.text.lower()
```

**Rules:**
- `from __future__ import annotations` at top of every file (deferred evaluation)
- Use `X | None` not `Optional[X]` (Python 3.10+ union syntax)
- Use `list[X]` not `List[X]` (Python 3.9+ lowercase generics)
- Return types always explicit — never omit them
- `mypy --strict` must pass (or be explicitly suppressed with a comment explaining why)

### 3.2 Pydantic Models

```python
# WRONG — dict, no validation, no documentation
def create_book(data: dict) -> dict:
    ...

# CORRECT — Pydantic with explicit validation
class CreateBookRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=400, description="Book title")
    language_primary: LanguageCode  # enum, not plain str
    tags: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("tags")
    @classmethod
    def tags_lowercase(cls, v: list[str]) -> list[str]:
        return [t.lower().strip() for t in v]
```

**Rules:**
- Use `LanguageCode`, `ChunkType`, `JobStatus` enums — never plain strings for categorical values
- All string fields: specify `min_length`/`max_length` explicitly
- Request models: separate from response models — never reuse the same model for both
- Never expose ORM models directly as response schemas — always transform through Pydantic

### 3.3 Router Rules

Routers are thin. They do exactly three things:
1. Parse and validate the request (Pydantic does this automatically)
2. Call one usecase
3. Return the response

```python
# WRONG — business logic in router
@router.post("/books/{book_id}/ingest")
async def start_ingestion(book_id: UUID, body: StartIngestionRequest, user: EditorOrAdminDep):
    # checking concurrent jobs here is WRONG — business logic
    count = await db.query("SELECT COUNT(*) FROM ingestion_jobs WHERE ...")
    if count >= MAX_CONCURRENT:
        raise HTTPException(...)
    job = await db.insert(...)
    await celery.send_task(...)
    return job

# CORRECT — router delegates entirely to usecase
@router.post("/books/{book_id}/ingest", response_model=StartIngestionResponse)
async def start_ingestion(
    book_id: UUID,
    body: StartIngestionRequest,
    user: EditorOrAdminDep,
    usecase: Annotated[IngestBookUsecase, Depends(get_ingest_book_usecase)],
) -> StartIngestionResponse:
    return await usecase.execute(book_id=book_id, config=body, actor=user)
```

### 3.4 Error Handling

```python
# WRONG — raw Exception with string messages
raise Exception("Book not found")
raise HTTPException(status_code=404, detail="not found")

# CORRECT — typed application errors
raise NotFoundError(f"Book {book_id} not found")
raise JobConcurrencyError("Max 2 concurrent jobs per user")
```

**Rules:**
- Never raise `HTTPException` inside domain services or repositories — only in routers
- Never catch bare `Exception` without re-raising or logging with full context
- All application errors inherit from `AppError` (defined in `core/errors.py`)
- Errors carry machine-readable `code` fields — never parse error strings in client code

### 3.5 Async Rules

```python
# WRONG — blocking I/O in async context
async def get_book(book_id: UUID) -> Book:
    result = requests.get(f"http://s3/...")  # BLOCKS the event loop
    return parse(result)

# CORRECT — async I/O throughout
async def get_book(book_id: UUID) -> Book:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://s3/...") as response:
            return parse(await response.read())
```

**Rules:**
- Never use `time.sleep()` in async code → use `asyncio.sleep()`
- Never use `requests` in async code → use `httpx.AsyncClient`
- Never use blocking file I/O in async code → use `aiofiles`
- CPU-intensive work (OCR, embedding) → offload to Celery workers, never in FastAPI

### 3.6 Dependency Injection Pattern

All infrastructure clients (DB session, Qdrant, Neo4j, S3) must be injected through FastAPI's `Depends()`. Never instantiate clients inside usecase or service methods.

```python
# WRONG — hidden dependency, untestable
class BookRepository:
    async def get(self, book_id: UUID) -> Book:
        session = create_engine(DATABASE_URL)  # hidden!
        ...

# CORRECT — session injected
class BookRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

async def get_book_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)]
) -> BookRepository:
    return BookRepository(session=session)
```

### 3.7 Configuration

```python
# WRONG — hardcoded or os.environ directly
CHUNK_SIZE = 2000
api_key = os.environ["OPENAI_API_KEY"]

# CORRECT — typed settings from config.py
from app.core.config import settings
chunk_size = settings.CHUNK_MAX_CHARS
```

**Rules:**
- Zero hardcoded values in business logic — all constants via `settings`
- `settings` is a singleton imported from `core/config.py`
- All settings have type annotations, defaults where safe, and no defaults for secrets
- App fails fast at startup if required settings are missing (pydantic-settings enforces this)

### 3.8 API Versioning Standards

The API is currently at `v1`. Version increments are breaking changes and require careful management.

**Definition of a breaking change (requires new version):**
- Removing a field from a response
- Changing a field's type (e.g., `string` → `integer`)
- Changing the behavior of an existing endpoint in a non-backwards-compatible way
- Removing an endpoint

**Non-breaking changes (no version bump required):**
- Adding a new optional field to a response
- Adding a new endpoint
- Adding a new optional request field with a default

**When v2 is created:**
- v1 and v2 share the same service/domain layer — only routers differ
- Both versions registered in `main.py` simultaneously
- v1 is frozen: security fixes only, no new features after v2 GA
- v1 sunset policy: minimum 3-month deprecation notice before removal
- Deprecated endpoints must return `Deprecation` and `Sunset` response headers:

```python
response.headers["Deprecation"] = "true"
response.headers["Sunset"] = "Sat, 01 Jan 2027 00:00:00 GMT"
response.headers["Link"] = '</v2/books>; rel="successor-version"'
```

### 3.9 Idempotency Key Standards

Endpoints that trigger long-running operations must support the `Idempotency-Key` header
to allow safe client retries without side effects.

**Endpoints that must support idempotency:**
- `POST /v1/books/{id}/ingest`
- `POST /v1/video-packages`
- `POST /v1/webhooks`

**Implementation rules:**
- Key format: UUID v4 only — validated at router entry, reject with 400 if malformed
- Key storage: Redis with TTL = 24 hours, key = `idempotency:{user_id}:{idempotency_key}`
- If key seen within TTL: return the cached response with HTTP 200 (not 201)
- If key seen but original request is still in-flight: return 409 Conflict
- Key is scoped to the authenticated user — same key from a different user is treated independently

```python
# In router — idempotency check before usecase
idempotency_key = request.headers.get("Idempotency-Key")
if idempotency_key:
    cached = await redis.get(f"idempotency:{user.user_id}:{idempotency_key}")
    if cached:
        return JSONResponse(content=json.loads(cached), status_code=200)
```

### 3.10 Rate Limiting Standards

Rate limits protect downstream services and prevent abuse. `slowapi` is wired in `main.py`.

**Rate limit tiers:**

| Tier | Limit | Scope |
|------|-------|-------|
| Unauthenticated | 10 req/min | Per IP |
| Authenticated (viewer/editor) | 120 req/min | Per `user_id` |
| Admin | 1000 req/min | Per `user_id` |
| API key client | 300 req/min | Per `api_key` |
| `POST /v1/search` | 30 req/min | Per `user_id` (additional limit) |
| `POST /v1/video-packages` | 5 req/min | Per `user_id` (generation is expensive) |

**Rules:**
- Rate limit responses must use the `ErrorResponse` envelope — never a bare 429 body
- Include `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` headers in all responses
- Rate limit state stored in Redis — survives API restarts
- Exempt from rate limits: `/health*` and `/metrics` endpoints

---

## 4. Database Standards

### 4.1 Repository Pattern (Mandatory)

Every database table has exactly one repository. No raw SQL outside repositories.

```python
# WRONG — SQL in usecase
class IngestBookUsecase:
    async def execute(self, book_id: UUID) -> None:
        await self.session.execute(
            "UPDATE books SET status = 'ingesting' WHERE book_id = $1", book_id
        )

# CORRECT — repository method
class BookRepository:
    async def update_status(self, book_id: UUID, status: str) -> None:
        stmt = update(BookORM).where(BookORM.book_id == book_id).values(status=status)
        await self.session.execute(stmt)

class IngestBookUsecase:
    async def execute(self, book_id: UUID) -> None:
        await self.book_repo.update_status(book_id, "ingesting")
```

### 4.2 ORM Model Rules

```python
class BookORM(Base):
    __tablename__ = "books"

    book_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False  # auto-updated
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
```

**Rules:**
- Use SQLAlchemy 2.0 `Mapped[]` typed columns — never the 1.x `Column()` style
- Every table: `uuid` PK, `created_at`, `updated_at`, `deleted_at` (soft-delete)
- Never use `session.query()` (1.x API) — always `select()` + `session.execute()`
- All queries use `where(Model.deleted_at.is_(None))` to exclude soft-deleted rows
- Relationships loaded explicitly (no lazy loading in async context)

### 4.3 Migration Rules

```python
# Every migration must have BOTH upgrade() and downgrade()
def upgrade() -> None:
    op.add_column("books", sa.Column("visibility", sa.Text, nullable=True))  # expand phase
    # Note: NOT NULL constraint added in next migration after backfill

def downgrade() -> None:
    op.drop_column("books", "visibility")  # always reversible
```

**Rules:**
- Zero-downtime migrations only — use expand/contract pattern for breaking changes:
  1. Expand: add column as nullable
  2. Backfill: populate values via a Celery backfill task (never inside the migration)
  3. Constrain: add NOT NULL after backfill confirms zero nulls (separate migration)
  4. Contract: remove old column (separate migration, after one release cycle)
- Never rename a column directly — add new, backfill, drop old
- Never drop a column without a deprecation period (at minimum one release cycle)
- Every migration is idempotent (`IF NOT EXISTS`, `IF EXISTS`)
- Test both `upgrade` and `downgrade` in CI
- Seed data never in migration files — dev seeds live in `scripts/`, not Alembic

### 4.4 Query Performance Rules

- Every foreign key column must have an index
- Every column used in `WHERE` or `ORDER BY` clauses must have an index
- Use `EXPLAIN ANALYZE` before merging any query that touches > 10k rows
- Pagination: keyset cursor only — never `OFFSET` (performance degrades at scale)
- N+1 queries are a bug — always use `joinedload()` or explicit joins

```python
# WRONG — N+1: fires one query per book
books = await session.execute(select(BookORM))
for book in books:
    chunks = await session.execute(select(ChunkORM).where(ChunkORM.book_id == book.book_id))

# CORRECT — single join
stmt = (
    select(BookORM)
    .options(selectinload(BookORM.chunks))
    .where(BookORM.deleted_at.is_(None))
)
```

### 4.5 Transaction Boundaries

- One transaction per HTTP request (via `get_db_session` dependency)
- Celery tasks: one transaction per stage, committed after each stage checkpoint
- Never hold a transaction open across an LLM API call (network latency → lock contention)

```python
# WRONG — transaction held open during LLM call
async with session.begin():
    chunks = await repo.get_chunks(book_id)
    result = await openai.chat(chunks)  # may take 30 seconds
    await repo.save_units(result)       # lock held for 30+ seconds

# CORRECT — fetch, close transaction, call LLM, new transaction to save
chunks = await repo.get_chunks(book_id)          # tx 1 committed
result = await openai.chat(chunks)               # no transaction open
await repo.save_units(result)                    # tx 2 committed
```

### 4.6 Database Connection Pool Standards

All async SQLAlchemy engines must be configured explicitly — never rely on defaults in production.

```python
# infrastructure/db/session.py
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,        # steady-state connections per process
    max_overflow=5,      # burst allowance (total max = pool_size + max_overflow)
    pool_timeout=5,      # seconds to wait for connection before raising PoolTimeout
    pool_recycle=1800,   # recycle connections after 30 min (prevents stale connections)
    pool_pre_ping=True,  # verify connection alive before use; silently reconnects
)
```

**Rules:**
- `pool_size` × worker processes must not exceed PostgreSQL `max_connections` (default 100)
- Sizing formula: `pool_size ≤ (pg_max_connections − 5 reserved) / num_api_processes`
- Celery workers: each worker process has its own pool — default `pool_size=5` per worker
- Readiness probe (`/health/ready`) must fail if a test connection cannot be acquired within 2 seconds
- `pool_pre_ping=True` always — silently reconnects on stale connections without surfacing errors

### 4.7 Neo4j / Cypher Standards

Neo4j is used for the knowledge graph. All Cypher queries must follow these rules.

```cypher
// WRONG — CREATE allows duplicate nodes under concurrency
CREATE (c:Concept {canonical_key: $key, label_en: $label})

// CORRECT — MERGE is atomic; safe for concurrent workers
MERGE (c:Concept {canonical_key: $key})
ON CREATE SET c.label_en = $label, c.created_at = datetime()
ON MATCH SET c.updated_at = datetime()
```

**Rules:**
- `MERGE` always over `CREATE` for Concept, Relation, and Book nodes
- Never build Cypher strings with f-strings — always use parameterized queries:
  `session.run("MATCH (c:Concept {canonical_key: $key}) RETURN c", key=canonical_key)`
- `WITH` chaining: split queries with more than 3 hops into multiple round-trips
- APOC allowed list in application code: `apoc.merge.node`, `apoc.create.relationship`,
  `apoc.text.levenshteinDistance` — never `apoc.export.*`, `apoc.load.*`, or `apoc.trigger.*`
- Graph schema evolution: adding a new node label requires a new uniqueness constraint
  + index in a migration PR reviewed by the data lead (same gate as Alembic migrations)
- All graph queries must complete within 5 seconds — configure `transaction_timeout` in the driver

### 4.8 Data Seeding Standards

```python
# scripts/dev_seed.py — idempotent pattern
async def seed_admin_user() -> None:
    existing = await user_repo.get_by_email(settings.ADMIN_EMAIL)
    if existing:
        logger.info("admin_user_exists", email_domain=settings.ADMIN_EMAIL.split("@")[1])
        return  # idempotent — do not create duplicate
    await user_repo.create(...)
```

**Rules:**
- `dev_seed.py` is idempotent — running twice must not create duplicates or raise errors
- Seed data lives in `scripts/` only — never in Alembic migration files
- Test fixtures (`tests/fixtures/`) must not contain real copyrighted book content — use synthetic data
- When seed data schema changes, update `scripts/dev_seed.py` and document the change in the PR
- `make seed` is safe to run at any time on a running database

### 4.9 Data Migration (Backfill) Standards

Schema migrations (Alembic) add or remove columns. Data migrations (backfills) populate them.
These are always separate operations.

**Rules:**
- Backfill jobs run as Celery tasks in `tasks/backfill.py`, not inside Alembic `upgrade()`
- Every backfill must be resumable: checkpoint progress every 1,000 rows using a `last_processed_id` cursor
- Backfills use per-batch time limits to prevent table locks:
  each batch runs in its own short transaction, not one massive transaction
- NOT NULL constraint migration runs only after:
  `SELECT COUNT(*) FROM table WHERE col IS NULL` returns 0 — verified in CI
- Large backfills (> 1M rows): schedule during off-peak hours; notify the team before starting

```python
# tasks/backfill.py — resumable pattern
@app.task(name="tasks.backfill.populate_canonical_keys", bind=True)
def populate_canonical_keys(self, last_id: str | None = None) -> None:
    batch = repo.get_units_without_canonical_key(after_id=last_id, limit=1000)
    if not batch:
        logger.info("backfill_complete", task="populate_canonical_keys")
        return
    for unit in batch:
        unit.canonical_key = to_canonical_key(unit.subject)
    repo.bulk_update(batch)
    # recurse via Celery — resumable if worker crashes between batches
    self.apply_async(kwargs={"last_id": str(batch[-1].unit_id)})
```

---

## 5. Worker & Pipeline Standards

### 5.1 Celery Task Rules

```python
# CORRECT task signature
@app.task(
    bind=True,
    name="app.tasks.ingest.run_ingestion",  # explicit name, never rely on auto-name
    max_retries=3,
    default_retry_delay=60,
    queue="default",
    acks_late=True,  # re-queue on worker crash
)
def run_ingestion(self, job_id: str, book_id: str, config: dict) -> dict:
    ...
```

**Rules:**
- All task parameters: JSON-serializable primitives only (str, int, dict, list)
  — never pass Python objects, ORM models, or UUIDs directly
- All task names: explicitly set, never rely on auto-generated names (rename-safe)
- Every task: idempotent by design — safe to run twice without side effects
- All tasks: `acks_late=True` — Celery only acks after the task succeeds
- Task timeouts: always set `soft_time_limit` and `time_limit`
- Never call `async` functions from sync Celery tasks — use `asyncio.run()` wrapper

### 5.2 Pipeline Stage Rules

```python
class IngestionPipeline:
    async def _stage_chunk(self, pages: list[PageText]) -> list[Chunk]:
        # Rule: update progress BEFORE starting, after completing
        await self._update_stage("chunk", progress=0.0)

        chunks = []
        for i, page in enumerate(pages):
            chunk_batch = self._chunk_page(page)
            await self._save_chunk_batch(chunk_batch)  # checkpoint per batch
            await self._update_stage("chunk", progress=(i + 1) / len(pages))
            chunks.extend(chunk_batch)

        await self._update_stage("chunk", progress=1.0)
        return chunks
```

**Rules:**
- Every stage checkpoints progress to Postgres after each batch (not just at end)
- Every stage publishes progress to Redis pub/sub for SSE streaming
- Every stage is independently resumable from its checkpoint
- Stages never share in-memory state — pass only IDs between stages
- Failed stages: write structured error to `ingestion_jobs.error_json`, not just a string

### 5.3 Retry Strategy

| Error Type | Strategy |
|-----------|----------|
| Network (ConnectionError, TimeoutError) | Exponential backoff, 3 retries |
| Rate limit (429 from LLM API) | Respect `Retry-After` header, up to 5 retries |
| Permanent parse error | Mark failed immediately, no retry |
| S3 access denied | Alert + fail immediately (config issue, not transient) |
| OCR engine error | Log, fallback to Azure Vision, then fail |

### 5.4 Webhook Delivery Standards

Webhook delivery is handled by `tasks/webhooks.py`. Delivery must be reliable and observable.

**Delivery rules:**
- Request timeout per attempt: 10 seconds — never wait longer
- Retry schedule: 1 min → 5 min → 30 min → 2 hr → 24 hr (5 total attempts)
- After 5 consecutive failures: mark endpoint `status=disabled` in `webhooks` table; halt delivery
- Re-enable: manual action via `POST /v1/webhooks/{id}/enable` (editor or above)
- Signature header format: `X-Creator-Signature: sha256={hex_digest}` — HMAC-SHA256 over raw request body
- Dead letter: on final failure, write response body/status to `webhooks.last_failure_body` and `webhooks.last_failure_at`
- All delivery attempts logged to `audit_log` with `action=webhook_delivered` or `action=webhook_failed`

```python
# Delivery response handling
if 200 <= response.status_code < 300:
    await repo.record_success(webhook_id)
else:
    # Any non-2xx is a failure — the endpoint's problem
    await repo.record_failure(webhook_id, response.status_code, response.text[:1000])
    raise self.retry(countdown=next_retry_delay)
```

---

## 6. AI / LLM Standards

### 6.1 Prompt Management

Prompts are code. They must be versioned, tested, and reviewed like code.

```
infrastructure/llm/prompts/
  extraction/
    unit_extraction.jinja2        ← v1 prompt
    unit_extraction_v2.jinja2     ← experimental; A/B test before promoting
  generation/
    video_script.jinja2
    citation_repair.jinja2
```

**Rules:**
- All prompts in Jinja2 templates — never f-strings or concatenation in code
- Prompt version stored in DB with each LLM output (`llm_usage_logs.model_id`)
- Prompt changes require: test with 20 sample inputs, compare output quality, PR review
- System prompt: always in English (LLM performance is highest)
- User content: include `language_detected` hint so LLM adjusts extraction

### 6.2 Output Validation

Never trust LLM output. Always validate with Pydantic before saving.

```python
# WRONG — save whatever LLM returns
raw = await llm.chat(prompt)
await repo.save_units(json.loads(raw))  # could be anything

# CORRECT — validate against schema first
raw = await llm.chat(prompt)
try:
    parsed = KnowledgeUnitExtractionOutput.model_validate_json(raw)
except ValidationError as e:
    logger.warning("llm_output_invalid", error=str(e), raw_preview=raw[:200])
    # retry with simplified prompt or mark chunk as extraction_failed
    raise LLMOutputValidationError(str(e))
await repo.save_units(parsed.units)
```

**Rules:**
- Every LLM output validated through a Pydantic model before any further use
- Schema validation failure → retry with simplified prompt (max 1 retry), then fail gracefully
- Knowledge unit extraction: validate subject/predicate/object, snippet length ≤ 600 chars, evidence not empty
- Video generation: validate every scene has `evidence_refs`, storyboard schema complete
- Log all validation failures with raw output preview for debugging

### 6.3 Token & Cost Management

```python
# Always log token usage
result = await openai.chat.completions.create(...)
await llm_usage_repo.log(
    operation_type="extraction",
    model_id=result.model,
    input_tokens=result.usage.prompt_tokens,
    output_tokens=result.usage.completion_tokens,
    estimated_cost_usd=calculate_cost(result),
    job_id=job_id,
)
```

**Rules:**
- Every LLM call logs to `llm_usage_logs` — no exceptions
- Batch embeddings: max 100 texts per API call (BGE-M3 or OpenAI)
- Input text to LLM: always truncated to model's safe context window (leave 20% for output)
- Never call LLM in a loop without rate limiting — use `asyncio.Semaphore(max_concurrent=5)`

### 6.4 Prompt Injection Protection

```python
# All user-supplied text must go through sanitize_for_llm() before inclusion in prompts
topic = sanitize_for_llm(request.topic, max_length=500, field_name="topic")
query = sanitize_for_llm(request.query, max_length=2000, field_name="query")
```

**Rules:**
- `sanitize_for_llm()` applied to every user-supplied field before LLM inclusion
- Structured message format (system + user separation) — never concatenate user input into system prompt
- Log injection detection attempts to `audit_log` with `action=prompt_injection_detected`

### 6.5 LLM Fallback Design

Every LLM call must have a defined failure path:

| Operation | Primary | Fallback | Final Failure |
|-----------|---------|----------|--------------|
| Knowledge unit extraction | gpt-4o | gpt-4o-mini (simplified prompt) | Mark chunk as `extraction_failed` |
| Video generation | gpt-4o | Retry once with shorter context | Return partial package with warnings |
| Citation repair | gpt-4o-mini | Skip repair, apply `citation_repair_mode` | Log to warnings |
| Embeddings | BGE-M3 | text-embedding-3-large | Fail ingestion stage |

### 6.6 Embedding Model Upgrade Standards

The embedding model is locked to BGE-M3 (1024-dim, Cosine). Any future model change is a
breaking operation requiring a full re-index — never an in-place update.

**Migration procedure (zero downtime):**
1. Create new collection: `chunks_multilingual_v2` with new model parameters
2. Run background re-embedding Celery task (`tasks/reembed.py`) — 100 chunks per batch, low priority
3. Validate: new collection must pass golden query eval with recall@10 ≥ current baseline − 2%
4. Swap Qdrant collection alias: `chunks_multilingual` → `chunks_multilingual_v2`
5. Delete old collection after a 48-hour observation window

**Rules:**
- `chunks.embedding_model_id` records the model for each chunk — never assume uniformity
- Search code always uses the collection alias `chunks_multilingual`, never a versioned name directly
- Re-embedding runs on a dedicated `reembed` Celery queue with concurrency = 1 (low priority)
- No user-facing downtime: search continues against the aliased collection throughout the migration

### 6.7 Qdrant Operational Standards

```python
# WRONG — search with high limit for export/analytics purposes
results = client.search("chunks_multilingual", query_vector=vec, limit=10000)

# CORRECT — use scroll for bulk operations
points, next_offset = client.scroll(
    "chunks_multilingual",
    scroll_filter=models.Filter(...),
    limit=100,
    offset=last_offset,
)
```

**Rules:**
- Use `scroll()` for bulk export, re-embedding, and analytics — never `search()` with `limit > 200`
- Payload index rule: every field used in `must`/`should` filter conditions must have a payload index;
  adding a new filterable field requires updating `init_qdrant.py` in the same PR
- HNSW parameter changes (`m`, `ef_construct`, `ef`) require a full re-index — treat as breaking
- Take a collection snapshot before any bulk write operation: `client.create_snapshot("chunks_multilingual")`
- No point deletions in application code — use soft-delete via a `deleted: true` payload flag + filter

### 6.8 Multilingual NLP Quality Standards

This system processes Marathi, Hindi, and English. Quality gates must be explicit and logged.

**OCR quality gates:**

| Condition | Action |
|-----------|--------|
| `ocr_confidence >= 0.70` | Process normally |
| `0.40 ≤ ocr_confidence < 0.70` | Ingest but set `book_pages.ocr_review_required = true` |
| `ocr_confidence < 0.40` | Skip page, log `ocr_page_skipped` warning, write skip record |

Never silently discard a page — always write a record with the skip reason.

**Language detection gates:**
- `language_confidence < 0.80` → store `language_detected = 'und'` (undetermined) — do not guess
- Mixed-script pages (> 30% secondary script content): tag chunk as `is_bilingual = true`; do not force one language
- Chunks with `language_detected = 'und'`: excluded from language-filtered search by default; user can opt-in

**Transliteration consistency:**
- `to_canonical_key()` must produce the same output for equivalent Devanagari and Latin spellings
- Canonical key collisions → same Neo4j `Concept` node (enforced by unique constraint on `canonical_key`)
- All alias variants stored in `concept.aliases` — never create duplicate concept nodes for spelling variants

**Code review gate for NLP changes:**
Any change to `lang_detect.py` or `transliteration.py` requires before/after comparison on:
1. The 20 golden queries in `tests/golden_queries/golden_queries.jsonl`
2. The language detection sample set in `tests/fixtures/lang_detect_samples.jsonl`

---

## 7. Testing Standards

### 7.1 Test Pyramid

```
         /\
        /E2E\       ← 5%  (golden query eval, smoke tests)
       /------\
      / Integ  \    ← 25% (ingest fixture → search → verify)
     /----------\
    /    Unit    \  ← 70% (pure logic: lang detect, citation policy, transliteration)
   /--------------\
```

### 7.2 Unit Test Rules

```python
# CORRECT unit test — no DB, no network, deterministic
class TestCitationEnforcementPolicy:
    @pytest.mark.asyncio
    async def test_paragraph_without_evidence_gets_labeled(self):
        policy = CitationEnforcementPolicy(
            retrieved_evidence_ids={"chunk:abc"},
            repair_mode=CitationRepairMode.LABEL_INTERPRETATION,
            llm_repair_fn=None,  # no LLM in unit test
        )
        result = await policy.enforce([
            Paragraph("p1", "Unsupported claim.", evidence_ids=[])
        ])
        assert result.paragraphs[0].text.startswith("[Interpretation]")
        assert result.labeled_count == 1
        assert result.citation_coverage == 0.0
```

**Rules:**
- Unit tests: zero network calls, zero DB calls, zero file I/O
- Mock at the boundary (repository, LLM client) — never mock domain logic
- Test names: `test_{what}_{given condition}_{expected outcome}`
- Every `if` branch in business logic must have a corresponding test
- Parametrize tests for enum variations rather than repeating test bodies

### 7.3 Integration Test Rules

```python
# CORRECT integration test — uses test DB, real Qdrant (or testcontainers)
@pytest.mark.integration
async def test_ingest_pdf_creates_searchable_chunks(
    test_client: AsyncClient,
    auth_headers: dict,
    fixture_pdf: Path,
):
    # Upload
    resp = await test_client.post("/v1/books", json={...}, headers=auth_headers)
    book_id = resp.json()["book_id"]

    # Trigger ingestion
    resp = await test_client.post(f"/v1/books/{book_id}/ingest", headers=auth_headers)
    job_id = resp.json()["job_id"]

    # Wait for completion (poll with timeout)
    await wait_for_job(test_client, job_id, timeout=60)

    # Verify searchable
    resp = await test_client.post("/v1/search", json={"query": "soil"}, headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["results"]) > 0
```

**Rules:**
- Integration tests use real PostgreSQL and Qdrant via testcontainers (or docker-compose test profile)
- Never share DB state between tests — each test gets a fresh transaction (rolled back after)
- Integration tests tagged `@pytest.mark.integration` — runnable separately from unit tests
- Max integration test runtime: 60 seconds per test

### 7.4 Contract Tests

```python
# CORRECT — validate every API response against its JSON schema
import schemathesis

schema = schemathesis.from_uri("http://localhost:8000/openapi.json")

@schema.parametrize()
def test_api_contract(case):
    response = case.call()
    case.validate_response(response)
```

**Rules:**
- Every endpoint must have a contract test that validates against its JSON schema
- Contract tests run in CI on every PR
- New endpoint = new JSON schema in `libs/shared/contracts/` + contract test

### 7.5 Coverage Requirements

| Layer | Min Coverage | What to Cover |
|-------|-------------|--------------|
| Domain policies | 95% | All branches, all modes |
| Utils | 90% | All functions, edge cases |
| Repositories | 80% | Happy path + key error cases |
| Routers | 70% | Happy path + auth failures |
| Migrations | 100% | upgrade + downgrade |

CI will fail if thresholds drop.

---

## 8. Security Standards

### 8.1 Authentication Checklist

- [ ] All endpoints (except `/health*`) require valid JWT
- [ ] Refresh token JTI stored in Redis — revocation works immediately
- [ ] Failed login attempts: 5 failures → account lock for 30 minutes
- [ ] Passwords: bcrypt with cost factor 12 minimum
- [ ] JWT secrets: RS256 private key in vault/SSM — never in `.env` in production

### 8.2 Authorization Checklist

- [ ] Role checked at router level via `require_role()` dependency
- [ ] Resource ownership verified in usecase before any mutation
- [ ] Bulk operations check ownership for every item in the batch
- [ ] Admin-only endpoints: explicitly marked with `AdminDep`

### 8.3 Input Validation Rules

```python
# ALL of these applied to every user-supplied string:
# 1. Pydantic Field(max_length=...) — prevents oversized payloads
# 2. sanitize_for_llm() — before any LLM inclusion
# 3. HTML/script escaping — before any rendering (UI handles this)
# 4. SQL parameterization — SQLAlchemy handles this automatically
```

**Rules:**
- Never use f-strings to build SQL queries — SQLAlchemy parameters only
- All file uploads: verify MIME type server-side (not just file extension)
- All presigned URLs: include `Content-Type` and `Content-Length` constraints
- Sensitive response fields (password_hash, api_key_hash, secret_token_hash): never in any response schema
- Audit log: append-only — revoke DELETE privilege from application DB user

### 8.4 Secrets Rules

```
NEVER commit to git:
  - .env files (only .env.example with no real values)
  - Private keys (certs/ directory is in .gitignore)
  - API keys (OPENAI_API_KEY, etc.)
  - Database passwords

ALWAYS use:
  - Vault/SSM for production secrets
  - Docker secrets or environment injection for staging
  - .env file for local development only
```

### 8.5 Secret Rotation Standards

Secrets have a finite lifespan. Rotation must be possible without downtime.

**Rotation schedule:**

| Secret | Max Age | Rotation Method |
|--------|---------|----------------|
| JWT RS256 private key | 90 days | Dual-key rotation (see below) |
| Database password | 90 days | Connection pool reload |
| User-created API keys | User-controlled | Immediate revocation via `DELETE /v1/api-keys/{id}` |
| OpenAI / Azure API keys | 180 days | Environment variable swap + rolling restart |
| HMAC webhook signing key | 180 days | Grace period: sign with old + new simultaneously |

**JWT RS256 key rotation (zero-downtime procedure):**
1. Generate new RS256 key pair
2. Publish new public key to JWKS endpoint (`GET /v1/.well-known/jwks.json`) — keep old public key alongside
3. Start signing new tokens with the new private key
4. Wait for all existing access tokens to expire (max = access token TTL = 15 minutes)
5. Remove old public key from JWKS endpoint
6. Revoke all refresh tokens via Redis JTI flush (force re-login for all users)

**Rules:**
- Rotation runbook documented in `docs/runbooks/secret_rotation.md`
- After rotation: run smoke test on `GET /v1/auth/me` to verify new tokens validate correctly

### 8.6 Data Privacy & PII Standards

This system stores user data. Privacy is a first-class engineering concern.

**PII definition for this domain:**
- PII: user email address, full name, IP addresses recorded in `audit_log`
- Not PII: book content, knowledge units, video packages (user's own created content)

**Rules:**
- PII must never appear in log output — use `user_id` (UUID) in logs, never `user.email`
- `audit_log` rows: IP addresses anonymized after 2 years (replace with `[ANONYMIZED]`)
- Right-to-deletion (`DELETE /v1/users/{id}`) implemented as `DeleteUserUsecase`:
  - Hard-delete the `users` row
  - Replace PII in `audit_log` with `[DELETED:{user_id}]` (preserve the event, remove identity)
  - Anonymize `books.created_by` → transfer to org service account or set null
- Data export (`GET /v1/users/{id}/export`): return all user-created content as JSON archive (GDPR Article 20)
- `search_logs` retention: 90 days maximum, then anonymize `user_id` → null
- Qdrant vector payloads: never store raw PII — use UUIDs only in payload fields

---

## 9. Observability Standards

### 9.1 Structured Logging Rules

Every log entry must include context. Use `structlog.contextvars.bind_contextvars()` to
set context once at request entry, then all downstream logs carry it automatically.

```python
# At request start (middleware)
structlog.contextvars.bind_contextvars(
    trace_id=extract_trace_id(request),  # from traceparent header
    user_id=str(current_user.user_id),   # UUID only — never email
    path=request.url.path,
)

# In business logic — just log the event, context is automatic
logger.info("chunk_embedded", chunk_id=str(chunk_id), model="bge-m3", latency_ms=42)
logger.error("ocr_failed", page=12, confidence=0.31, engine="tesseract")
```

**Required context fields for every log:**
- `service`: `api` or `worker`
- `trace_id`: propagated across services (extracted from W3C `traceparent` header)
- `user_id`: UUID only for authenticated requests — never email address
- `job_id`: for worker tasks
- `book_id`: when processing a book
- `event`: what happened (snake_case verb noun)
- `duration_ms`: for any operation with latency

**Log levels:**
| Level | When to use |
|-------|------------|
| DEBUG | Detailed internal state (disabled in prod) |
| INFO | Normal operations (request received, stage complete, chunk saved) |
| WARNING | Recoverable issues (OCR confidence low, LLM retry, circuit breaker) |
| ERROR | Operation failed (stage failed, LLM unreachable, DB write failed) |
| CRITICAL | System-level failure (DB unreachable, cannot start) |

### 9.2 Metrics to Capture

Every metric must have a `book_id` or `job_id` label where applicable.

| Metric | Type | Labels |
|--------|------|--------|
| `ingestion_stage_duration_seconds` | Histogram | `stage`, `book_id` |
| `ocr_confidence` | Histogram | `engine`, `language`, `book_id` |
| `embedding_batch_size` | Histogram | `model` |
| `llm_tokens_used` | Counter | `operation`, `model`, `user_id` |
| `citation_coverage_ratio` | Gauge | `video_id` |
| `search_latency_seconds` | Histogram | `has_graph` |
| `knowledge_unit_status` | Gauge | `status`, `book_id` |
| `db_pool_checked_out` | Gauge | `service` |
| `celery_task_duration_seconds` | Histogram | `task_name`, `queue`, `status` |
| `webhook_delivery_attempts_total` | Counter | `status` (`success`\|`failure`\|`disabled`) |

### 9.3 Tracing Rules (W3C Trace Context)

This project adopts the [W3C Trace Context](https://www.w3.org/TR/trace-context/) standard.
Use the `traceparent` header — not a custom `X-Trace-Id` header.

```python
# main.py — configure W3C propagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap

set_global_textmap(CompositePropagator([TraceContextTextMapPropagator()]))
```

**Rules:**
- HTTP → Celery task: inject `traceparent` into Celery task headers via OpenTelemetry's Celery instrumentation
- Celery task → Neo4j/Qdrant: OpenTelemetry auto-instrumentation propagates automatically
- Every external API call (OpenAI, Azure Vision): create a child span
- Span names: `{service}.{operation}` → `worker.ocr_page`, `api.hybrid_search`
- Extract `trace_id` from `traceparent` and bind to structlog context at request start
- Traces viewable in Jaeger or Grafana Tempo via the OTLP collector in `docker-compose.yml`

### 9.4 SLO / SLA Definitions

Service Level Objectives define the acceptable performance envelope. Grafana dashboards
are required for every SLO before the service goes to production.

| SLO | Target | Measurement Window |
|-----|--------|--------------------|
| API uptime | 99.5% monthly | 30-day rolling |
| Search latency p95 | < 500ms | 1-hour rolling |
| Search latency p99 | < 2s | 1-hour rolling |
| Ingestion completion p95 (< 500 pages) | < 10 minutes | Per-job |
| Video generation p95 | < 5 minutes | Per-job |
| API error rate (5xx) | < 0.5% | 1-hour rolling |
| Webhook delivery success rate | > 95% | 24-hour rolling |

**Error budget:** 0.5% of monthly API requests may fail (= ~3.6 hours downtime equivalent).
When error budget is consumed: feature freeze until next calendar month.

### 9.5 Incident Response Standards

**Alert thresholds:**

| Alert | Severity | Threshold | Action |
|-------|----------|-----------|--------|
| API 5xx error rate | P1 | > 5% over 5 min | Page on-call immediately |
| API 5xx error rate | P2 | > 1% over 15 min | Notify on-call |
| DB connection pool exhausted | P1 | pool_checked_out = max | Page on-call immediately |
| Ingestion error rate | P2 | > 5% over 30 min | Notify on-call |
| Celery queue depth | P2 | > 500 tasks in any queue | Notify on-call |
| LLM API error rate | P2 | > 10% over 10 min | Notify on-call |

**Response process:**
1. Acknowledge alert within 15 minutes (P1) or 1 hour (P2)
2. Diagnose using runbook in `docs/runbooks/{service}.md`
3. Mitigate: rollback, scale, or disable the failing feature flag
4. Resolve: root cause fix deployed and verified
5. Post-mortem: required within 48 hours for all P1 incidents; 5-Why format; filed in `docs/post-mortems/`

**Runbook standard:** Every service has a runbook at `docs/runbooks/{service}.md` covering:
- How to check service health and read key metrics
- Common failure modes and their fixes
- How to restart or rollback the service
- Escalation contacts

---

## 10. Git & Review Standards

### 10.1 Branch Strategy

```
main          ← production-ready, protected. Direct push NEVER allowed.
staging       ← auto-deployed to staging on merge from main
feature/*     ← feature branches, branch from main
fix/*         ← bug fix branches
migration/*   ← DB migration branches (reviewed separately)
```

### 10.2 Commit Message Format

Follow Conventional Commits (enforced by commitlint in CI):

```
<type>(<scope>): <short description>

<body — optional, explain WHY not WHAT>

<footer — issue refs, breaking change notes>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `security`

```
# CORRECT
feat(ingestion): add EPUB parsing via ebooklib spine extraction

# CORRECT
fix(citation): repair mode label_interpretation now preserves evidence_refs

# CORRECT with body
perf(search): switch to keyset pagination for chunk listing

Offset pagination was causing full table scans on the chunks table
at > 100k rows. Keyset cursor reduced p95 from 800ms to 45ms.

Closes #142

# WRONG
fixed stuff
Update code
WIP
```

### 10.3 Pull Request Requirements

Every PR must have:
- [ ] Description explaining WHY (not what — that's in the diff)
- [ ] Link to the user story it implements (e.g., `US-INGEST-04`)
- [ ] Self-review completed (author reviews their own diff before requesting review)
- [ ] All CI checks passing (lint, type-check, unit tests, contract tests)
- [ ] No new TODO comments without a linked issue number: `# TODO(#123): wire DB`
- [ ] Migration PRs: include rollback plan and estimated migration duration

### 10.4 Code Review Rules

**Reviewer must check:**
- [ ] Layer violations (router calling repo, service importing fastapi, etc.)
- [ ] Missing tests for new branches
- [ ] Any new TODO without issue number
- [ ] Hardcoded values instead of settings
- [ ] Missing error handling for external calls
- [ ] Logging context fields present
- [ ] No secrets in code

**Maximum PR size:** 400 lines changed (excluding migrations and generated files).
Large PRs must be split — reviewers may reject oversized PRs without review.

### 10.5 Architecture Decision Records (ADR)

Any decision that changes a principle in STANDARDS.md, selects a new dependency, or makes
a significant architectural trade-off must be recorded as an ADR.

**Location:** `docs/adr/ADR-{NNN}-{slug}.md`

**Required when:**
- Selecting a new dependency (database, queue, external API)
- Changing a principle in this STANDARDS.md document
- Choosing between two approaches where the trade-off is non-obvious
- Deciding to deviate from these standards with documented justification

**Template:**
```markdown
# ADR-001: Use BGE-M3 as the primary embedding model

## Status
Accepted

## Context
We need a multilingual embedding model that supports Marathi, Hindi, and English
in a single vector space without language-specific fine-tuning.

## Decision
Use BGE-M3 (1024-dim, Cosine) self-hosted via sentence-transformers.

## Consequences
+ Single collection for all languages; cross-lingual search works out of the box
+ Self-hosted: no per-call cost after infrastructure cost
- 1024-dim vectors use more Qdrant storage than 384-dim models
- Requires GPU instance in production for acceptable throughput
```

---

## 11. Frontend Standards (React + TypeScript)

### 11.1 Component Rules

```
apps/web/src/
  components/
    ui/           ← shadcn primitives (never modify, regenerate from shadcn)
    shared/       ← reusable across pages (EvidencePanel, ParagraphList)
    [feature]/    ← feature-specific (BookCard, KnowledgeUnitRow)
  pages/          ← one component per route
  hooks/          ← custom hooks (useSearchResults, useJobProgress)
  stores/         ← Zustand stores (only global/cross-page state)
  api/            ← TanStack Query hooks + API client functions
```

**Rules:**
- No business logic in components — move to custom hooks
- No API calls directly in components — all go through TanStack Query hooks in `api/`
- Global state (Zustand): only for truly global state (auth, selected book, sidebar)
- Component state (`useState`): for UI-only state (modal open, hover, animation)
- Props: always typed with TypeScript interfaces — never `any`

### 11.2 API Client Rules

```typescript
// CORRECT — typed API client with error handling
export async function searchKnowledgeBase(
  request: HybridSearchRequest,
  signal?: AbortSignal
): Promise<HybridSearchResponse> {
  const response = await apiClient.post("/v1/search", request, { signal });
  return HybridSearchResponse.parse(response.data);  // Zod validation
}
```

**Rules:**
- All API responses validated with Zod schemas (mirroring the JSON schemas in `libs/shared/contracts/`)
- Every query has loading, error, and empty states handled in UI
- Pagination: use TanStack Query's `useInfiniteQuery` for all cursor-paginated lists
- SSE job events: `EventSource` with reconnect logic in a custom hook `useJobEvents`

### 11.3 Evidence Panel Rules

The evidence panel (`click paragraph → see source`) is the most critical UI feature.
It must always:
- Show source book title, chapter, page range
- Show exact snippet (highlighted if possible)
- Work without page reload (state in URL hash `#para-{id}`)
- Be accessible (keyboard navigable, ARIA labels on evidence items)
- Load evidence lazily (only fetch full citation on click, not on render)

### 11.4 TypeScript Strictness Standards

```jsonc
// tsconfig.json — mandatory settings
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "exactOptionalPropertyTypes": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitReturns": true
  }
}
```

**Rules:**
- No `as` type assertions except at external API boundaries — must include an inline comment explaining why
- No `@ts-ignore` — use `@ts-expect-error` with an explanation comment; fails automatically when the error is fixed
- Categorical values: use `const enum` for compile-time inlining, not string literals
- All optional API response fields: typed as `T | null`, not `T | undefined` — matches OpenAPI nullable semantics
- No implicit `any` from third-party types — add explicit type declarations or `@types/` packages

### 11.5 Frontend Performance Standards

**Bundle size budgets:**

| Bundle | Max size (gzipped) | Enforcement |
|--------|-------------------|-------------|
| Initial chunk (main) | 200 KB | CI fails if exceeded |
| Per-route chunk | 80 KB | CI warning |
| Total assets on first load | 500 KB | CI warning |

**Rules:**
- Every route is lazy-loaded with `React.lazy()` + `Suspense` — no exceptions
- Long lists (> 50 items): use `@tanstack/virtual` for virtualization — never render all DOM nodes
- `useMemo` and `useCallback`: only when a profiler measurement shows render cost > 16ms — not preemptively
- Images: always use `loading="lazy"` + explicit `width`/`height` to prevent layout shift (CLS)
- Lighthouse CI check runs on every PR touching `apps/web/` — Performance score must not drop below 80

### 11.6 Accessibility Standards

**Commitment: WCAG 2.1 Level AA for all user-facing pages.**

**Rules:**
- All interactive elements (buttons, links, inputs): keyboard reachable with visible focus indicator
- Icon-only buttons: must have `aria-label` — a button with only an icon has no accessible name
- All images: must have `alt` attribute — decorative images use `alt=""`
- Color contrast: minimum 4.5:1 for normal text, 3:1 for large text and UI components
- Form fields: every input has an associated `<label>` — placeholder text is not a label substitute
- Error messages: announced to screen readers via `role="alert"` or `aria-live="polite"`
- `axe-core` automated scan runs in CI on every PR — zero AA violations required to merge

---

## 12. Tech Debt Prevention Rules

These are process rules — enforced in retrospectives and PR reviews.

### Rule 1: Every TODO needs an issue number

```python
# WRONG — untracked todo
# TODO: wire DB

# CORRECT — tracked todo
# TODO(#23): wire DB session to book repository
```

Before merging any PR: all new `# TODO` comments must reference an open GitHub issue.
No issue → no merge.

### Rule 2: The Boy Scout Rule

Leave code cleaner than you found it. Every PR may include up to 20 lines of cleanup
unrelated to the PR's main purpose (fix a typo, improve a docstring, add a missing type).
More than 20 lines of cleanup → separate PR.

### Rule 3: No Broken Windows

If you encounter a test that is disabled, a `type: ignore` without explanation, or a `TODO`
that has been there for more than 2 sprints — fix it or file an issue immediately.
Do not add code around broken windows.

### Rule 4: Deprecation Before Deletion

Never delete a public API endpoint or database column without:
1. Marking it deprecated in the OpenAPI spec and docs
2. One release cycle (minimum 2 weeks) of the deprecation being live
3. Confirming no clients are calling the endpoint (check logs)

### Rule 5: No Copy-Paste Code

If you write the same logic in two places, it must be extracted by the time the PR is
merged. Three occurrences is not a coincidence — it is a missing abstraction.
Exception: test code may have controlled repetition for clarity.

### Rule 6: Infrastructure Changes Need a Runbook

Any PR that changes docker-compose, migrations, environment variables, or deployment
configuration must include a runbook section in the PR description:

```
## Runbook
1. Run migration: `make migrate`
2. Restart workers: `docker-compose restart worker`
3. Verify: `curl localhost:8000/health/ready` → all services OK
4. Rollback: `make migrate-down && docker-compose restart worker`
```

### Rule 7: Measure Before You Optimize

No performance optimization without a benchmark showing the before/after. Include the
`EXPLAIN ANALYZE` output, benchmark script, or profiler report in the PR description.

### Rule 8: Dependency Hygiene

- Review `pip audit` output before every release (no known CVEs)
- Pin major versions in `pyproject.toml` — never use `>=` without an upper bound
- No new dependency added without: (a) explanation of why existing deps can't solve it,
  (b) license compatibility check, (c) maintenance status check

### Rule 9: Feature Flags for Partial Deployments

Any feature that touches > 2 services, introduces a new LLM prompt, or changes ingestion
behavior must be gated behind a feature flag until validated in staging.

**Flag system:** Environment variable flags only — no runtime DB flags.
Rationale: DB flags can be toggled without a deployment (dangerous); env flags require a deliberate deploy.

**Naming convention:** `FF_{FEATURE}_{SCOPE}` (e.g., `FF_EPUB_INGESTION_ENABLED=true`)

**Lifecycle rules:**
- Every flag has a GitHub issue created at flag introduction for its future removal
- Flags are temporary: targeted removal within 2 sprints of the feature going fully live
- Dead flags (feature fully launched, flag always true in all envs): removed in the next PR

### Rule 10: Environment Parity

Dev, staging, and production must behave identically. Behavioral differences are defects,
not features.

**What may differ across environments:** infrastructure endpoints (DB host, S3 bucket), secrets.
**What must never differ:** business logic, feature flags (unless explicitly staged rollout), LLM prompts.

**Enforcement:**
- `make config-diff` compares staging and prod config keys — must show only expected infra differences
- No `if settings.ENV == "development":` conditionals in business logic — use feature flags instead

---

## 13. CI/CD & Deployment Standards

### 13.1 Pipeline Stages

Every push to a feature branch runs:

```
1. lint          — ruff check + black --check (fail on any violation)
2. typecheck     — mypy --strict on all packages
3. unit          — pytest -m "not integration" (coverage thresholds enforced)
4. contract      — schemathesis against OpenAPI spec
5. build         — docker build for api and worker images
```

Every merge to `main` additionally runs:

```
6. integration   — pytest -m integration (real Postgres + Qdrant via testcontainers)
7. eval          — scripts/eval_run.py golden queries (≤ 2% recall@10 regression gate)
8. deploy-staging — push images tagged with commit SHA, run migrations, restart services
9. smoke          — curl /health/ready + 3 representative API calls against staging
10. deploy-prod   — (manual approval gate) push images, run migrations, restart services
```

### 13.2 Deployment Rules

- Docker images tagged with full commit SHA: `{registry}/{service}:{sha}` — never use `latest` tag in staging or prod
- Migration runs before new containers start — use init-container pattern or `make migrate` in deploy script
- Worker drain before restart: send shutdown signal, wait for in-flight tasks to complete (max 5 min)
- Deployment order: database migrations → worker → api (never start api before migrations complete)
- Rollback uses the previous commit SHA — always available from the image registry

### 13.3 Rollback Standards

Every deployment must be reversible. Rollback procedure:

```
1. Identify: check Grafana SLO dashboard — confirm rollback is needed (not just a spike)
2. API rollback: deploy previous image SHA (< 2 minutes)
3. Worker rollback: drain current tasks → deploy previous worker image
4. DB rollback:
   a. Additive migration (new nullable column): no rollback needed — old app ignores new column
   b. Data migration: restore from pre-migration snapshot (coordinate with DBA)
   c. Breaking migration (dropped column): prohibited — see Section 4.3
5. Verify: run smoke test after rollback
6. Post-mortem: required within 48 hours
```

**Non-rollbackable migrations are prohibited.** If data must be removed, deploy a soft-delete
step first (at least one full release cycle), then hard-delete in a separate migration.

### 13.4 Docker Image Standards

```dockerfile
# Required structure in every Dockerfile
FROM python:3.12-slim                          # pinned minor version — never :latest

RUN apt-get update && \
    apt-get install -y --no-install-recommends ... && \
    rm -rf /var/lib/apt/lists/*               # clean apt cache in the same layer

COPY pyproject.toml poetry.lock* ./           # lock file must be committed
RUN pip install --no-cache-dir poetry && \
    poetry install --no-dev                   # no dev dependencies in production images

RUN adduser --disabled-password appuser
USER appuser                                  # never run as root

HEALTHCHECK CMD curl -f http://localhost:8000/health/live || exit 1
```

**Rules:**
- Never use `:latest` base image tags — pin the minor version
- `poetry.lock` must be committed; it is the authoritative source for installed package versions
- Production images: no dev dependencies (`--no-dev`)
- Every Dockerfile must include a `HEALTHCHECK` instruction

---

## 14. Documentation Standards

### 14.1 Docstring Requirements

**Where docstrings are required:**
- All public functions and methods in `domain/`, `services/`, `repositories/`
- All Pydantic request/response model classes (class-level docstring describing purpose and usage)
- All Celery tasks

**Where docstrings are NOT required:**
- Private functions and methods (`_prefix`) — name should be self-explanatory
- Test functions — the test name and assertions are the documentation
- Router endpoints — `response_model`, `summary`, and `description` in the decorator serve this role

**Style: Google docstring format.**

```python
# CORRECT — Google style
async def enforce(self, paragraphs: list[Paragraph]) -> EnforcementResult:
    """Enforce citation coverage on a list of paragraphs.

    Applies the configured repair mode to paragraphs that lack evidence
    references, and returns an enforcement result summarizing all actions taken.

    Args:
        paragraphs: The list of paragraphs to enforce citations on.
            Each paragraph must have an `evidence_ids` field.

    Returns:
        EnforcementResult with citation_coverage, labeled_count, and
        removed_count populated.

    Raises:
        LLMOutputValidationError: If repair mode is LLM_REPAIR and the
            LLM returns an invalid response after one retry.
    """
```

### 14.2 Inline Comment Rules

- Comments explain **why**, not **what** — code shows what; comments explain intent and non-obvious decisions
- Complex regex patterns: always followed by a plain-English explanation and a match example
- `# noqa` suppressions: must include the rule code and reason: `# noqa: E501 — long URL, cannot wrap`
- `# type: ignore`: must include the mypy error code and reason: `# type: ignore[assignment] — third-party stub missing`

### 14.3 API Documentation

- `docs/API.md` is generated from the OpenAPI spec — never hand-edited
- Command: `make docs` → runs `scripts/generate_api_docs.py` → updates `docs/API.md`
- New endpoints documented via `summary`, `description`, and `response_model` in the router decorator
- API changelog: `docs/CHANGELOG.md` updated on every release with new, changed, and deprecated endpoints

### 14.4 Architecture Decision Records

Required whenever (see also Section 10.5):
- A new dependency (database, queue, external API) is added
- A principle in this STANDARDS.md document is changed
- Two valid implementation approaches exist and the trade-off is non-obvious
- A decision is made to deviate from these standards

**Location:** `docs/adr/ADR-{NNN}-{slug}.md` — use the template in Section 10.5.

---

## 15. Definition of Done

A user story is DONE when ALL of the following are true:

### Code
- [ ] Implementation matches the acceptance criteria in the user story
- [ ] No `# TODO` comments without a linked issue
- [ ] No dead code or commented-out code
- [ ] All new functions have type annotations
- [ ] No new `type: ignore` without explanation comment

### Tests
- [ ] Unit tests cover all new branches (coverage threshold met)
- [ ] Integration test covers the happy path end-to-end
- [ ] Contract test validates the response schema
- [ ] Edge cases covered (empty input, missing optional fields, auth failure)
- [ ] Tests pass in CI (not just locally)

### Database
- [ ] Migration has both `upgrade()` and `downgrade()`
- [ ] Migration is idempotent
- [ ] New indexes added for all FK and query columns
- [ ] Migration tested on a copy of production-size data (for tables > 100k rows)
- [ ] Backfill task written if new NOT NULL column added to an existing table

### Security
- [ ] Auth check present for every new endpoint
- [ ] Input sanitization for every user-supplied field
- [ ] No new secrets in code
- [ ] Audit log entry for sensitive mutations
- [ ] PII not present in log output

### Observability
- [ ] Structured log events added for key operations
- [ ] LLM calls log to `llm_usage_logs`
- [ ] Error paths log with full context
- [ ] New SLO-relevant operations have a Grafana metric panel

### Documentation
- [ ] Docstrings added to all new public functions in domain/service/repository layers
- [ ] User story acceptance criteria updated if implementation diverged
- [ ] New environment variables added to `.env.example` with description
- [ ] New endpoints reflected in `docs/API.md` (via `make docs`)
- [ ] GAPS.md updated if a gap was resolved by this story
- [ ] ADR created if an architectural decision was made

### Review
- [ ] PR description explains WHY, not what
- [ ] Self-review completed
- [ ] CODEOWNERS approval obtained for owned paths
- [ ] At least one peer review approved
- [ ] All CI checks green

---

*Last updated: 2026-02-20*
*Owner: Engineering Team*
*Review cycle: Every sprint retrospective*
