"""
ingest_folder.py — Bulk folder ingestion into the RAG knowledge base.

Usage:
    python ingest_folder.py "C:\\Paramount\\10-1"
    python ingest_folder.py "C:\\Paramount\\10-1" --chroma ./chroma_db --log ingest_log.txt

Safe to stop and restart — already-ingested files are tracked in ingest_tracker.db
and will never be re-processed.

MD5 dedup: files whose content is identical to an already-ingested file are skipped
even when they live in a different folder, preventing duplicate chunks in the KB.
"""
import argparse
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

# ── Resolve project root so rag.* imports work regardless of cwd ──────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from rag.chunker import _chunk_text, _extract_zip, load_document
from rag.store import add_chunks, doc_count

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_CHROMA = str(_SCRIPT_DIR.parent / "chroma_db")
_BACKOFF_DELAYS = [30, 60, 120]   # seconds between retries on rate-limit errors
_INTER_FILE_DELAY = 1.0            # seconds between embedding calls
_MIN_FILE_BYTES = 1024             # skip files smaller than 1 KB

# Explicit allowlist — ingestible file types
_ALLOWED = {".pdf", ".docx", ".xlsx", ".csv", ".pptx", ".txt", ".md", ".json",
            ".svg", ".vtt"}

# Directory name fragments that cause a whole subtree to be skipped
_SKIP_DIR_FRAGMENTS = {"node_modules", ".git", "__pycache__", ".venv"}

# Absolute path prefixes to exclude entirely (case-insensitive on Windows)
_SKIP_PATH_PREFIXES: list[str] = []

# Specific filenames (stem only, case-insensitive) to always skip
_SKIP_FILENAMES = {"r598 - cash detail report.xlsx"}


# ── SQLite tracker ────────────────────────────────────────────────────────────

def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _open_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingested (
            path        TEXT PRIMARY KEY,
            status      TEXT,
            chunks      INTEGER,
            ingested_at TEXT,
            error       TEXT,
            md5_hash    TEXT
        )
    """)
    # Add md5_hash column to existing DBs that pre-date this change
    try:
        con.execute("ALTER TABLE ingested ADD COLUMN md5_hash TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    con.commit()
    return con


def _is_done(con: sqlite3.Connection, path: str) -> bool:
    row = con.execute(
        "SELECT status FROM ingested WHERE path = ?", (path,)
    ).fetchone()
    return row is not None and row[0] == "ok"


def _is_duplicate(con: sqlite3.Connection, md5: str) -> str | None:
    """Return the path of an already-ingested file with the same MD5, or None."""
    row = con.execute(
        "SELECT path FROM ingested WHERE md5_hash = ? AND status = 'ok' LIMIT 1", (md5,)
    ).fetchone()
    return row[0] if row else None


def _record(con: sqlite3.Connection, path: str, status: str,
            chunks: int = 0, error: str = "", md5: str = "") -> None:
    con.execute(
        """INSERT INTO ingested (path, status, chunks, ingested_at, error, md5_hash)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(path) DO UPDATE SET
               status=excluded.status, chunks=excluded.chunks,
               ingested_at=excluded.ingested_at, error=excluded.error,
               md5_hash=excluded.md5_hash""",
        (path, status, chunks, datetime.now().isoformat(timespec="seconds"), error, md5),
    )
    con.commit()


# ── File collection ───────────────────────────────────────────────────────────

def _collect_files(root: Path) -> list[Path]:
    """
    Recursively collect all ingestible files under root.
    Zip archives are expanded into a temp directory and their contents collected;
    the temp dir is cleaned up when this function returns (zips are NOT themselves
    added to the list — their extracted files are).
    """
    results: list[Path] = []
    _walk(root, results, temp_roots=[])
    return results


def _walk(folder: Path, out: list[Path], temp_roots: list[Path]) -> None:
    for entry in sorted(folder.rglob("*")):
        if not entry.is_file():
            continue

        parts_lower = [p.lower() for p in entry.parts]

        # Skip macOS / Windows metadata noise
        if any(p.startswith("__macosx") or p.startswith(".") for p in parts_lower):
            continue

        # Skip subtrees we never want
        if any(frag in parts_lower for frag in _SKIP_DIR_FRAGMENTS):
            continue

        # Skip explicitly excluded path prefixes
        entry_lower = str(entry).lower()
        if any(entry_lower.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
            continue

        suffix = entry.suffix.lower()

        if suffix == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="rag_ingest_zip_"))
            temp_roots.append(tmp)
            try:
                _extract_zip(entry, tmp)
                _walk(tmp, out, temp_roots)
            except Exception as exc:
                print(f"  [WARN] Could not expand {entry.name}: {exc}")
            continue

        if suffix not in _ALLOWED:
            continue

        if entry.name.lower() in _SKIP_FILENAMES:
            continue

        if entry.stat().st_size < _MIN_FILE_BYTES:
            continue

        out.append(entry)


# ── Ingestion ─────────────────────────────────────────────────────────────────

def _ingest_file(
    path: Path,
    source_label: str,
    chroma_dir: str,
    retries: int = 3,
) -> int:
    """
    Load, chunk, and embed one file. Returns number of chunks stored.
    Retries with exponential backoff on rate-limit-like errors.
    """
    _BATCH_SIZE = 500  # stay well under ChromaDB's 5461 limit

    text = load_document(path)
    if not text or not text.strip():
        raise ValueError("empty content")

    chunks_text = _chunk_text(text)
    chunks = [
        {"text": t, "source": source_label, "chunk_index": i}
        for i, t in enumerate(chunks_text)
    ]

    total = 0
    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start: batch_start + _BATCH_SIZE]
        for attempt in range(retries + 1):
            try:
                add_chunks(batch, doc_meta={}, persist_dir=chroma_dir)
                total += len(batch)
                break
            except Exception as exc:
                msg = str(exc).lower()
                is_rate_limit = any(k in msg for k in ("rate", "429", "too many", "quota"))
                if is_rate_limit and attempt < retries:
                    delay = _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)]
                    print(f"  [RATE LIMIT] waiting {delay}s before retry {attempt + 1}/{retries}…")
                    time.sleep(delay)
                else:
                    raise

    return total


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk ingest a folder into the RAG KB.")
    parser.add_argument("folder", help="Root folder to ingest")
    parser.add_argument("--chroma", default=_DEFAULT_CHROMA,
                        help=f"ChromaDB directory (default: {_DEFAULT_CHROMA})")
    parser.add_argument("--db", default="ingest_tracker.db",
                        help="SQLite tracker database (default: ingest_tracker.db)")
    parser.add_argument("--log", default="ingest_log.txt",
                        help="Log file (default: ingest_log.txt)")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Re-process all files with status=error in the tracker DB")
    args = parser.parse_args()

    root = Path(args.folder).resolve()
    if not root.exists():
        print(f"ERROR: folder not found: {root}")
        sys.exit(1)

    print(f"\nRAG Folder Ingestion")
    print(f"  Source : {root}")
    print(f"  ChromaDB: {args.chroma}")
    print(f"  Tracker : {args.db}")
    print(f"  Log     : {args.log}")
    if args.retry_errors:
        print(f"  Mode    : retry errors")
    print()

    # ── Open tracker + log ─────────────────────────────────────────────────────
    con = _open_db(args.db)
    log = open(args.log, "a", encoding="utf-8")

    def _log(line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        log.write(f"[{ts}] {line}\n")
        log.flush()

    # ── Collect files ──────────────────────────────────────────────────────────
    if args.retry_errors:
        # Build a map of source_label -> actual path from the scanned files,
        # then filter to only those marked error in the tracker.
        print("Scanning for files to build path map…")
        temp_roots: list[Path] = []
        all_scanned: list[Path] = []
        _walk(root, all_scanned, temp_roots)

        # Build label -> path lookup
        label_to_path: dict[str, Path] = {}
        for fpath in all_scanned:
            try:
                label = fpath.relative_to(root).as_posix()
            except ValueError:
                label = fpath.name
            label_to_path[label] = fpath

        # Fetch error entries from tracker
        rows = con.execute(
            "SELECT path FROM ingested WHERE status = 'error'"
        ).fetchall()
        error_labels = [r[0] for r in rows]

        all_files = []
        missing = []
        for label in error_labels:
            if label in label_to_path:
                all_files.append(label_to_path[label])
            else:
                missing.append(label)

        if missing:
            print(f"  {len(missing)} error entries have no matching file on disk (skipped)")
        total = len(all_files)
        print(f"Found {total} error files to retry.\n")
    else:
        print("Scanning for files…")
        temp_roots = []
        all_files = []
        _walk(root, all_files, temp_roots)
        total = len(all_files)
        print(f"Found {total} ingestible files.\n")

    if total == 0:
        print("Nothing to ingest.")
        return

    _log(f"=== Session start: {root} ({total} files) retry={args.retry_errors} ===")

    # ── Process ────────────────────────────────────────────────────────────────
    ingested = skipped_done = skipped_empty = skipped_dupe = errors = 0

    for idx, fpath in enumerate(all_files, 1):
        # Use path relative to root as the source label (preserves folder context)
        try:
            source_label = fpath.relative_to(root).as_posix()
        except ValueError:
            # File is inside an extracted zip temp dir — use filename only
            source_label = fpath.name

        print(f"[{idx}/{total}] {source_label}", end="", flush=True)

        # Skip already done (same path)
        if _is_done(con, source_label):
            print("  -> already ingested, skipping")
            _log(f"SKIP (done) {source_label}")
            skipped_done += 1
            continue

        # MD5 dedup — skip files whose content already exists under a different path
        file_md5 = _md5(fpath)
        existing = _is_duplicate(con, file_md5)
        if existing:
            print(f"  -> duplicate of '{existing}', skipping")
            _log(f"SKIP (dupe of {existing}) {source_label}")
            _record(con, source_label, "duplicate", md5=file_md5)
            skipped_dupe += 1
            continue

        try:
            n = _ingest_file(fpath, source_label, args.chroma)
            if n == 0:
                print("  -> empty, skipped")
                _log(f"SKIP (empty) {source_label}")
                _record(con, source_label, "empty", md5=file_md5)
                skipped_empty += 1
            else:
                print(f"  -> {n} chunks")
                _log(f"OK {n} chunks  {source_label}")
                _record(con, source_label, "ok", chunks=n, md5=file_md5)
                ingested += 1
                time.sleep(_INTER_FILE_DELAY)

        except KeyboardInterrupt:
            print("\n\nInterrupted — progress saved. Re-run to continue.")
            _log("=== Interrupted by user ===")
            break

        except Exception as exc:
            err = str(exc)
            print(f"  -> ERROR: {err}")
            _log(f"ERROR {source_label}  {err}")
            _record(con, source_label, "error", error=err)
            errors += 1

    # ── Summary ────────────────────────────────────────────────────────────────
    total_kb = doc_count(args.chroma)
    summary = (
        f"\nDone.\n"
        f"  Ingested : {ingested}\n"
        f"  Skipped  : {skipped_done} already done, {skipped_dupe} duplicates, {skipped_empty} empty\n"
        f"  Errors   : {errors}\n"
        f"  KB total : {total_kb} chunks\n"
    )
    print(summary)
    _log(f"=== Session end — ingested={ingested} skipped={skipped_done} errors={errors} total_kb={total_kb} ===")

    log.close()
    con.close()

    # Clean up any zip temp dirs created during file collection
    for tmp in temp_roots:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
