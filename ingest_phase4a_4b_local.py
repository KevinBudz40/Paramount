# ingest_phase4a_4b_local.py
# [2026-08-21] Fresh rebuild ingest — Phase 4a + Phase 4b + local_docs only
# [2026-08-22] Qdrant migration: bypass Flask entirely — chunk + embed + write directly to Qdrant.
# Flask is NOT needed for ingest. Memory stays lean because each file is processed and released.

import gc
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, r"C:\ParamountRAG\scripts")
sys.path.insert(0, r"C:\GIT\rag-system-qdrant")

import ingest_batched
ingest_batched.WATCH_ROOTS = [
    Path(r"C:\Users\kevin\Paramount\Paramount Oracle Assessment - ERP Phase 4a October 2026 Go-Live"),
    Path(r"C:\Users\kevin\Paramount\Paramount Oracle Assessment - ERP Phase 4b January 2027 Go-Live"),
    Path(r"C:\Paramount\local_docs"),
]

from ingest_batched import collect_new_files
from source_log import init_db, upsert, remove, file_md5, lookup_by_path
from rag.chunker import chunk_document
from rag.store import add_chunks

QDRANT_DIR  = r"C:\GIT\qdrant_db"
LABEL_SCRIPT = Path(r"C:\ParamountRAG\scripts\label_chunks.py")
PYTHON      = sys.executable
BATCH_SIZE  = 25  # log progress every N files; no Flask restart needed


if __name__ == "__main__":
    init_db()
    files = collect_new_files()
    total = len(files)
    print(f"Files to ingest: {total}")

    ingested = updated = errors = skipped = 0

    for idx, fpath in enumerate(files, 1):
        full_path = str(fpath.resolve())
        try:
            current_hash = file_md5(fpath)
            existing = lookup_by_path(full_path)

            # Build source key (same format ingest_todo uses: relative-ish filename)
            source_key = fpath.name

            # Chunk the file
            chunks = chunk_document(fpath)
            if not chunks:
                print(f"  SKIP (empty)  {fpath.name}")
                skipped += 1
                continue

            # Build doc_meta
            doc_meta = {
                "doc_type":   fpath.suffix.lstrip(".").lower(),
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }

            # Write directly to Qdrant — no Flask, no HTTP
            count = add_chunks(chunks, doc_meta=doc_meta, persist_dir=QDRANT_DIR)

            # Update source_log
            upsert(
                source_key=source_key,
                full_path=full_path,
                content_hash=current_hash,
                doc_type=fpath.suffix.lstrip(".").lower(),
                file_size=fpath.stat().st_size,
                chunk_count=count,
            )

            action = "UPDATE" if existing else "NEW"
            print(f"  [{idx}/{total}] {action}  {count:>4} chunks  {fpath.name}")
            if existing:
                updated += 1
            else:
                ingested += 1

        except Exception as exc:
            print(f"  [{idx}/{total}] ERR  {fpath.name} — {exc}")
            errors += 1

        # Explicit GC every BATCH_SIZE files to keep memory flat
        if idx % BATCH_SIZE == 0:
            gc.collect()
            print(f"  --- {idx}/{total} files done ---")

    print(f"\nIngest complete: {ingested} new, {updated} updated, {skipped} skipped, {errors} errors")

    # [2026-08-23] Force GC before label_chunks — Qdrant embedded lock not released until client is GC'd
    gc.collect()
    print("\nRunning label_chunks --apply ...")
    subprocess.run([PYTHON, str(LABEL_SCRIPT), "--apply"], check=False)

    print("\nDone.")
