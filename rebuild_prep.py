"""
rebuild_prep.py — Prepare ChromaDB for a full rebuild.

Steps:
  1. Rename chroma_db -> chroma_db_pre_rebuild (safety net)
  2. Create fresh chroma_db directory
  3. Copy source_log.db into new chroma_db (preserves skip_list)
  4. DELETE FROM source_log in the copy (clear ingest history, keep skip_list)

Run with Flask DOWN. Do NOT run while Flask is active.
"""
# [2026-08-18] Written for full ChromaDB rebuild — preserves skip_list, wipes ingest history

import sqlite3
import shutil
import sys
from pathlib import Path

CHROMA_DB   = Path(r"C:\GIT\chroma_db")
PRE_REBUILD = Path(r"C:\GIT\chroma_db_pre_rebuild")
SOURCE_LOG  = "source_log.db"

def main():
    # Safety: abort if Flask is running
    import subprocess
    result = subprocess.run(
        ["powershell", "-Command",
         "Get-Process python -ErrorAction SilentlyContinue | Where-Object { "
         "(Get-WmiObject Win32_Process -Filter \"ProcessId=$($_.Id)\" -ErrorAction SilentlyContinue)"
         ".CommandLine -like '*app.py*' } | Measure-Object | Select-Object -ExpandProperty Count"],
        capture_output=True, text=True
    )
    if result.stdout.strip() not in ("", "0"):
        print("ERROR: Flask is running. Stop Flask before running rebuild_prep.py.")
        sys.exit(1)

    # Step 1: rename existing chroma_db
    if PRE_REBUILD.exists():
        print(f"ERROR: {PRE_REBUILD} already exists. Remove or rename it first.")
        sys.exit(1)
    if not CHROMA_DB.exists():
        print(f"ERROR: {CHROMA_DB} not found.")
        sys.exit(1)

    print(f"Step 1: Renaming {CHROMA_DB} -> {PRE_REBUILD} ...")
    CHROMA_DB.rename(PRE_REBUILD)
    print("  Done.")

    # Step 2: create fresh chroma_db
    print(f"Step 2: Creating fresh {CHROMA_DB} ...")
    CHROMA_DB.mkdir()
    print("  Done.")

    # Step 3: copy source_log.db
    old_db = PRE_REBUILD / SOURCE_LOG
    new_db = CHROMA_DB / SOURCE_LOG
    if not old_db.exists():
        print(f"  [WARN] No source_log.db found in old chroma_db — skip_list will be empty.")
    else:
        print(f"Step 3: Copying source_log.db into new chroma_db ...")
        shutil.copy2(old_db, new_db)
        print("  Done.")

        # Step 4: clear ingest history, keep skip_list
        print(f"Step 4: Clearing source_log ingest records (preserving skip_list) ...")
        conn = sqlite3.connect(new_db)
        before = conn.execute("SELECT COUNT(*) FROM source_log").fetchone()[0]
        skips  = conn.execute("SELECT COUNT(*) FROM skip_list").fetchone()[0]
        conn.execute("DELETE FROM source_log")
        conn.commit()
        conn.close()
        print(f"  Cleared {before:,} ingest records.")
        print(f"  Skip list preserved: {skips:,} entries.")

    print()
    print("Rebuild prep complete.")
    print(f"  Old DB:  {PRE_REBUILD}  (safety net — do not delete until rebuild is verified)")
    print(f"  New DB:  {CHROMA_DB}  (empty, ready for ingest)")
    print()
    print("Next steps:")
    print("  1. Start Flask")
    print("  2. Run ingests in priority order")
    print("  3. Stop Flask, run label_chunks.py")
    print("  4. Restart Flask and verify chunk count")
    print("  5. Take a backup")

if __name__ == "__main__":
    main()
