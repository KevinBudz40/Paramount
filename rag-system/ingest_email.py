"""
ingest_email.py — Ingest Exchange Online emails into ChromaDB via Microsoft Graph.

Usage:
    python ingest_email.py                        # last 200 inbox emails
    python ingest_email.py --folder sentitems     # sent items
    python ingest_email.py --top 500              # fetch more messages
    python ingest_email.py --since 2026-01-01     # only emails on/after this date
    python ingest_email.py --folder inbox --folder sentitems  # multiple folders
"""
import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from rag.chunker import _chunk_text
from rag.graph.mail import fetch_messages
from rag.store import add_chunks, checkpoint, doc_count

_DEFAULT_CHROMA = str(_SCRIPT_DIR / "chroma_db")
_BATCH = 500


def ingest_emails(folder: str, top: int, since: str | None, chroma_dir: str) -> tuple[int, int]:
    filter_query = None
    if since:
        filter_query = f"receivedDateTime ge {since}T00:00:00Z"

    print(f"  Fetching up to {top} messages from '{folder}'" + (f" since {since}" if since else "") + "...")
    messages = fetch_messages(folder=folder, top=top, filter_query=filter_query)
    print(f"  Retrieved {len(messages)} messages")

    total_chunks = 0
    processed = 0

    for i, msg in enumerate(messages, 1):
        text = msg.get("text", "").strip()
        source = msg.get("source", f"email:unknown:{i}")
        doc_meta = msg.get("doc_meta", {})

        if not text:
            continue

        chunks = [
            {"text": t, "source": source, "chunk_index": idx}
            for idx, t in enumerate(_chunk_text(text))
        ]
        if not chunks:
            continue

        for batch_start in range(0, len(chunks), _BATCH):
            add_chunks(chunks[batch_start:batch_start + _BATCH], doc_meta=doc_meta, persist_dir=chroma_dir)

        total_chunks += len(chunks)
        processed += 1
        date = doc_meta.get("date", "")
        subject = doc_meta.get("topic", "")[:70]
        print(f"  [{processed}] {date}  {subject}  -> {len(chunks)} chunk(s)")

    checkpoint(chroma_dir)
    return processed, total_chunks


def main():
    parser = argparse.ArgumentParser(description="Ingest Exchange emails into ChromaDB")
    parser.add_argument("--folder", action="append", default=[], metavar="FOLDER",
                        help="Mail folder name (inbox, sentitems, …). Repeatable. Default: inbox")
    parser.add_argument("--top",    type=int, default=200,
                        help="Max emails per folder (default 200)")
    parser.add_argument("--since",  default=None,
                        help="Only ingest emails on/after this date, e.g. 2026-01-01")
    parser.add_argument("--chroma", default=_DEFAULT_CHROMA,
                        help="ChromaDB directory (default: ./chroma_db)")
    args = parser.parse_args()

    folders = args.folder or ["inbox"]

    print(f"\nIngesting emails from Microsoft Graph")
    print(f"  Folders : {', '.join(folders)}")
    print(f"  ChromaDB: {args.chroma}\n")

    before = doc_count(args.chroma)
    grand_processed = 0
    grand_chunks = 0

    for folder in folders:
        print(f"\n--- Folder: {folder} ---")
        processed, chunks = ingest_emails(folder, args.top, args.since, args.chroma)
        grand_processed += processed
        grand_chunks += chunks

    after = doc_count(args.chroma)
    print(f"\nDone.")
    print(f"  Emails processed : {grand_processed}")
    print(f"  Chunks added     : {grand_chunks}")
    print(f"  Total DB chunks  : {after}  (was {before})")


if __name__ == "__main__":
    main()
