"""
Initialize Qdrant collections for the Creator GraphRAG system.

Qdrant Collection Spec (GAP-DATA-08):
  collection_name: chunks_multilingual
  vector_size:     1024 (BGE-M3) or 3072 (text-embedding-3-large)
  distance:        Cosine
  hnsw_config:     m=16, ef_construct=200
  payload_indexes: book_id (keyword), chunk_type (keyword), language_detected (keyword)

Run:
  python scripts/init_qdrant.py
"""
from __future__ import annotations
import os
import sys

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    PayloadSchemaType,
    VectorParams,
)

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "chunks_multilingual")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")
VECTOR_SIZE = 1024 if EMBEDDING_MODEL == "bge-m3" else 3072


def init_qdrant():
    client = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        api_key=QDRANT_API_KEY,
    )

    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        print(f"Collection '{COLLECTION_NAME}' already exists. Skipping creation.")
    else:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
            hnsw_config=HnswConfigDiff(
                m=16,
                ef_construct=200,
            ),
        )
        print(f"Created collection '{COLLECTION_NAME}' (dim={VECTOR_SIZE}, Cosine)")

    # Create payload indexes for efficient filtering
    payload_indexes = {
        "book_id": PayloadSchemaType.KEYWORD,
        "chunk_type": PayloadSchemaType.KEYWORD,
        "language_detected": PayloadSchemaType.KEYWORD,
        "chapter_id": PayloadSchemaType.KEYWORD,
    }
    for field_name, schema_type in payload_indexes.items():
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field_name,
            field_schema=schema_type,
        )
        print(f"  Payload index created: {field_name} ({schema_type.value})")

    print("\nQdrant initialization complete.")
    client.close()


if __name__ == "__main__":
    init_qdrant()
