"""
recover_source_log.py — Fix source_log / ChromaDB chunk gap.

Audit source_log against what is actually in ChromaDB and take corrective action:

  CATEGORY A — tracked in source_log, file still on disk, NOT in ChromaDB
                → clear from source_log so the next ingest_batched run picks them up

  CATEGORY B — tracked in source_log, file deleted from disk, NOT in ChromaDB
                → remove stale entry (optional, --purge-gone flag)

Run in dry-run mode first (default) to see what would change, then --execute to apply.

Usage:
  python recover_source_log.py                   # dry run — prints summary only
  python recover_source_log.py --execute         # apply Category A fixes
  python recover_source_log.py --execute --purge-gone  # also remove Category B stale entries

After running with --execute, run ingest_batched.py to re-ingest the cleared files.

[2026-08-09] Written to fix July 2026 DB-restore gap: ~3,600 sources tracked in
source_log as ingested but absent from ChromaDB after a restore wiped the vector
store without resetting source_log. Root cause: restore was done without --since-days 3.
"""

import argparse
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SOURCE_LOG  = Path(r"C:\GIT\chroma_db\source_log.db")
CHROMA_DB   = Path(r"C:\GIT\chroma_db\chroma.sqlite3")


def load_chroma_sources(chroma_path: Path) -> set[str]:
    con = sqlite3.connect(str(chroma_path))
    try:
        rows = con.execute(
            "SELECT DISTINCT string_value FROM embedding_metadata WHERE key = 'source'"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows if r[0]}


def load_source_log(sl_path: Path) -> list[dict]:
    con = sqlite3.connect(str(sl_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT source_key, full_path, doc_type, chunk_count, ingested_at FROM source_log"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def categorize(sl_rows: list[dict], ch_sources: set[str]) -> tuple[list, list, list]:
    """Return (in_chroma, cat_a, cat_b) lists."""
    in_chroma, cat_a, cat_b = [], [], []
    for r in sl_rows:
        key = r["source_key"]
        if key in ch_sources:
            in_chroma.append(r)
            continue
        path = r["full_path"]
        if path and os.path.exists(path):
            cat_a.append(r)
        else:
            cat_b.append(r)
    return in_chroma, cat_a, cat_b


def print_breakdown(label: str, rows: list[dict]) -> None:
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r.get("doc_type") or "(none)"].append(r)
    print(f"\n{label} ({len(rows):,} sources, {sum(r['chunk_count'] for r in rows):,} chunks):")
    for dt, items in sorted(by_type.items(), key=lambda x: -sum(r["chunk_count"] for r in x[1])):
        chunks = sum(r["chunk_count"] for r in items)
        print(f"  {dt:<12}  {len(items):>5} sources  {chunks:>8,} chunks")


def clear_source_log_entries(sl_path: Path, keys: list[str], label: str) -> None:
    con = sqlite3.connect(str(sl_path))
    try:
        con.executemany("DELETE FROM source_log WHERE source_key = ?", [(k,) for k in keys])
        con.commit()
    finally:
        con.close()
    print(f"  Deleted {len(keys):,} {label} entries from source_log.")


def main():
    parser = argparse.ArgumentParser(description="Recover source_log / ChromaDB gap")
    parser.add_argument("--execute",    action="store_true", help="Apply fixes (default: dry run)")
    parser.add_argument("--purge-gone", action="store_true", help="Also remove Category B stale entries (file deleted from disk)")
    args = parser.parse_args()

    print(f"ChromaDB : {CHROMA_DB}")
    print(f"source_log: {SOURCE_LOG}")
    print(f"Mode      : {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Timestamp : {datetime.now(timezone.utc).isoformat()}")
    print()

    print("Loading ChromaDB source list...")
    ch_sources = load_chroma_sources(CHROMA_DB)
    print(f"  {len(ch_sources):,} unique sources in ChromaDB")

    print("Loading source_log...")
    sl_rows = load_source_log(SOURCE_LOG)
    print(f"  {len(sl_rows):,} entries in source_log ({sum(r['chunk_count'] for r in sl_rows):,} chunks)")

    in_chroma, cat_a, cat_b = categorize(sl_rows, ch_sources)

    print_breakdown("HEALTHY (in ChromaDB)", in_chroma)
    print_breakdown("CATEGORY A — file on disk, missing from ChromaDB (will clear & re-ingest)", cat_a)
    print_breakdown("CATEGORY B — file deleted from disk, stale entry", cat_b)

    print(f"\nSummary:")
    print(f"  Healthy               : {len(in_chroma):,} sources")
    print(f"  Cat A (re-ingest)     : {len(cat_a):,} sources  {sum(r['chunk_count'] for r in cat_a):,} chunks")
    print(f"  Cat B (stale/gone)    : {len(cat_b):,} sources  {sum(r['chunk_count'] for r in cat_b):,} chunks")

    if not args.execute:
        print("\nDry run — no changes made. Re-run with --execute to apply.")
        return

    print("\nApplying fixes...")
    if cat_a:
        clear_source_log_entries(SOURCE_LOG, [r["source_key"] for r in cat_a], "Category A")

    if args.purge_gone and cat_b:
        clear_source_log_entries(SOURCE_LOG, [r["source_key"] for r in cat_b], "Category B")
    elif cat_b and not args.purge_gone:
        print(f"  Skipping Category B ({len(cat_b):,} stale entries) — re-run with --purge-gone to remove.")

    print("\nDone. Next step: run ingest_batched.py to re-ingest Category A files.")
    print("  Recommended: python C:\\ParamountRAG\\scripts\\ingest_batched.py --batch 75 --no-label")


if __name__ == "__main__":
    main()
