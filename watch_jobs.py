import asyncio, asyncpg, sys, datetime

DB = 'postgresql://cgr_user:changeme_required@localhost:5432/creator_graphrag'

async def check():
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch("""
        SELECT b.title, j.stage, ROUND(j.progress::numeric,3) AS progress, j.status, j.updated_at
        FROM ingestion_jobs j
        LEFT JOIN books b ON b.book_id = j.book_id
        WHERE j.updated_at > NOW() - INTERVAL '10 hours'
          AND j.status IN ('running','completed')
        ORDER BY j.updated_at DESC
        LIMIT 20
    """)
    total_ku = await conn.fetchval('SELECT COUNT(*) FROM knowledge_units')
    await conn.close()

    running = [r for r in rows if r['status'] == 'running']
    completed = [r for r in rows if r['status'] == 'completed']

    now = datetime.datetime.now().strftime('%H:%M:%S')
    sys.stdout.buffer.write(f'\n=== {now} | KUs: {total_ku} | Running: {len(running)} | Done: {len(completed)} ===\n'.encode())
    for r in rows:
        line = f"  {r['status']:10} {float(r['progress'])*100:5.1f}%  {r['stage']:15}  {(r['title'] or '?')[:50]}\n"
        sys.stdout.buffer.write(line.encode('utf-8', errors='replace'))
    sys.stdout.flush()
    return len(running)

async def main():
    while True:
        try:
            running = await check()
            if running == 0:
                sys.stdout.buffer.write(b'\n*** ALL JOBS COMPLETE ***\n')
                sys.stdout.flush()
                break
        except Exception as e:
            sys.stdout.buffer.write(f'Error: {e}\n'.encode())
            sys.stdout.flush()
        await asyncio.sleep(300)  # check every 5 minutes

asyncio.run(main())
