"""Quick check: knowledge units in PostgreSQL."""
import asyncio
import asyncpg


async def main():
    conn = await asyncpg.connect(
        "postgresql://cgr_user:changeme_required@localhost:5432/creator_graphrag"
    )
    try:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM knowledge_units WHERE deleted_at IS NULL"
        )
        print(f"Knowledge units in DB: {row['cnt']}")

        rows = await conn.fetch(
            "SELECT type, COUNT(*) as cnt FROM knowledge_units "
            "WHERE deleted_at IS NULL GROUP BY type ORDER BY cnt DESC"
        )
        print("Type distribution:")
        for r in rows:
            print(f"  {r['type']}: {r['cnt']}")

        rows = await conn.fetch(
            "SELECT status, COUNT(*) as cnt FROM knowledge_units "
            "WHERE deleted_at IS NULL GROUP BY status ORDER BY cnt DESC"
        )
        print("Status distribution:")
        for r in rows:
            print(f"  {r['status']}: {r['cnt']}")

        # Sample units
        rows = await conn.fetch(
            "SELECT subject, predicate, object, type, confidence "
            "FROM knowledge_units WHERE deleted_at IS NULL "
            "LIMIT 5"
        )
        print("\nSample knowledge units:")
        for r in rows:
            print(f"  [{r['type']}] {r['subject']} --{r['predicate']}--> {r['object']}  (conf={r['confidence']})")

    finally:
        await conn.close()


asyncio.run(main())
