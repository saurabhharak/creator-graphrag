"""
Ingestion Pipeline Orchestrator.

Stages (with weights for progress calculation):
  upload           0.02
  ocr              0.35  <- heaviest for scanned books
  structure_extract 0.10
  chunk            0.10
  embed            0.20
  unit_extract     0.15
  graph_build      0.08

Two source paths are supported:
  "pdf"                   - standard PDF (Tesseract OCR or Azure Vision)
  "pre_extracted_sarvam"  - document.md + metadata/ already produced by
                            scripts/sarvam_extract.py; OCR stage is skipped.

Each stage must be:
  - Idempotent (safe to retry from any point)
  - Checkpoint-aware (progress stored in Postgres after each page/chunk batch)
  - Observable (publishes to Redis pub/sub for SSE streaming)
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import structlog

import app.infrastructure.neo4j_client as worker_neo4j
from app.core.config import worker_settings
from app.infrastructure.db import (
    format_error,
    insert_knowledge_units,
    log_llm_usage,
    update_job_status,
)
from app.infrastructure.qdrant_client import (
    build_point,
    ensure_collection,
    get_client,
    upsert_points,
)
from app.pipelines.chunker import TextChunk, chunk_document
from app.pipelines.embedder import embed_batch
from app.pipelines.graph_builder import build_graph_for_units
from app.pipelines.unit_extractor import extract_units_for_chunk

logger = structlog.get_logger(__name__)

STAGE_WEIGHTS = {
    "upload": 0.02,
    "ocr": 0.35,
    "structure_extract": 0.10,
    "chunk": 0.10,
    "embed": 0.20,
    "unit_extract": 0.15,
    "graph_build": 0.08,
}


@dataclass
class IngestionConfig:
    force_ocr: bool = False
    ocr_languages: list[str] = field(default_factory=lambda: ["hin", "mar", "eng"])
    max_chars: int = 2000
    overlap_chars: int = 250
    extract_knowledge_units: bool = True
    build_graph: bool = True
    # Source path control
    source_format: str = "pdf"               # "pdf" | "pre_extracted_sarvam"
    pre_extracted_dir: str | None = None     # absolute path to the extracted folder


class IngestionPipeline:
    """
    Orchestrates the full book ingestion pipeline.

    Usage:
        pipeline = IngestionPipeline(job_id, book_id, config)
        await pipeline.run()
    """

    def __init__(self, job_id: UUID, book_id: UUID, config: IngestionConfig):
        self.job_id = job_id
        self.book_id = book_id
        self.config = config
        self._db_url = worker_settings.DATABASE_URL
        self._qdrant = get_client(
            host=worker_settings.QDRANT_HOST,
            port=worker_settings.QDRANT_PORT,
        )

    async def run(self) -> None:
        """Execute all pipeline stages in sequence."""
        if self.config.source_format == "pre_extracted_sarvam":
            await self._run_pre_extracted()
        else:
            await self._run_pdf()

    # ── Pre-extracted Sarvam path ──────────────────────────────────────────────

    async def _run_pre_extracted(self) -> None:
        """Pipeline path for books already extracted by sarvam_extract.py.

        Skips: download, OCR.
        Runs:  chunk, embed, upsert.
        Stubs: structure_extract, unit_extract, graph_build.
        """
        if not self.config.pre_extracted_dir:
            raise ValueError("pre_extracted_dir must be set for pre_extracted_sarvam source")

        extracted_dir = Path(self.config.pre_extracted_dir)
        doc_md_path = extracted_dir / "document.md"
        if not doc_md_path.exists():
            raise FileNotFoundError(f"document.md not found at {doc_md_path}")

        await self._update_stage("upload", 1.0)           # already done
        await self._update_stage("ocr", 1.0)              # skipped

        await self._update_stage("structure_extract", 0.0)
        # TODO(#2): parse metadata/*.json for chapter structure
        chapters: list = []
        await self._update_stage("structure_extract", 1.0)

        # ── Chunk ──────────────────────────────────────────────────────────────
        await self._update_stage("chunk", 0.0)
        document_md = doc_md_path.read_text(encoding="utf-8")
        chunks = chunk_document(
            document_md,
            max_chars=self.config.max_chars,
            overlap_chars=self.config.overlap_chars,
        )
        logger.info("chunking_done", job_id=str(self.job_id), chunks=len(chunks))
        await self._update_stage("chunk", 1.0)

        # ── Embed + Upsert ─────────────────────────────────────────────────────
        await self._update_stage("embed", 0.0)
        await self._embed_and_upsert(chunks)
        await self._update_stage("embed", 1.0)

        # Knowledge unit extraction and graph build — stubs for Phase 2
        await self._update_stage("unit_extract", 0.0)
        await self._update_stage("unit_extract", 1.0)
        await self._update_stage("graph_build", 0.0)
        await self._update_stage("graph_build", 1.0)

        await self._mark_done(metrics={"chunks_created": len(chunks)})

    # ── Standard PDF path (stubs — OCR not yet implemented) ───────────────────

    async def _run_pdf(self) -> None:
        """Standard PDF pipeline — OCR path (TODO stubs)."""
        await self._stage_download()
        file_type = await self._detect_source_type()
        pages = await self._stage_ocr(file_type)
        chapters = await self._stage_structure_extract(pages)
        chunks = await self._stage_chunk(pages, chapters)
        await self._stage_embed(chunks)
        if self.config.extract_knowledge_units:
            units = await self._stage_unit_extract(chunks)
            if self.config.build_graph:
                await self._stage_graph_build(units)
        await self._mark_done(metrics={"chunks_created": len(chunks)})

    # ── Shared embed + upsert helper ───────────────────────────────────────────

    async def _embed_and_upsert(self, chunks: list[TextChunk]) -> None:
        """Embed chunks with Ollama and upsert to Qdrant."""
        ensure_collection(
            self._qdrant,
            worker_settings.QDRANT_COLLECTION_NAME,
            dim=worker_settings.EMBEDDING_DIMENSION,
        )

        texts = [c.text for c in chunks]
        embed_results = embed_batch(
            texts,
            endpoint=worker_settings.OLLAMA_ENDPOINT,
            model=worker_settings.EMBEDDING_MODEL,
        )

        book_id_str = str(self.book_id)
        points = []
        for chunk, emb in zip(chunks, embed_results):
            # Deterministic point ID: UUID5(book_id, text_hash)
            point_id = str(uuid.uuid5(uuid.UUID(book_id_str), chunk.text_hash))
            points.append(
                build_point(
                    point_id=point_id,
                    vector=emb.vector,
                    book_id=book_id_str,
                    chunk_type=chunk.chunk_type,
                    language_detected=chunk.language_detected,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section_title=chunk.section_title,
                    text_hash=chunk.text_hash,
                    embedding_model_id=emb.model_id,
                )
            )

        upsert_points(self._qdrant, worker_settings.QDRANT_COLLECTION_NAME, points)
        logger.info("embed_upsert_done", job_id=str(self.job_id), points=len(points))

    # ── Standard PDF stage stubs ───────────────────────────────────────────────

    async def _detect_source_type(self) -> str:
        """
        Determine if PDF is text-based or scanned.

        Algorithm:
        - Extract text via pdfplumber
        - If text < 100 chars for >30% of pages -> 'pdf_scanned'
        - Mixed: classify per page, OCR only sparse pages
        - EPUB: detected by MIME type
        """
        # TODO(#0): implement detection logic
        return "pdf_text"

    async def _stage_download(self) -> None:
        """Download raw file from S3 to worker temp directory."""
        await self._update_stage("upload", 0.0)
        # TODO(#0): boto3 download
        await self._update_stage("upload", 1.0)

    async def _stage_ocr(self, file_type: str) -> list:
        """
        OCR stage for scanned PDFs and EPUBs.

        PDF scanned -> Tesseract with '-l hin+mar+eng'
        EPUB -> ebooklib + BeautifulSoup for HTML body extraction
        Mixed -> per-page: OCR only pages with text < threshold

        Fallback: if average OCR confidence < 0.60, log warning +
                  optionally retry with Azure Vision.
        """
        await self._update_stage("ocr", 0.0)
        # TODO(#0): implement OCR logic with adapter pattern
        pages = []
        await self._update_stage("ocr", 1.0)
        return pages

    async def _stage_structure_extract(self, pages: list) -> list:
        """
        Extract chapter/section structure from pages.

        Strategy:
        1. Use ToC if available (PDFMiner bookmark extraction)
        2. Heading heuristics fallback (font size, bold, line patterns)
        3. Store confidence per chapter
        """
        await self._update_stage("structure_extract", 0.0)
        chapters = []
        await self._update_stage("structure_extract", 1.0)
        return chapters

    async def _stage_chunk(self, pages: list, chapters: list) -> list:
        """
        Split pages into chunks with provenance metadata.

        Chunk types: concept | process | evidence | general
        Dedup: text_hash per (book_id, text_hash) unique
        Language detection: fastText/CLD3 + Devanagari disambiguation
        """
        await self._update_stage("chunk", 0.0)
        chunks: list = []
        await self._update_stage("chunk", 1.0)
        return chunks

    async def _stage_embed(self, chunks: list) -> None:
        """
        Generate multilingual embeddings and upsert to Qdrant.

        Model: Qwen3-Embedding-8B via Ollama (1024-dim, Cosine)
        Stores embedding_model_id per chunk for future re-embedding
        """
        await self._update_stage("embed", 0.0)
        if chunks:
            await self._embed_and_upsert(chunks)
        await self._update_stage("embed", 1.0)

    async def _stage_unit_extract(self, chunks: list[TextChunk]) -> list[dict]:
        """Extract knowledge units from chunks using gpt-4o.

        Skips silently if OPENAI_API_KEY is not set or
        extract_knowledge_units=False in config. Gracefully degrades per
        chunk (LLM failures return empty list for that chunk).
        """
        if not self.config.extract_knowledge_units or not worker_settings.OPENAI_API_KEY:
            logger.info(
                "unit_extract_skipped",
                job_id=str(self.job_id),
                reason="disabled_or_no_api_key",
            )
            await self._update_stage("unit_extract", 1.0)
            return []

        await self._update_stage("unit_extract", 0.0)
        all_units: list[dict] = []
        total = max(len(chunks), 1)

        for i, chunk in enumerate(chunks):
            unit_dicts, in_tok, out_tok = await extract_units_for_chunk(
                chunk,
                book_id=str(self.book_id),
                book_title="",  # book title not cached in pipeline; prompt uses ""
                chapter_title=chunk.section_title or "",
                openai_api_key=worker_settings.OPENAI_API_KEY,
                openai_base_url=worker_settings.OPENAI_BASE_URL,
                extraction_model=worker_settings.LLM_EXTRACTION_MODEL,
            )
            all_units.extend(unit_dicts)

            if in_tok > 0:
                await log_llm_usage(
                    self._db_url,
                    operation_type="extraction",
                    model_id=worker_settings.LLM_EXTRACTION_MODEL,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    book_id=str(self.book_id),
                    job_id=str(self.job_id),
                )

            await self._update_stage("unit_extract", (i + 1) / total)

        if all_units:
            await insert_knowledge_units(self._db_url, all_units)
        logger.info(
            "unit_extract_done",
            job_id=str(self.job_id),
            units=len(all_units),
            needs_review=sum(1 for u in all_units if u.get("status") == "needs_review"),
        )
        return all_units

    async def _stage_graph_build(self, units: list[dict]) -> None:
        """MERGE knowledge units into Neo4j as Concept nodes + relationships.

        Skips if build_graph=False or no units were extracted.
        Runs synchronously in a thread pool to avoid blocking the event loop.
        """
        if not self.config.build_graph or not units:
            logger.info(
                "graph_build_skipped",
                job_id=str(self.job_id),
                reason="disabled_or_no_units",
            )
            await self._update_stage("graph_build", 1.0)
            return

        await self._update_stage("graph_build", 0.0)
        driver = worker_neo4j.get_driver(
            uri=worker_settings.NEO4J_URI,
            user=worker_settings.NEO4J_USER,
            password=worker_settings.NEO4J_PASSWORD,
        )
        try:
            merged = await asyncio.to_thread(build_graph_for_units, driver, units)
            logger.info(
                "graph_build_done",
                job_id=str(self.job_id),
                nodes_merged=merged,
            )
        finally:
            await asyncio.to_thread(worker_neo4j.close_driver, driver)

        await self._update_stage("graph_build", 1.0)

    # ── Progress tracking ──────────────────────────────────────────────────────

    async def _update_stage(self, stage: str, stage_progress: float) -> None:
        """Update job progress in Postgres.

        Global progress = sum(completed_stage_weights) + current_stage_weight * stage_progress
        """
        stage_keys = list(STAGE_WEIGHTS.keys())
        current_idx = stage_keys.index(stage) if stage in stage_keys else 0
        global_progress = (
            sum(w for s, w in STAGE_WEIGHTS.items() if stage_keys.index(s) < current_idx)
            + STAGE_WEIGHTS.get(stage, 0) * stage_progress
        )
        global_progress = round(global_progress, 3)

        await update_job_status(
            self._db_url,
            str(self.job_id),
            status="running",
            stage=stage,
            progress=global_progress,
        )
        logger.info(
            "stage_progress",
            job_id=str(self.job_id),
            stage=stage,
            stage_progress=stage_progress,
            global_progress=global_progress,
        )

    async def _mark_done(self, metrics: dict | None = None) -> None:
        """Mark job as completed in Postgres with optional quality metrics."""
        await update_job_status(
            self._db_url,
            str(self.job_id),
            status="completed",
            stage="done",
            progress=1.0,
            message="Ingestion complete",
            metrics_json=metrics,
        )
        logger.info("ingestion_complete", job_id=str(self.job_id))
