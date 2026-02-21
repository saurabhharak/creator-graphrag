"""Backup PostgreSQL and Neo4j databases.

Creates timestamped DB dumps in the data/backups directory.
For PostgreSQL, it uses docker exec to run pg_dump.
For Neo4j (Community Edition), it temporarily stops the container to run
neo4j-admin database dump via a transient container, then restarts it.
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    # Use repo root for consistent paths
    project_root = Path(__file__).resolve().parent.parent
    backup_dir = project_root / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    
    # ── PostgreSQL Backup ─────────────────────────────────────────────────────
    print("🗄️ Backing up PostgreSQL...")
    pg_file = backup_dir / f"pg-creator_graphrag-{timestamp}.dump"
    
    # Using 'docker exec' ensures we don't need pg_dump installed locally.
    # Format 'c' (custom) is compressed and suitable for pg_restore.
    cmd_pg = [
        "docker", "exec", "creator-graphrag-postgres-1",
        "pg_dump", "-U", "cgr_user", "-F", "c", "-d", "creator_graphrag"
    ]
    
    try:
        with open(pg_file, "wb") as f:
            proc = subprocess.run(cmd_pg, stdout=f)
            if proc.returncode != 0:
                print(f"  ❌ PostgreSQL backup failed (return code {proc.returncode})", file=sys.stderr)
            else:
                size_mb = pg_file.stat().st_size / (1024 * 1024)
                print(f"  ✅ Saved: {pg_file.name} ({size_mb:.2f} MB)")
    except Exception as e:
        print(f"  ❌ Error running PostgreSQL backup: {e}", file=sys.stderr)


    # ── Neo4j Backup ──────────────────────────────────────────────────────────
    print("\n🕸️ Backing up Neo4j...")
    neo4j_container = "neo4j-dev"
    neo4j_file = f"neo4j-{timestamp}.dump"
    
    try:
        # Check if container exists before proceeding
        proc = subprocess.run(["docker", "ps", "-a", "--filter", f"name={neo4j_container}", "--format", "{{.Names}}"], 
                              capture_output=True, text=True)
        if neo4j_container not in proc.stdout:
            print(f"  ⚠️ Container '{neo4j_container}' not found. Skipping Neo4j backup.")
            return

        print(f"  Stopping '{neo4j_container}' for offline dump...")
        subprocess.run(["docker", "stop", neo4j_container], check=True, stdout=subprocess.DEVNULL)
        
        print("  Dumping Neo4j database...")
        # On Windows Docker, path mounts need to use absolute paths.
        abs_backup_dir = backup_dir.absolute().as_posix()
        cmd_neo4j = [
            "docker", "run", "--rm",
            "--volumes-from", neo4j_container,
            "-v", f"{abs_backup_dir}:/backups",
            "neo4j:5-community",
            "neo4j-admin", "database", "dump", "neo4j", "--to-path=/backups"
        ]
        
        proc = subprocess.run(cmd_neo4j)
        if proc.returncode != 0:
             print(f"  ❌ Neo4j backup failed (return code {proc.returncode})", file=sys.stderr)
        else:
             # It defaults to creating neo4j.dump
             default_dump = backup_dir / "neo4j.dump"
             final_path = backup_dir / neo4j_file
             if default_dump.exists():
                 default_dump.rename(final_path)
             size_mb = final_path.stat().st_size / (1024 * 1024) if final_path.exists() else 0
             print(f"  ✅ Saved: {neo4j_file} ({size_mb:.2f} MB)")

    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error running Neo4j backup: {e}", file=sys.stderr)
    finally:
        # ALWAYS restart the container
        print(f"  Restarting '{neo4j_container}'...")
        subprocess.run(["docker", "start", neo4j_container], check=True, stdout=subprocess.DEVNULL)


    print("\n🎉 Backup process completed!")
    print(f"Check the backup directory: {backup_dir.relative_to(project_root)}")


if __name__ == "__main__":
    main()
