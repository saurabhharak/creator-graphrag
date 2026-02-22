"""
Database Manager — Backup & Restore for Creator GraphRAG.

Handles PostgreSQL, Qdrant, and Neo4j (rebuilt from Postgres).

Usage:
    python scripts/db_manager.py backup     Take fresh backups (overwrites latest)
    python scripts/db_manager.py restore    Restore empty DBs from latest backups
    python scripts/db_manager.py status     Show what data exists in each DB

Run from project root:
    python scripts/db_manager.py backup
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ── Config (read from .env or defaults) ──────────────────────────────────────
def _env(key: str, default: str = "") -> str:
    """Read from .env file or environment."""
    val = os.environ.get(key)
    if val:
        return val
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return default


PG_CONTAINER = "creator-graphrag-postgres-1"
PG_USER = _env("POSTGRES_USER", "cgr_user")
PG_DB = _env("POSTGRES_DB", "creator_graphrag")
PG_PASSWORD = _env("POSTGRES_PASSWORD", "changeme_required")

QDRANT_HOST = _env("QDRANT_HOST", "localhost")
QDRANT_PORT = _env("QDRANT_PORT", "6333")
QDRANT_COLLECTION = _env("QDRANT_COLLECTION_NAME", "chunks_multilingual")

NEO4J_CONTAINER = "creator-graphrag-neo4j-1"
NEO4J_URI = _env("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = _env("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _env("NEO4J_PASSWORD", "changeme_dev")

# ── File naming ──────────────────────────────────────────────────────────────
PG_LATEST = BACKUP_DIR / f"pg-{PG_DB}-latest.dump"
QDRANT_LATEST = BACKUP_DIR / f"qdrant-{QDRANT_COLLECTION}-latest.snapshot"


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS — Check what data exists
# ═══════════════════════════════════════════════════════════════════════════════
def check_postgres() -> dict:
    """Check Postgres for data. Returns {ok, tables, users, books, kus}."""
    try:
        result = subprocess.run(
            ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-t", "-A", "-c",
             "SELECT count(*) FROM users; SELECT count(*) FROM books; SELECT count(*) FROM knowledge_units;"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip()[:200]}
        counts = [int(x.strip()) for x in result.stdout.strip().split("\n") if x.strip().isdigit()]
        return {"ok": True, "users": counts[0] if len(counts) > 0 else 0,
                "books": counts[1] if len(counts) > 1 else 0,
                "kus": counts[2] if len(counts) > 2 else 0}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def check_qdrant() -> dict:
    """Check Qdrant for data. Returns {ok, points}."""
    try:
        import urllib.request
        url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{QDRANT_COLLECTION}"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        points = data.get("result", {}).get("points_count", 0)
        return {"ok": True, "points": points}
    except Exception as e:
        err = str(e)
        if "404" in err or "Not Found" in err:
            return {"ok": True, "points": 0}
        return {"ok": False, "error": err[:200]}


def check_neo4j() -> dict:
    """Check Neo4j for data. Returns {ok, nodes, rels}."""
    try:
        result = subprocess.run(
            ["docker", "exec", NEO4J_CONTAINER, "cypher-shell",
             "-u", NEO4J_USER, "-p", NEO4J_PASSWORD,
             "MATCH (n) RETURN count(n) AS c"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip()[:200]}
        lines = result.stdout.strip().split("\n")
        nodes = int(lines[-1].strip()) if len(lines) >= 2 else 0

        result2 = subprocess.run(
            ["docker", "exec", NEO4J_CONTAINER, "cypher-shell",
             "-u", NEO4J_USER, "-p", NEO4J_PASSWORD,
             "MATCH ()-[r]->() RETURN count(r) AS c"],
            capture_output=True, text=True, timeout=15,
        )
        rels = 0
        if result2.returncode == 0:
            lines2 = result2.stdout.strip().split("\n")
            rels = int(lines2[-1].strip()) if len(lines2) >= 2 else 0

        return {"ok": True, "nodes": nodes, "rels": rels}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def show_status():
    """Print the current state of all databases."""
    print("\n  Database Status:")
    print("  " + "-" * 50)

    pg = check_postgres()
    if pg["ok"]:
        print(f"  PostgreSQL:  {pg.get('users', 0)} users, {pg.get('books', 0)} books, {pg.get('kus', 0)} knowledge units")
    else:
        print(f"  PostgreSQL:  ERROR - {pg.get('error', 'unknown')}")

    qd = check_qdrant()
    if qd["ok"]:
        print(f"  Qdrant:      {qd.get('points', 0)} vector points")
    else:
        print(f"  Qdrant:      ERROR - {qd.get('error', 'unknown')}")

    n4j = check_neo4j()
    if n4j["ok"]:
        print(f"  Neo4j:       {n4j.get('nodes', 0)} nodes, {n4j.get('rels', 0)} relationships")
    else:
        print(f"  Neo4j:       ERROR - {n4j.get('error', 'unknown')}")

    print("  " + "-" * 50)

    # Backup files
    print("\n  Backup Files:")
    if PG_LATEST.exists():
        size = PG_LATEST.stat().st_size / 1024
        print(f"  PostgreSQL:  {PG_LATEST.name} ({size:.0f} KB)")
    else:
        print("  PostgreSQL:  No backup found")

    if QDRANT_LATEST.exists():
        size = QDRANT_LATEST.stat().st_size / (1024 * 1024)
        print(f"  Qdrant:      {QDRANT_LATEST.name} ({size:.1f} MB)")
    else:
        print("  Qdrant:      No backup found")

    print(f"  Neo4j:       (rebuilt from PostgreSQL knowledge_units)")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKUP
# ═══════════════════════════════════════════════════════════════════════════════
def backup_postgres() -> bool:
    """Dump PostgreSQL to latest.dump (overwrites)."""
    print("  [pg] Dumping PostgreSQL...")
    tmp_path = "/tmp/pg_backup.dump"
    result = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "pg_dump",
         "-U", PG_USER, "-d", PG_DB, "-Fc", "-f", tmp_path],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"  [pg] ERROR: {result.stderr.strip()[:200]}")
        return False

    # Copy out of container
    result2 = subprocess.run(
        ["docker", "cp", f"{PG_CONTAINER}:{tmp_path}", str(PG_LATEST)],
        capture_output=True, text=True, timeout=30,
    )
    if result2.returncode != 0:
        print(f"  [pg] ERROR copying: {result2.stderr.strip()[:200]}")
        return False

    size = PG_LATEST.stat().st_size / 1024
    print(f"  [pg] Saved: {PG_LATEST.name} ({size:.0f} KB)")
    return True


def backup_qdrant() -> bool:
    """Create Qdrant snapshot and download to latest.snapshot (overwrites)."""
    import urllib.request

    print("  [qd] Creating Qdrant snapshot...")
    try:
        # Create snapshot
        url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{QDRANT_COLLECTION}/snapshots"
        req = urllib.request.Request(url, method="POST", data=b"")
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        snapshot_name = data.get("result", {}).get("name")
        if not snapshot_name:
            print(f"  [qd] ERROR: No snapshot name in response: {data}")
            return False

        # Download snapshot
        print(f"  [qd] Downloading {snapshot_name}...")
        download_url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{QDRANT_COLLECTION}/snapshots/{snapshot_name}"
        urllib.request.urlretrieve(download_url, str(QDRANT_LATEST))

        size = QDRANT_LATEST.stat().st_size / (1024 * 1024)
        print(f"  [qd] Saved: {QDRANT_LATEST.name} ({size:.1f} MB)")
        return True

    except Exception as e:
        print(f"  [qd] ERROR: {e}")
        return False


def do_backup():
    """Run full backup."""
    print("\n" + "=" * 56)
    print("  BACKUP — Creator GraphRAG Databases")
    print("=" * 56)

    ok_pg = backup_postgres()
    ok_qd = backup_qdrant()

    print()
    print("  Summary:")
    print(f"    PostgreSQL: {'OK' if ok_pg else 'FAILED'}")
    print(f"    Qdrant:     {'OK' if ok_qd else 'FAILED'}")
    print(f"    Neo4j:      (no backup needed — rebuilt from Postgres)")
    print(f"    Location:   {BACKUP_DIR}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  RESTORE
# ═══════════════════════════════════════════════════════════════════════════════
def restore_postgres() -> bool:
    """Restore PostgreSQL from latest.dump."""
    if not PG_LATEST.exists():
        print("  [pg] No backup file found, skipping.")
        return False

    print(f"  [pg] Restoring from {PG_LATEST.name}...")
    # Copy into container
    result = subprocess.run(
        ["docker", "cp", str(PG_LATEST), f"{PG_CONTAINER}:/tmp/pg_restore.dump"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  [pg] ERROR copying: {result.stderr.strip()[:200]}")
        return False

    # Restore
    result2 = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "pg_restore",
         "--clean", "--if-exists", "--no-owner",
         "-U", PG_USER, "-d", PG_DB, "/tmp/pg_restore.dump"],
        capture_output=True, text=True, timeout=120,
    )
    # pg_restore returns non-zero for warnings (e.g. "table does not exist" on --clean)
    # Check if data actually loaded
    pg = check_postgres()
    if pg["ok"] and pg.get("users", 0) > 0:
        print(f"  [pg] Restored: {pg['users']} users, {pg['books']} books, {pg['kus']} knowledge units")
        return True
    else:
        print(f"  [pg] WARNING: Restore may have issues. Status: {pg}")
        return False


def restore_qdrant() -> bool:
    """Restore Qdrant from latest.snapshot."""
    if not QDRANT_LATEST.exists():
        print("  [qd] No backup file found, skipping.")
        return False

    print(f"  [qd] Restoring from {QDRANT_LATEST.name}...")
    try:
        # Use curl.exe for multipart upload (Python urllib can't do multipart easily)
        result = subprocess.run(
            ["curl.exe", "-s", "-X", "POST",
             f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{QDRANT_COLLECTION}/snapshots/upload",
             "-H", "Content-Type: multipart/form-data",
             "-F", f"snapshot=@{QDRANT_LATEST}"],
            capture_output=True, text=True, timeout=120,
        )
        resp = json.loads(result.stdout) if result.stdout else {}
        if resp.get("status") == "ok":
            qd = check_qdrant()
            print(f"  [qd] Restored: {qd.get('points', '?')} vector points")
            return True
        else:
            print(f"  [qd] ERROR: {result.stdout[:200]}")
            return False
    except Exception as e:
        print(f"  [qd] ERROR: {e}")
        return False


def rebuild_neo4j() -> bool:
    """Rebuild Neo4j graph from PostgreSQL knowledge units."""
    print("  [n4j] Rebuilding Neo4j graph from PostgreSQL knowledge units...")
    try:
        # Use the rebuild script
        rebuild_script = SCRIPT_DIR / "rebuild_neo4j_graph.py"
        if not rebuild_script.exists():
            # Fallback: direct rebuild
            print("  [n4j] rebuild_neo4j_graph.py not found, using inline rebuild...")
            return _inline_neo4j_rebuild()

        result = subprocess.run(
            [sys.executable, str(rebuild_script)],
            capture_output=True, text=True, timeout=600,
            cwd=str(PROJECT_ROOT / "apps" / "api"),
        )
        # Print last few lines of output
        lines = result.stdout.strip().split("\n")
        for line in lines[-6:]:
            if line.strip():
                print(f"  [n4j] {line.strip()}")

        n4j = check_neo4j()
        if n4j["ok"] and n4j.get("nodes", 0) > 0:
            return True
        return result.returncode == 0

    except Exception as e:
        print(f"  [n4j] ERROR: {e}")
        return False


def _inline_neo4j_rebuild() -> bool:
    """Fallback inline rebuild if script not found."""
    try:
        import neo4j as neo4j_driver
        import psycopg2
    except ImportError:
        print("  [n4j] Missing dependencies (neo4j, psycopg2). Install them first.")
        return False

    # This is a simplified version — the full logic is in rebuild_neo4j_graph.py
    print("  [n4j] (use 'python -m scripts.rebuild_neo4j_graph' from apps/api for full rebuild)")
    return False


def do_restore(auto: bool = False):
    """Check all DBs and restore what's empty."""
    if not auto:
        print("\n" + "=" * 56)
        print("  RESTORE — Creator GraphRAG Databases")
        print("=" * 56)

    restored_any = False

    # 1. Check & restore PostgreSQL
    pg = check_postgres()
    if pg["ok"] and pg.get("users", 0) > 0:
        if not auto:
            print(f"  [pg] Data exists ({pg['users']} users, {pg['books']} books). Skipping.")
    else:
        if pg["ok"]:
            print("  [pg] Database is empty. Restoring...")
        else:
            print(f"  [pg] Cannot connect ({pg.get('error', '')}). Attempting restore...")
        if restore_postgres():
            restored_any = True

    # 2. Check & restore Qdrant
    qd = check_qdrant()
    if qd["ok"] and qd.get("points", 0) > 0:
        if not auto:
            print(f"  [qd] Data exists ({qd['points']} points). Skipping.")
    else:
        if qd["ok"]:
            print("  [qd] Collection is empty. Restoring...")
        else:
            print(f"  [qd] Cannot connect ({qd.get('error', '')}). Attempting restore...")
        if restore_qdrant():
            restored_any = True

    # 3. Check & rebuild Neo4j (always from Postgres)
    n4j = check_neo4j()
    if n4j["ok"] and n4j.get("nodes", 0) > 0:
        if not auto:
            print(f"  [n4j] Data exists ({n4j['nodes']} nodes). Skipping.")
    else:
        # Only rebuild if Postgres has knowledge units
        pg_check = check_postgres()
        if pg_check["ok"] and pg_check.get("kus", 0) > 0:
            print(f"  [n4j] Graph is empty. Rebuilding from {pg_check['kus']} knowledge units...")
            if rebuild_neo4j():
                restored_any = True
        else:
            if not auto:
                print("  [n4j] No knowledge units in Postgres to rebuild from.")

    if not auto:
        print()
        if restored_any:
            print("  Restore complete!")
        else:
            print("  Nothing to restore — all databases have data.")
        print()

    return restored_any


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 2:
        print()
        print("  Creator GraphRAG — Database Manager")
        print()
        print("  Usage:")
        print("    python scripts/db_manager.py backup    Take fresh backups")
        print("    python scripts/db_manager.py restore   Restore empty DBs")
        print("    python scripts/db_manager.py status    Show DB status")
        print()
        sys.exit(0)

    command = sys.argv[1].lower()

    if command == "backup":
        do_backup()
    elif command == "restore":
        do_restore(auto=False)
    elif command == "status":
        show_status()
    elif command == "auto-restore":
        # Silent mode for start.bat — only prints if restoring
        do_restore(auto=True)
    else:
        print(f"  Unknown command: {command}")
        print("  Use: backup, restore, or status")
        sys.exit(1)


if __name__ == "__main__":
    main()
