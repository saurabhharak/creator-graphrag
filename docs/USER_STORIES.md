# User Stories
## Multilingual Book Knowledge Base → GraphRAG Video Content Generator

Each story follows: **As a [role], I want [goal] so that [benefit].**
Acceptance criteria are testable conditions.

---

## EPIC 1: Authentication & User Management

### US-AUTH-01: User Registration
**As a** new user,
**I want to** register with my email and password,
**so that** I can access the Creator Studio.

**Acceptance Criteria:**
- [ ] `POST /v1/auth/register` accepts `{email, password, display_name}`
- [ ] Password stored as bcrypt hash (min 12 chars, complexity validated)
- [ ] Email uniqueness enforced; 409 returned for duplicates
- [ ] JWT access token (15 min) + refresh token (7 days) returned on success
- [ ] Welcome email sent (or logged in dev)
- [ ] Default role assigned: `editor`

---

### US-AUTH-02: User Login
**As a** registered user,
**I want to** log in with my credentials,
**so that** I receive a token to call the API.

**Acceptance Criteria:**
- [ ] `POST /v1/auth/login` accepts `{email, password}`
- [ ] Returns `{access_token, refresh_token, token_type, expires_in}`
- [ ] Invalid credentials return 401 with generic message (no field hints)
- [ ] Failed login attempts logged to `audit_log`
- [ ] Brute-force protection: lock account after 5 consecutive failures

---

### US-AUTH-03: Token Refresh
**As an** authenticated user,
**I want to** refresh my access token before it expires,
**so that** my session stays active without re-logging in.

**Acceptance Criteria:**
- [ ] `POST /v1/auth/refresh` accepts valid refresh token
- [ ] Returns new access token + new refresh token (rotation)
- [ ] Used refresh token immediately invalidated in Redis
- [ ] Expired refresh tokens return 401

---

### US-AUTH-04: API Key Management
**As a** developer integrating with the API,
**I want to** create and manage API keys,
**so that** my automated scripts can authenticate without storing user passwords.

**Acceptance Criteria:**
- [ ] `POST /v1/api-keys` creates a key with `{label, scopes[], expires_at}`
- [ ] Key secret shown exactly once at creation; stored as hash
- [ ] `GET /v1/api-keys` lists active keys (no secrets shown)
- [ ] `DELETE /v1/api-keys/{key_id}` revokes immediately
- [ ] API key accepted via `Authorization: Bearer <key>` header

---

### US-AUTH-05: Role-Based Access Control
**As an** admin,
**I want** role-based permissions enforced on all endpoints,
**so that** viewers cannot modify data and editors cannot access admin functions.

**Acceptance Criteria:**
- [ ] Roles defined: `admin`, `editor`, `viewer`, `api_client`
- [ ] `viewer`: read-only access to search, video packages, knowledge units
- [ ] `editor`: all viewer + ingest books, review KUs, generate packages
- [ ] `admin`: all editor + manage users, manage templates, view analytics
- [ ] 403 returned (not 401) for authenticated users lacking permission
- [ ] Role enforced via `require_role()` dependency in `deps.py`

---

## EPIC 2: Book Ingestion

### US-INGEST-01: Upload a Book
**As a** content editor,
**I want to** upload a PDF or EPUB book,
**so that** I can build a knowledge base from it.

**Acceptance Criteria:**
- [ ] `POST /v1/books` accepts metadata `{title, author, year, language_primary, tags}`
- [ ] Response includes presigned S3 upload URL (15-min expiry)
- [ ] Supported formats: `application/pdf`, `application/epub+zip`
- [ ] Max file size enforced: 500 MB (configurable)
- [ ] `POST /v1/books/{book_id}/upload-complete` confirms upload; API verifies S3 object exists
- [ ] Book `upload_status` transitions: `pending → uploaded → verified`

---

### US-INGEST-02: Trigger Book Ingestion
**As a** content editor,
**I want to** start the ingestion pipeline for an uploaded book,
**so that** its content is extracted and indexed.

**Acceptance Criteria:**
- [ ] `POST /v1/books/{book_id}/ingest` accepts optional config `{force_ocr, ocr_languages, chunking, extract_knowledge_units, build_graph}`
- [ ] Returns `{job_id, status: queued}` immediately
- [ ] Validates file was uploaded and verified before queueing
- [ ] Max 2 concurrent ingestion jobs per user (429 if exceeded)
- [ ] Idempotency key support via `Idempotency-Key` header

---

### US-INGEST-03: Monitor Ingestion Progress
**As a** content editor,
**I want to** see real-time progress of book ingestion,
**so that** I know when the book is ready for use.

**Acceptance Criteria:**
- [ ] `GET /v1/jobs/{job_id}` returns `{status, stage, progress (0-1), metrics}`
- [ ] `GET /v1/jobs/{job_id}/events` streams SSE events on stage transitions
- [ ] Progress percentage uses stage-weighted formula (OCR = 35% weight)
- [ ] Metrics include: `pages_total`, `pages_ocr`, `chunks_created`, `units_extracted`, `graph_nodes`, `graph_edges`, `citation_coverage`
- [ ] Failed stages show `error.code` and `error.detail`

---

### US-INGEST-04: Handle Scanned PDFs with OCR
**As a** content editor,
**I want** scanned Devanagari books to be OCR-processed automatically,
**so that** Marathi and Hindi books are fully indexed.

**Acceptance Criteria:**
- [ ] System detects scanned pages: text < 100 chars for >30% of pages
- [ ] Tesseract OCR run with `-l hin+mar+eng` on scanned pages
- [ ] Per-page OCR confidence stored in `book_pages.ocr_confidence`
- [ ] Pages with confidence < 0.60 flagged for review
- [ ] If average confidence < 0.60, system logs warning; Azure Vision fallback available via config
- [ ] Source type per chunk: `digital` vs `ocr`

---

### US-INGEST-05: Parse EPUB Books
**As a** content editor,
**I want to** ingest EPUB format books,
**so that** I can use digital-first books without PDF conversion.

**Acceptance Criteria:**
- [ ] EPUB detected by MIME type `application/epub+zip`
- [ ] Spine items mapped to chapters; NCX/Nav used for structure
- [ ] XHTML body text extracted; HTML tags stripped
- [ ] Images referenced but not processed (noted in chunk metadata)
- [ ] Resulting chapters/chunks identical schema to PDF-ingested content
- [ ] Integration test with a fixture EPUB file

---

### US-INGEST-06: Detect and Handle Multilingual Content
**As the** ingestion pipeline,
**I want to** detect the language of each chunk (Marathi / Hindi / English),
**so that** cross-lingual retrieval works correctly.

**Acceptance Criteria:**
- [ ] Language detected at book, page, and chunk level
- [ ] fastText or CLD3 used for initial detection
- [ ] Devanagari script → Marathi vs Hindi disambiguated using stopword distribution
- [ ] Stopword lists maintained in `data/stopwords/mr.txt` and `hi.txt`
- [ ] `language_confidence FLOAT` stored per chunk
- [ ] Chunks with confidence < 0.80 classified as `mixed`
- [ ] Unit tests for language detection + disambiguation

---

### US-INGEST-07: Cancel or Retry a Failed Job
**As a** content editor,
**I want to** cancel a running job or retry a failed one,
**so that** I can recover from errors without re-uploading the book.

**Acceptance Criteria:**
- [ ] `POST /v1/jobs/{job_id}/cancel` stops the running Celery task
- [ ] `POST /v1/jobs/{job_id}/retry` re-queues from the failed stage (idempotent)
- [ ] Retry respects original config or accepts override params
- [ ] Each pipeline stage is idempotent; partial work is safely re-runnable
- [ ] Canceled jobs show `status: canceled`, failed jobs show `status: failed`

---

## EPIC 3: Book Library Management

### US-LIBRARY-01: View Book Library
**As a** content editor,
**I want to** see all my books with their status and quality metrics,
**so that** I can manage my knowledge base.

**Acceptance Criteria:**
- [ ] `GET /v1/books` returns paginated list with filters: `status`, `language`, `tags`, `search`
- [ ] Each item shows: title, author, language, ingestion status, chunk count, unit approval rate
- [ ] Cursor-based pagination with `?cursor=&limit=` (default limit: 20)
- [ ] Only books owned by authenticated user returned (or shared books with permission)

---

### US-LIBRARY-02: View Book Detail
**As a** content editor,
**I want to** see detailed information about a specific book,
**so that** I can understand its structure and quality.

**Acceptance Criteria:**
- [ ] `GET /v1/books/{book_id}` returns full metadata, files list, latest job summary
- [ ] `GET /v1/books/{book_id}/chapters` returns chapter tree with page ranges and confidence scores
- [ ] `GET /v1/books/{book_id}/chunks` returns paginated chunks with filters by type and language
- [ ] Quality metrics: OCR confidence distribution, chunk type breakdown, citation coverage

---

### US-LIBRARY-03: Update Book Metadata
**As a** content editor,
**I want to** correct book metadata after upload,
**so that** the library information is accurate.

**Acceptance Criteria:**
- [ ] `PATCH /v1/books/{book_id}` accepts any subset of `{title, author, year, edition, tags, language_primary}`
- [ ] Only book owner or admin can update
- [ ] Changes logged to `audit_log`
- [ ] `updated_at` timestamp updated

---

### US-LIBRARY-04: Delete a Book
**As a** content editor,
**I want to** delete a book from the library,
**so that** I can remove outdated or incorrect content.

**Acceptance Criteria:**
- [ ] `DELETE /v1/books/{book_id}` performs soft-delete (sets `deleted_at`)
- [ ] Cascades: S3 files queued for deletion, chunks removed from Qdrant, nodes from Neo4j (async job)
- [ ] Only book owner or admin can delete
- [ ] Deleted books not returned in list endpoints
- [ ] Hard-delete endpoint available to admins only

---

### US-LIBRARY-05: Share a Book with Team Members
**As a** content editor,
**I want to** share my book with other users,
**so that** they can search and generate video packages from it.

**Acceptance Criteria:**
- [ ] `POST /v1/books/{book_id}/permissions` grants `{user_id, permission_level: read|edit}`
- [ ] `GET /v1/books/{book_id}/permissions` lists all grants
- [ ] `DELETE /v1/books/{book_id}/permissions/{user_id}` revokes
- [ ] Shared users can see the book in their library (clearly marked as shared)
- [ ] Permission changes logged to `audit_log`

---

## EPIC 4: Knowledge Unit Review

### US-KU-01: Review Extracted Knowledge Units
**As a** content editor,
**I want to** review knowledge units extracted from books,
**so that** only high-quality, accurate facts enter the knowledge graph.

**Acceptance Criteria:**
- [ ] `GET /v1/knowledge-units?status=needs_review` returns units needing review
- [ ] Each unit shows: type, subject/predicate/object, confidence, evidence snippets with page refs
- [ ] Units with `confidence < 0.65` auto-tagged `needs_review`
- [ ] Paginated list (cursor-based, default limit: 50)
- [ ] Filter by `book_id`, `type`, `language`, `status`

---

### US-KU-02: Approve or Reject Knowledge Units
**As a** content editor,
**I want to** approve or reject individual knowledge units,
**so that** the knowledge graph only contains verified information.

**Acceptance Criteria:**
- [ ] `PATCH /v1/knowledge-units/{unit_id}` accepts `{status: approved|rejected, editor_note}`
- [ ] `GET /v1/knowledge-units/{unit_id}` fetches single unit with edit history
- [ ] Status transitions: `extracted → needs_review → approved|rejected`
- [ ] Changes recorded in `unit_edits` with `editor_user_id`
- [ ] Rejected units excluded from retrieval and generation

---

### US-KU-03: Bulk Approve / Reject Knowledge Units
**As a** content editor,
**I want to** bulk approve or reject multiple knowledge units,
**so that** I can process large books efficiently.

**Acceptance Criteria:**
- [ ] `POST /v1/knowledge-units/bulk-update` accepts `{unit_ids[], action: approve|reject, editor_note}`
- [ ] Returns `{succeeded: int, failed: int, errors: [{unit_id, reason}]}`
- [ ] Max 200 units per bulk operation
- [ ] Each update still recorded in `unit_edits`

---

### US-KU-04: Edit a Knowledge Unit
**As a** content editor,
**I want to** edit the subject, predicate, or object of a knowledge unit,
**so that** I can correct extraction errors without losing the evidence.

**Acceptance Criteria:**
- [ ] `PATCH /v1/knowledge-units/{unit_id}` accepts `{subject, predicate, object, payload, editor_note}`
- [ ] Previous values preserved in `unit_edits` (full audit trail)
- [ ] Confidence can be manually overridden by editor
- [ ] Edited units retain their evidence references

---

### US-KU-05: Detect and Resolve Conflicting Claims
**As a** content editor,
**I want to** see when two books make conflicting claims,
**so that** I can resolve contradictions in the knowledge base.

**Acceptance Criteria:**
- [ ] System detects conflicts: same `subject+predicate+object` triple with different values across books
- [ ] Conflicting units marked `status=conflicting` with shared `conflict_group_id`
- [ ] `GET /v1/knowledge-units?status=conflicting` returns conflicting pairs
- [ ] Editor can mark one as authoritative and the other as `rejected`

---

## EPIC 5: Knowledge Graph

### US-GRAPH-01: Browse Concept Nodes
**As a** content editor,
**I want to** browse the knowledge graph's concept nodes,
**so that** I can understand what concepts have been extracted.

**Acceptance Criteria:**
- [ ] `GET /v1/graph/concepts?q=&language=&limit=` returns matching concepts
- [ ] `GET /v1/graph/concepts/{canonical_key}` returns node detail: all language aliases, edge summary, evidence count
- [ ] Concepts show labels in mr/hi/en where available
- [ ] Canonical key shown for cross-lingual merging

---

### US-GRAPH-02: Explore Concept Relationships
**As a** content editor,
**I want to** traverse the relationships between concepts in the graph,
**so that** I can understand cause-effect chains and related topics.

**Acceptance Criteria:**
- [ ] `GET /v1/graph/concepts/{canonical_key}/neighbors?relation_types=&max_hops=` returns neighbor nodes
- [ ] Supports up to 4 hops
- [ ] Each edge includes: relation type, confidence, source `unit_id`
- [ ] Response includes a Mermaid diagram spec of the subgraph

---

### US-GRAPH-03: Merge Duplicate Concepts
**As a** content editor,
**I want to** merge two concept nodes that refer to the same thing,
**so that** the graph is deduplicated and cross-lingual aliases are unified.

**Acceptance Criteria:**
- [ ] `POST /v1/graph/concepts/{key_a}/merge/{key_b}` merges key_b into key_a
- [ ] All aliases from key_b added to key_a
- [ ] All edges re-pointed from key_b to key_a
- [ ] All knowledge units referencing key_b updated to key_a
- [ ] Merge action logged to `audit_log`
- [ ] Only admins and editors can merge

---

## EPIC 6: Hybrid Search

### US-SEARCH-01: Search the Knowledge Base
**As a** content creator,
**I want to** search my knowledge base with a query in any language,
**so that** I find relevant evidence even across language boundaries.

**Acceptance Criteria:**
- [ ] `POST /v1/search` accepts `{query, query_language, top_k, filters, graph}`
- [ ] Query in Marathi retrieves matching English and Hindi chunks
- [ ] Vector search uses multilingual embeddings (BGE-M3)
- [ ] Optional graph enhancement: resolves canonical concept, expands outline beats
- [ ] Each result includes: text preview, score, chunk type, language, page refs, citations
- [ ] p95 latency < 500ms for standard queries

---

### US-SEARCH-02: Filter Search by Book, Language, or Content Type
**As a** content creator,
**I want to** filter search results,
**so that** I only see relevant content for my current project.

**Acceptance Criteria:**
- [ ] Filters: `book_ids[]`, `chunk_types[]`, `languages[]`, `page_min`, `page_max`, `tags[]`
- [ ] Multiple filters combined with AND logic
- [ ] Filter combinations tested in integration tests
- [ ] Empty filter → search across all accessible books

---

### US-SEARCH-03: View Graph-Augmented Outline
**As a** content creator,
**I want to** see a knowledge graph-derived outline for my search topic,
**so that** I can see the full conceptual landscape before generating content.

**Acceptance Criteria:**
- [ ] When `graph.enable=true`, response includes `graph_plan.beats[]`
- [ ] Each beat has: `beat_id`, `title`, `intent`, `related_concepts[]`
- [ ] Beats represent: definition, why-it-matters, process, cause-effect, example, recap
- [ ] Beats ranked by: evidence count, confidence avg, audience relevance

---

## EPIC 7: Video Package Generation

### US-GEN-01: Generate a Video Package
**As a** content creator,
**I want to** generate a complete video package for a topic,
**so that** I have a ready-to-use script, storyboard, and visual spec.

**Acceptance Criteria:**
- [ ] `POST /v1/video-packages:generate` accepts `{topic, format, audience_level, language_mode, tone}`
- [ ] Returns complete package: `outline_md`, `script_md`, `storyboard`, `visual_spec`, `citations_report`, `evidence_map`
- [ ] All claims in script have at least one evidence reference
- [ ] Paragraphs without evidence: labeled `[Interpretation]` (default) or removed (configurable)
- [ ] Warnings list auto-repair actions taken

---

### US-GEN-02: Choose Video Format and Tone
**As a** content creator,
**I want to** select the format and tone of the generated video,
**so that** the output matches my content strategy.

**Acceptance Criteria:**
- [ ] Formats: `shorts` (60-90s, 5-8 scenes), `explainer` (4-6 min), `deep_dive` (10+ min)
- [ ] Tones: `teacher`, `storyteller`, `myth_buster`, `step_by_step`
- [ ] Audience levels: `beginner` (simple vocabulary, more analogies), `intermediate` (technical terms)
- [ ] Scene count within format constraints
- [ ] Template selection via `template_id` (optional; uses format default if omitted)

---

### US-GEN-03: Generate in Marathi, Hindi, or English
**As a** content creator,
**I want to** generate video scripts in Marathi, Hindi, or English (or mixed),
**so that** I can create content for my target audience.

**Acceptance Criteria:**
- [ ] `language_mode` options: `mr`, `hi`, `en`, `hinglish`, `mr_plus_en_terms`, `hi_plus_en_terms`
- [ ] `mr_plus_en_terms`: Marathi sentences, English for scientific/technical nouns
- [ ] `hinglish`: Hindi grammar + English technical terms
- [ ] Post-generation validation: detect actual output language, flag deviations
- [ ] Voiceover text in storyboard matches chosen language mode

---

### US-GEN-04: View Generated Script with Clickable Evidence
**As a** content creator,
**I want to** click any paragraph in the generated script and see its source evidence,
**so that** I can verify every claim before using it.

**Acceptance Criteria:**
- [ ] `GET /v1/video-packages/{video_id}` returns full package including `evidence_map`
- [ ] `GET /v1/evidence-map/{video_id}` returns `paragraphs[]` each with `script_text` + `evidence_refs[]`
- [ ] `GET /v1/evidence/{chunk_id}` returns full citation with snippet, page range, chapter, book title
- [ ] UI: clicking paragraph highlights evidence in right panel (split-view)
- [ ] URL hash `#para-{paragraph_id}` deep-links to specific paragraph

---

### US-GEN-05: View and Download Storyboard
**As a** content creator,
**I want to** view and export the storyboard with visual specs,
**so that** I can hand it off to my video production team.

**Acceptance Criteria:**
- [ ] Each storyboard scene includes: `duration_sec`, `on_screen_text`, `voiceover`, `visual_description`, `animation_cues`, `diagram_refs`, `evidence_refs`
- [ ] Visual spec includes Mermaid diagrams (flow, concept_map, cycle, comparison_table, process_steps)
- [ ] `GET /v1/video-packages/{video_id}/export?format=json` returns raw JSON
- [ ] `GET /v1/video-packages/{video_id}/export?format=zip` returns async export job; signed URL returned
- [ ] Icon suggestions list included in visual spec

---

### US-GEN-06: Manage Video Package Versions
**As a** content creator,
**I want to** regenerate a video package and keep previous versions,
**so that** I can compare outputs and revert if needed.

**Acceptance Criteria:**
- [ ] Each generation creates a new version (auto-incremented integer)
- [ ] `GET /v1/video-packages/{video_id}/versions` lists all versions
- [ ] `GET /v1/video-packages/{video_id}/versions/{n}` retrieves specific version
- [ ] Current version pointer updated on new generation
- [ ] Previous versions retained with their original evidence maps

---

### US-GEN-07: Apply Generation to Specific Books Only
**As a** content creator,
**I want to** restrict video generation to specific books in my library,
**so that** the content is sourced only from authoritative texts I've curated.

**Acceptance Criteria:**
- [ ] `source_filters.book_ids[]` in generation request restricts evidence retrieval
- [ ] `source_filters.prefer_languages[]` weights retrieval toward specific languages
- [ ] 422 returned if all specified books have no relevant content for the topic
- [ ] `citations_report` shows which books contributed evidence

---

## EPIC 8: Template Management

### US-TMPL-01: Browse Available Templates
**As a** content creator,
**I want to** see all available video generation templates,
**so that** I can choose the right structure for my content.

**Acceptance Criteria:**
- [ ] `GET /v1/templates` returns list of templates with: name, format, audience_level, scene_range
- [ ] System templates pre-seeded: `shorts_60s`, `explainer_5min`, `myth_buster`, `step_by_step`
- [ ] Custom templates visible to their creator and admins

---

### US-TMPL-02: Create a Custom Template
**As an** admin,
**I want to** create custom generation templates,
**so that** my team uses consistent video structures aligned to our brand.

**Acceptance Criteria:**
- [ ] `POST /v1/templates` accepts `{name, format, required_sections[], scene_min, scene_max, pacing_constraints, output_schema}`
- [ ] Admin-only endpoint
- [ ] Template validated against schema before saving
- [ ] Created template immediately available for generation

---

## EPIC 9: Webhooks & Notifications

### US-WEBHOOK-01: Register a Webhook
**As a** developer,
**I want to** register a webhook for job completion events,
**so that** my downstream systems are notified automatically.

**Acceptance Criteria:**
- [ ] `POST /v1/webhooks` accepts `{url, events[], label}`
- [ ] Supported events: `job.completed`, `job.failed`, `ku_review.ready`, `video_package.created`
- [ ] Webhook deliveries signed with HMAC-SHA256 in `X-Signature` header
- [ ] Failed deliveries retried with exponential backoff (3 attempts)
- [ ] `GET /v1/webhooks` lists registered webhooks; `DELETE /v1/webhooks/{id}` removes them

---

## EPIC 10: Analytics & Quality

### US-ANALYTICS-01: Monitor Citation Coverage
**As a** content editor,
**I want to** see the citation coverage rate across my video packages,
**so that** I can ensure our content meets the 100% citation standard.

**Acceptance Criteria:**
- [ ] `GET /v1/analytics/books/{book_id}/coverage` returns: chunk count, units extracted, units approved, citation coverage %
- [ ] Dashboard shows trend over time (last 30 days)
- [ ] Alert if citation coverage drops below 95% in strict mode

---

### US-ANALYTICS-02: Track LLM Usage and Costs
**As an** admin,
**I want to** track LLM token usage and estimated costs by operation,
**so that** I can manage API costs and set budgets.

**Acceptance Criteria:**
- [ ] `GET /v1/analytics/llm-usage?from=&to=&group_by=operation|book|user` returns usage breakdown
- [ ] Tracks: `embedding`, `extraction`, `generation`, `repair` operation types
- [ ] Estimated cost in USD per operation
- [ ] Per-user monthly token budget configurable; 429 returned if exceeded

---

### US-ANALYTICS-03: Quality Sampling for Knowledge Units
**As a** content editor,
**I want to** perform random sampling audits on extracted knowledge units,
**so that** I can measure extraction precision.

**Acceptance Criteria:**
- [ ] QA Sampling UI: presents one unit at a time from random sample
- [ ] Reviewer marks: `correct`, `incorrect`, `partially_correct`
- [ ] Results stored in `qa_samples` table
- [ ] `GET /v1/analytics/unit-precision?book_id=&from=` returns precision score
- [ ] Sampling session tracks who reviewed what (no duplicate reviews)

---

## EPIC 11: Platform Health & Operations

### US-OPS-01: Health Check Endpoints
**As an** infrastructure engineer,
**I want** health check endpoints on the API,
**so that** container orchestrators know when the service is ready and alive.

**Acceptance Criteria:**
- [ ] `GET /health` returns `{status: ok}` (no auth required)
- [ ] `GET /health/ready` checks connectivity to postgres, redis, qdrant, neo4j; returns per-service status
- [ ] `GET /health/live` returns liveness (no downstream checks)
- [ ] Ready returns 503 if any critical dependency is down
- [ ] Used in `docker-compose.yml` `healthcheck` and K8s readiness probes

---

### US-OPS-02: Structured Logging
**As a** DevOps engineer,
**I want** all services to emit structured JSON logs with correlation IDs,
**so that** I can trace issues across distributed components.

**Acceptance Criteria:**
- [ ] All logs include: `timestamp`, `level`, `service`, `trace_id`, `span_id`, `job_id` (if applicable), `book_id` (if applicable), `user_id` (if applicable), `event`, `duration_ms`
- [ ] Logs output as JSON to stdout
- [ ] Correlation ID propagated from HTTP request → Celery task headers
- [ ] Error logs include stack trace in `extra.exception`

---

### US-OPS-03: Rate Limiting
**As a** platform engineer,
**I want** API rate limits enforced per user,
**so that** no single user can overload the system.

**Acceptance Criteria:**
- [ ] `POST /generate/*`: 10 requests/hour per user
- [ ] `POST /search`: 60 requests/minute per user
- [ ] `POST /books/*/ingest`: 5 requests/hour per user
- [ ] 429 response includes `Retry-After` header
- [ ] Rate limit counters stored in Redis
- [ ] Admin users exempt from rate limits

---

## EPIC 12: Creator Studio UI

### US-UI-01: Book Library Screen
**As a** content editor,
**I want to** see all my books in a searchable library with status indicators,
**so that** I can manage my knowledge base visually.

**Acceptance Criteria:**
- [ ] Grid/list view of books with: cover image (if available), title, author, language badge, ingestion status, chunk count
- [ ] Filter panel: language, status, tags
- [ ] Search bar filters by title/author
- [ ] Click book → goes to Book Detail screen
- [ ] Upload button → triggers upload flow with drag-and-drop

---

### US-UI-02: Book Detail Screen
**As a** content editor,
**I want to** inspect the chapter structure and chunk statistics of a book,
**so that** I understand what knowledge was extracted.

**Acceptance Criteria:**
- [ ] Chapter tree on left, chunk stats on right
- [ ] Stats: total chunks by type (concept/process/evidence/general), language distribution
- [ ] OCR confidence bar chart per chapter
- [ ] "Structure confidence" badge per chapter
- [ ] Ingestion job history tab (stages, durations, errors)

---

### US-UI-03: Knowledge Review Screen
**As a** content editor,
**I want to** review, edit, and approve knowledge units in a table view,
**so that** I can efficiently curate the knowledge graph.

**Acceptance Criteria:**
- [ ] Table of units: type badge, subject→predicate→object, confidence bar, status badge, evidence snippet
- [ ] Inline edit for subject/predicate/object
- [ ] Approve/reject buttons per row
- [ ] Bulk select + bulk action toolbar
- [ ] Filter by: status, type, language, book
- [ ] Click evidence snippet → opens evidence detail panel

---

### US-UI-04: Generate Screen
**As a** content creator,
**I want** a simple form to configure and generate a video package,
**so that** I can create content without knowing the API details.

**Acceptance Criteria:**
- [ ] Fields: topic (text), format (dropdown), audience level (radio), language mode (dropdown), tone (dropdown)
- [ ] Optional: template selection, source book filter, scene count range
- [ ] Validation before submit (required fields, topic min length)
- [ ] Loading state with stage progress indicator during generation
- [ ] Auto-redirect to Output Review screen when complete

---

### US-UI-05: Output Review Screen
**As a** content creator,
**I want to** review the generated script and storyboard with inline evidence,
**so that** I can verify quality before using the content.

**Acceptance Criteria:**
- [ ] Split view: script paragraphs (left) + evidence panel (right)
- [ ] Click paragraph → highlights evidence snippets, shows page numbers, book title
- [ ] Storyboard tab: scene cards with voiceover, visual description, duration
- [ ] Visual Spec tab: Mermaid diagrams rendered inline
- [ ] Citations Report tab: full source list with page refs
- [ ] Download button: export as JSON or ZIP
- [ ] "Regenerate" button → creates new version

---

## EPIC 13: Security & Compliance

### US-SEC-01: Audit Trail for Sensitive Actions
**As an** admin,
**I want** all sensitive actions logged to an immutable audit trail,
**so that** I can investigate incidents and demonstrate compliance.

**Acceptance Criteria:**
- [ ] All state-mutating operations write to `audit_log`
- [ ] Logged: `user_id`, `action`, `resource_type`, `resource_id`, `ip_address`, `user_agent`, `payload_json`, `created_at`
- [ ] Application DB user has no DELETE privilege on `audit_log`
- [ ] `GET /v1/admin/audit-log` available to admins with filtering

---

### US-SEC-02: Prompt Injection Protection
**As a** security engineer,
**I want** all user-supplied text sanitized before being passed to the LLM,
**so that** malicious users cannot extract private data or subvert generation.

**Acceptance Criteria:**
- [ ] `sanitize_for_llm(text)` applied to: `topic`, `query`, `editor_note`, all free-text inputs
- [ ] Strips instruction-like prefixes (e.g., "Ignore previous instructions")
- [ ] Max length enforced before LLM submission (2000 chars for queries, 500 for topic)
- [ ] Flagged inputs logged to `audit_log` with `action=prompt_injection_detected`
- [ ] Unit tests for known injection patterns

---

## EPIC 14: Testing & Evaluation

### US-TEST-01: Golden Query Regression Tests
**As a** developer,
**I want** a golden query test suite that runs in CI,
**so that** I detect retrieval quality regressions before deployment.

**Acceptance Criteria:**
- [ ] Golden queries defined in `tests/golden_queries/golden_queries.jsonl`
- [ ] Each entry: `{query, language, expected_chunk_ids[], expected_book_ids[], min_citation_coverage}`
- [ ] `scripts/eval_run.py` runs all golden queries and computes recall@k, precision@k
- [ ] CI fails if recall@k drops > 5% from baseline
- [ ] Results stored in `eval_results/` with timestamps
- [ ] At least 20 golden queries covering: English, Marathi, Hindi, and cross-lingual cases

---

### US-TEST-02: Multilingual Test Fixtures
**As a** developer,
**I want** multilingual test fixture books,
**so that** language detection and cross-lingual retrieval are properly tested.

**Acceptance Criteria:**
- [ ] `tests/fixtures/sample_marathi_agri.pdf` — ~10 pages Marathi agricultural content
- [ ] `tests/fixtures/sample_hindi_text.pdf` — ~10 pages Hindi content
- [ ] `tests/fixtures/sample_mixed_script.pdf` — mixed Marathi+English content
- [ ] Fixture factory in `tests/conftest.py` for unit tests (no real OCR required)
- [ ] All fixture files documented with source and license in `tests/fixtures/README.md`

---

## Story Count Summary

| Epic | Stories | Status |
|------|---------|--------|
| EPIC 1: Auth & Users | 5 | Phase 0 (pre-MVP) |
| EPIC 2: Book Ingestion | 7 | Phase 1 (MVP) |
| EPIC 3: Library Management | 5 | Phase 1 |
| EPIC 4: Knowledge Unit Review | 5 | Phase 2 |
| EPIC 5: Knowledge Graph | 3 | Phase 2 |
| EPIC 6: Hybrid Search | 3 | Phase 1 |
| EPIC 7: Video Generation | 7 | Phase 1-2 |
| EPIC 8: Template Management | 2 | Phase 2 |
| EPIC 9: Webhooks | 1 | Phase 2 |
| EPIC 10: Analytics | 3 | Phase 3 |
| EPIC 11: Platform Ops | 3 | Phase 0 (pre-MVP) |
| EPIC 12: Creator Studio UI | 5 | Phase 3 |
| EPIC 13: Security | 2 | Phase 0 |
| EPIC 14: Testing | 2 | Ongoing |
| **Total** | **53** | |
