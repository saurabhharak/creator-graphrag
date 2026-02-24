#!/usr/bin/env python3
"""Fix book ownership: reassign the 3 canonical books to the real user.

Canonical books (from Qdrant):
  5d3f6232-ce05-57b8-ac89-9ecff1df68ce  Introduction to Natural Farming  (en, 391 chunks)
  00272ec0-ce39-5d32-a12a-fbb87b3c5591  An agricultural testament         (en, 466 chunks)
  2dedee82-7755-5b3b-a695-6fe32c28acc2  आपले हात जगन्नाथ                  (mr, 116 chunks)

Actions:
  1. Update Natural Farming's created_by → real user
  2. INSERT Agricultural Testament (was missing from DB)
  3. INSERT Marathi book            (was missing from DB)
  4. Soft-delete all other test books

Usage:
    python scripts/fix_book_ownership.py [user_email]
    python scripts/fix_book_ownership.py saurabhharak2020@gmail.com
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import asyncpg

DATABASE_URL = (
    os.environ.get(
        "DATABASE_URL",
        "postgresql://cgr_user:changeme@localhost:5432/creator_graphrag",
    ).replace("postgresql+asyncpg://", "postgresql://")
)

# 3 canonical books that have real Qdrant data
CANONICAL_BOOKS = [
    {
        "book_id": "5d3f6232-ce05-57b8-ac89-9ecff1df68ce",
        "title": "Introduction to Natural Farming",
        "language_primary": "en",
        "author": "Masanobu Fukuoka",
        "tags": ["agriculture", "natural-farming"],
        "chunk_count": 391,
    },
    {
        "book_id": "00272ec0-ce39-5d32-a12a-fbb87b3c5591",
        "title": "An agricultural testament",
        "language_primary": "en",
        "author": "Sir Albert Howard",
        "tags": ["agriculture", "organic"],
        "chunk_count": 466,
    },
    {
        "book_id": "2dedee82-7755-5b3b-a695-6fe32c28acc2",
        "title": "\u0906\u092a\u0932\u0947 \u0939\u093e\u0924 \u091c\u0917\u0928\u094d\u0928\u093e\u0925",
        "language_primary": "mr",
        "author": None,
        "tags": ["agriculture", "marathi"],
        "chunk_count": 116,
    },
]


async def main(user_email: str) -> None:
    conn: asyncpg.Connection = await asyncpg.connect(DATABASE_URL)

    # Resolve user
    user = await conn.fetchrow(
        "SELECT user_id, email FROM users WHERE email = $1", user_email
    )
    if not user:
        print(f"ERROR: User '{user_email}' not found in database.")
        await conn.close()
        return

    real_user_id = str(user["user_id"])
    print(f"Target user: {user_email} → {real_user_id}")
    print()

    canonical_ids = [b["book_id"] for b in CANONICAL_BOOKS]

    # --- Step 1+2+3: Ensure each canonical book exists and belongs to real user ---
    for book in CANONICAL_BOOKS:
        existing = await conn.fetchrow(
            "SELECT book_id, created_by FROM books WHERE book_id = $1::uuid",
            book["book_id"],
        )
        if existing:
            if str(existing["created_by"]) != real_user_id:
                await conn.execute(
                    "UPDATE books SET created_by = $1::uuid, updated_at = NOW() WHERE book_id = $2::uuid",
                    real_user_id,
                    book["book_id"],
                )
                print(f"  updated  {book['title'][:45]} → owner reassigned")
            else:
                print(f"  ok       {book['title'][:45]} already owned by real user")
        else:
            # Insert missing book
            import json
            await conn.execute(
                """
                INSERT INTO books (
                    book_id, created_by, title, author,
                    language_primary, tags, visibility, usage_rights,
                    created_at, updated_at
                ) VALUES (
                    $1::uuid, $2::uuid, $3, $4,
                    $5, $6::jsonb, 'private', 'all_rights_reserved',
                    NOW(), NOW()
                )
                """,
                book["book_id"],
                real_user_id,
                book["title"],
                book["author"],
                book["language_primary"],
                json.dumps(book["tags"]),
            )
            print(f"  inserted {book['title'][:45]}")

    print()

    # --- Step 4: Soft-delete all other test books ---
    deleted = await conn.fetchval(
        """
        UPDATE books
        SET deleted_at = NOW()
        WHERE deleted_at IS NULL
          AND book_id NOT IN (
              SELECT unnest($1::uuid[])
          )
        RETURNING COUNT(*)
        """,
        canonical_ids,
    )
    # The above uses aggregate RETURNING which needs a subquery
    # Use a simpler approach:
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM books WHERE deleted_at IS NULL AND book_id != ALL($1::uuid[])",
        canonical_ids,
    )
    if count > 0:
        await conn.execute(
            "UPDATE books SET deleted_at = NOW() WHERE deleted_at IS NULL AND book_id != ALL($1::uuid[])",
            canonical_ids,
        )
        print(f"  soft-deleted {count} test/duplicate books")
    else:
        print("  no duplicate books to clean up")

    print()
    # --- Summary ---
    final = await conn.fetch(
        "SELECT book_id, title, language_primary FROM books WHERE deleted_at IS NULL ORDER BY title"
    )
    print("Final active books:")
    for b in final:
        print(f"  {b['book_id']} | {b['language_primary']:3s} | {b['title']}")

    await conn.close()
    print("\nDone.")


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "saurabhharak2020@gmail.com"
    asyncio.run(main(email))
